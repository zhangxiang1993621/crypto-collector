"""Goal.com Indonesia 体育新闻抓取 + 入库脚本

功能：从 Goal.com/id 抓取印尼语体育新闻/赛程，存入 Supabase posts 表
- 抓取首页文章列表
- 逐篇访问详情页提取标题、内容、图片
- 图片下载为 base64 内嵌
- 标题去重，避免重复入库

用法：
    python sport/goal/goal_scraper.py                  # 仅抓取打印
    python sport/goal/goal_scraper.py --save           # 抓取并直接入库
    python sport/goal/goal_scraper.py --save --max 10  # 限制篇数
"""

import os
import sys
import json
import time
import base64
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, update_one, execute_sql
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.goal.com"
ID_BASE = f"{BASE_URL}/id"
NEWS_URL = f"{ID_BASE}/berita"


# ────────────────────── 数据库工具 ──────────────────────

def lookup_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error(f"未找到作者: {username}")
        sys.exit(1)
    logger.info(f"作者: {username} (id={row['id']})")
    return row["id"]


def lookup_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME") or "Sports Talk"
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    logger.info(f"分类: {name} (id={row['id']})")
    return row["id"]


def load_existing_titles() -> set[str]:
    rows = select_all("posts", {}, columns="title")
    titles = {r["title"] for r in rows}
    logger.info(f"数据库中已有 {len(titles)} 条帖子")
    return titles


def download_image_as_base64(img_url: str) -> dict | None:
    """下载图片并转为 base64 data URI"""
    if not img_url:
        return None
    try:
        r = httpx.get(img_url, timeout=30)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "image/jpeg")
        b64 = base64.b64encode(r.content).decode()
        return {"url": f"data:{content_type};base64,{b64}", "alt": "", "width": 640}
    except Exception as e:
        logger.warning(f"图片下载失败: {img_url} - {e}")
        return None


def insert_post(title: str, content: str, author_id: str,
                category_id: str, images: list | None = None,
                tags: list[str] | None = None) -> str | None:
    """插入帖子，标题去重"""
    existing = select_one("posts", {"title": title}, columns="id")
    if existing:
        logger.info(f"[跳过] 标题已存在: {title}")
        return None

    now = datetime.now(timezone.utc).isoformat()
    image_json = json.dumps(images) if images else "[]"

    result = insert_one("posts", {
        "title": title,
        "content": content,
        "author_id": author_id,
        "category_id": category_id,
        "post_type": "info",
        "status": "pending_review",
        "images": image_json,
        "created_at": now,
        "updated_at": now,
    }, returning="id")
    post_id = result["id"]

    if tags:
        sync_tags(post_id, tags)

    logger.info(f"[入库] {title}")
    return post_id


def sync_tags(post_id: str, tag_names: list[str]) -> None:
    """同步标签关联"""
    for tag_name in tag_names:
        if not tag_name:
            continue
        row = select_one("tags", {"name": tag_name}, columns="id,name")
        if row:
            tag_id = row["id"]
        else:
            r = insert_one("tags", {"name": tag_name}, returning="id")
            tag_id = r["id"]
        rel = select_one("post_tags", {"post_id": post_id, "tag_id": tag_id}, columns="post_id")
        if not rel:
            insert_one("post_tags", {"post_id": post_id, "tag_id": tag_id})


# ────────────────────── 内容抓取 ──────────────────────

def extract_article_list(page) -> list[dict]:
    """从页面提取文章卡片列表"""
    articles = page.evaluate("""() => {
        const results = [];
        // 查找文章卡片 (各种可能的 CSS 选择器)
        const selectors = [
            'article',
            '[data-testid="article-card"]',
            '.article-card',
            '.widget-news-card',
            'a[href*="/daftar/"]',
            'a[href*="/berita/"]'
        ];

        const seen = new Set();

        // 按选择器遍历
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                // 找到最近的标题链接
                let titleEl = el.tagName === 'A' ? el : el.querySelector('a[href]');
                if (!titleEl) return;
                const href = titleEl.getAttribute('href') || titleEl.href;
                // 只保留绝对路径形式的文章链接
                if (!href || !href.match(/\\/(daftar|berita)\\/[^/]+/)) return;

                const fullUrl = href.startsWith('http') ? href : 'https://www.goal.com' + href;
                if (seen.has(fullUrl)) return;
                seen.add(fullUrl);

                const title = (titleEl.textContent || '').trim();
                if (!title || title.length < 10) return;

                // 图片
                let imgSrc = '';
                const img = el.querySelector('img');
                if (img) {
                    imgSrc = img.getAttribute('src') || img.getAttribute('data-src') || '';
                }

                // 摘要
                let excerpt = '';
                const excerptEl = el.querySelector('p, .excerpt, .description, [class*="excerpt"], [class*="description"], [class*="summary"]');
                if (excerptEl) {
                    excerpt = excerptEl.textContent.trim();
                }

                // 分类标签
                const catEl = el.querySelector('[class*="category"], [class*="tag"], [class*="label"], [data-testid="category"]');
                let category = '';
                if (catEl) category = catEl.textContent.trim();

                results.push({ title, url: fullUrl, imgSrc, excerpt, category });
            });
        });

        return results.slice(0, 30);
    }""")
    return articles


def scrape_article_detail(page, url: str) -> dict | None:
    """抓取文章详情：标题、内容、图片"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
    except Exception as e:
        logger.warning(f"详情页加载超时: {url} - {e}")
        return None

    detail = page.evaluate("""() => {
        // 标题
        let title = '';
        const h1 = document.querySelector('h1');
        if (h1) title = h1.textContent.trim();

        // 正文
        const bodySelectors = [
            '.article-body',
            '[class*="article-body"]',
            '[class*="article_content"]',
            '[class*="content-block"]',
            'article .body',
            '[data-testid="article-body"]',
            '.widget-match-report-body'
        ];
        let bodyEl = null;
        bodySelectors.forEach(sel => {
            if (!bodyEl) bodyEl = document.querySelector(sel);
        });
        // 降级：用 article 标签
        if (!bodyEl) {
            const article = document.querySelector('article');
            if (article) bodyEl = article;
        }

        let contentHTML = '';
        if (bodyEl) {
            contentHTML = bodyEl.innerHTML;
        }

        // 图片
        const images = [];
        const allImgs = document.querySelectorAll('article img, .article-body img, [class*="content"] img');
        allImgs.forEach(img => {
            const src = img.getAttribute('src') || img.getAttribute('data-src');
            if (src && src.startsWith('http') && !src.includes('logo') && !src.includes('icon')) {
                images.push({ url: src, alt: img.getAttribute('alt') || '' });
            }
        });

        // 主图
        let heroImage = '';
        const heroImg = document.querySelector('meta[property="og:image"]');
        if (heroImg) heroImage = heroImg.getAttribute('content') || '';
        if (!heroImage && images.length > 0) heroImage = images[0].url;

        return { title, contentHTML, heroImage, images: images.slice(0, 8) };
    }""")

    if not detail or not detail.get("title"):
        return None

    return detail


def build_html_content(detail: dict, source_url: str) -> str:
    """构建入库 HTML 内容"""
    title = detail.get("title", "")
    hero = detail.get("heroImage", "")
    content = detail.get("contentHTML", "")

    html_parts = []
    if hero:
        html_parts.append(f'<p><img src="{hero}" alt="{title}" style="max-width:100%;border-radius:8px"/></p>')
    html_parts.append(content)
    html_parts.append(f'<hr><p style="font-size:12px;color:#999">Sumber: <a href="{source_url}" target="_blank">Goal.com Indonesia</a></p>')
    return '\n'.join(html_parts)


# ────────────────────── 主流程 ──────────────────────

def run(save_to_db: bool = False, max_articles: int = 15) -> list[dict]:
    logger.info("=== Goal.com Indonesia 体育新闻抓取 ===")

    existing_titles = load_existing_titles() if save_to_db else set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="id-ID",
        )
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        # 抓取首页
        logger.info(f"访问首页: {ID_BASE}")
        try:
            page.goto(ID_BASE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        except Exception as e:
            logger.error(f"首页加载失败: {e}")
            browser.close()
            return []

        articles = extract_article_list(page)
        logger.info(f"首页提取到 {len(articles)} 篇文章")

        # 抓取新闻列表页
        logger.info(f"访问新闻列表: {NEWS_URL}")
        try:
            page.goto(NEWS_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            news_articles = extract_article_list(page)
            logger.info(f"新闻列表提取到 {len(news_articles)} 篇文章")
            articles.extend(news_articles)
        except Exception as e:
            logger.warning(f"新闻列表页加载失败: {e}")

        # 去重
        seen = set()
        unique = []
        for a in articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                unique.append(a)
        articles = unique[:max_articles]
        logger.info(f"去重后共 {len(articles)} 篇")

        # 获取作者和分类
        if save_to_db:
            author_id = lookup_author_id()
            category_id = lookup_category_id()

        result = []
        for i, article in enumerate(articles):
            title_preview = article["title"][:60]
            logger.info(f"抓取 [{i+1}/{len(articles)}]: {title_preview}...")

            if save_to_db and article["title"] in existing_titles:
                logger.info(f"  [跳过] 标题已存在")
                continue

            # 抓取详情
            detail = scrape_article_detail(page, article["url"])
            if not detail:
                logger.warning(f"  [失败] 详情页内容为空")
                continue

            # 下载图片
            images = []
            hero = detail.get("heroImage", "")
            if hero:
                img_data = download_image_as_base64(hero)
                if img_data:
                    images.append(img_data)

            # 构建 HTML
            content = build_html_content(detail, article["url"])

            # 提取标签
            tags = []
            cat = article.get("category", "")
            if cat:
                tags.append(cat)

            if save_to_db:
                insert_post(
                    title=detail["title"],
                    content=content,
                    author_id=author_id,
                    category_id=category_id,
                    images=images,
                    tags=tags,
                )

            result.append({
                "title": detail["title"],
                "url": article["url"],
                "images_count": len(images),
            })

            # 避免请求过快
            time.sleep(1)

        browser.close()

    logger.info(f"=== 抓取完成: 共处理 {len(result)} 篇 ===")
    return result


def main():
    parser = argparse.ArgumentParser(description="Goal.com Indonesia 体育新闻抓取")
    parser.add_argument("--save", action="store_true", help="直接入库")
    parser.add_argument("--max", type=int, default=15, help="最大文章数 (default: 15)")
    args = parser.parse_args()
    run(save_to_db=args.save, max_articles=args.max)


if __name__ == "__main__":
    main()
