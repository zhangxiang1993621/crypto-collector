"""FIFA 世界杯官方 Blog 抓取 + 入库脚本

功能：从 FIFA 官方 Blog 页面抓取文章，存入 Supabase posts 表
- 抓取列表页所有文章链接
- 逐篇访问详情页提取标题、内容、图片、标签
- 图片下载为 base64 内嵌
- 标题去重，避免重复入库
- 使用 indoAdmin 账号，发布到 Sports Talk 分类

用法：
    python fifa_blog_scraper/fifa_blog_scraper.py                  # 仅抓取打印
    python fifa_blog_scraper/fifa_blog_scraper.py --save           # 抓取并直接入库
    python fifa_blog_scraper/fifa_blog_scraper.py --save --output backup.json
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

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://fifaworldcup26.hospitality.fifa.com"
BLOG_LIST_URL = f"{BASE_URL}/blog"


# ────────────────────── Supabase 工具 ──────────────────────

def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 环境变量")
        sys.exit(1)
    return create_client(url, key)


def lookup_author_id(client: Client) -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    result = client.table("profiles").select("id,username").eq("username", username).execute()
    if not result.data:
        logger.error(f"未找到作者: {username}")
        sys.exit(1)
    logger.info(f"作者: {username} (id={result.data[0]['id']})")
    return result.data[0]["id"]


def lookup_category_id(client: Client) -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME", "Sports Talk")
    result = client.table("categories").select("id,name").eq("name", name).execute()
    if not result.data:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    logger.info(f"分类: {name} (id={result.data[0]['id']})")
    return result.data[0]["id"]


def load_existing_titles(client: Client) -> set[str]:
    result = client.table("posts").select("title").execute()
    titles = {r["title"] for r in result.data}
    logger.info(f"数据库中已有 {len(titles)} 条帖子")
    return titles


def download_image_as_base64(img_url: str) -> dict | None:
    """下载图片并转为 base64 data URI"""
    if not img_url:
        return None
    try:
        resp = httpx.get(img_url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/webp")
        b64 = base64.b64encode(resp.content).decode("utf-8")
        data_uri = f"data:{content_type};base64,{b64}"
        filename = img_url.split("/")[-1].split("?")[0] or "image.webp"
        if "." not in filename:
            filename = f"image.{content_type.split('/')[-1]}"
        return {"src": data_uri, "filename": filename}
    except Exception as e:
        logger.warning(f"  图片下载失败 ({img_url[:60]}...): {e}")
        return None


# ────────────────────── 标签同步 ──────────────────────

def sync_tags_for_post(client: Client, post_id: str, tag_names: list[str]) -> None:
    """为文章同步标签：查找/创建 tag，写入 post_tags 关联"""
    if not tag_names:
        return

    unique_names = list(set(tag_names))

    existing_map = {}
    try:
        existing = client.table("tags").select("id,name").in_("name", unique_names).execute()
        existing_map = {r["name"]: r["id"] for r in existing.data}
    except Exception:
        pass

    new_names = [n for n in unique_names if n not in existing_map]
    if new_names:
        try:
            result = client.table("tags").insert([{"name": n, "posts_count": 0} for n in new_names]).execute()
            for r in result.data:
                existing_map[r["name"]] = r["id"]
        except Exception:
            pass

    for name in unique_names:
        tag_id = existing_map.get(name)
        if not tag_id:
            continue
        try:
            link = client.table("post_tags").select("post_id").eq("post_id", post_id).eq("tag_id", tag_id).execute()
            if not link.data:
                client.table("post_tags").insert({"post_id": post_id, "tag_id": tag_id}).execute()
                tag = client.table("tags").select("posts_count").eq("id", tag_id).single().execute()
                new_count = (tag.data.get("posts_count", 0) or 0) + 1
                client.table("tags").update({"posts_count": new_count}).eq("id", tag_id).execute()
        except Exception:
            pass


# ────────────────────── 页面抓取 ──────────────────────

def fetch_article_list(max_articles: int = 50) -> list[dict]:
    """从 Blog 列表页抓取所有文章链接、标题、描述和标签"""
    logger.info(f"正在访问 Blog 列表页: {BLOG_LIST_URL}")
    articles = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        page.goto(BLOG_LIST_URL, wait_until="load", timeout=60000)
        page.wait_for_timeout(5000)

        # 提取文章列表
        articles = page.evaluate("""(maxCount) => {
            const items = [];
            
            // 特色文章（顶部）
            const featuredLink = document.querySelector('a[href^="/blog/"]');
            
            // "Browse all" 区域的文章列表
            const browseSection = document.querySelector('[class*="blog_list"]') 
                || document.querySelector('[class*="collection-list"]')
                || document.querySelector('main ul + div ul, main div[class*="list"]');
            
            // 更通用的方式：获取所有指向 /blog/xxx 的链接（非 /blog 本身）
            const allLinks = document.querySelectorAll('a[href^="/blog/"]');
            const seen = new Set();
            
            allLinks.forEach(link => {
                const href = link.getAttribute('href');
                if (!href || href === '/blog' || href === '/blog/' || seen.has(href)) return;
                seen.add(href);
                
                // 尝试获取标题
                let title = '';
                const hEl = link.querySelector('h1, h2, h3, p[class*="title"], [class*="title"]');
                if (hEl) {
                    title = hEl.textContent.trim();
                } else {
                    title = link.textContent.trim();
                }
                if (!title || title.length < 3) return;
                
                // 尝试获取描述
                let description = '';
                const descEl = link.querySelector('p:not([class*="title"])');
                if (descEl) {
                    description = descEl.textContent.trim();
                }
                
                // 尝试获取文章 URL
                const url = href.startsWith('http') ? href : 'https://fifaworldcup26.hospitality.fifa.com' + href;
                
                // 尝试获取标签（在列表项中查找）
                let tags = [];
                const listItem = link.closest('li') || link.closest('[class*="item"]');
                if (listItem) {
                    const tagEls = listItem.querySelectorAll('[class*="tag"], [class*="category"]');
                    tags = Array.from(tagEls).map(t => t.textContent.trim()).filter(Boolean);
                }
                
                items.push({
                    title: title,
                    description: description,
                    url: url,
                    tags: tags
                });
            });
            
            return items.slice(0, maxCount);
        }""", max_articles)

        browser.close()

    logger.info(f"从列表页获取到 {len(articles)} 篇文章")
    return articles


def fetch_article_detail(url: str) -> dict | None:
    """访问文章详情页，提取完整内容和图片"""
    logger.info(f"  访问文章: {url}")
    detail = {"url": url}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        try:
            page.goto(url, wait_until="load", timeout=30000)
            page.wait_for_timeout(3000)

            data = page.evaluate("""() => {
                const main = document.querySelector('main');
                if (!main) return null;
                
                // 标题
                const h1 = main.querySelector('h1');
                const title = h1 ? h1.textContent.trim() : '';
                
                // 副标题/描述
                const descEl = main.querySelector('.post_hero-decription, h1 + p');
                const description = descEl ? descEl.textContent.trim() : '';
                
                // 标签
                const tagContainer = main.querySelector('[class*="tag-list"], [class*="tags"], ul + div ul');
                let tags = [];
                if (tagContainer) {
                    const tagItems = tagContainer.querySelectorAll('li, [class*="tag"]');
                    tags = Array.from(tagItems).map(t => t.textContent.trim()).filter(t => t && t.length < 50);
                }
                
                // 英雄图
                const heroImg = main.querySelector('.post_hero-image');
                let heroSrc = '';
                if (heroImg) {
                    heroSrc = heroImg.src || heroImg.getAttribute('data-src') || '';
                }
                
                // 正文内容
                const contentEl = main.querySelector('.blog_rich-text, .post-content, [class*="rich-text"]');
                let contentHTML = '';
                if (contentEl) {
                    contentHTML = contentEl.innerHTML;
                }
                
                // 内容区所有图片
                const contentImgs = [];
                if (contentEl) {
                    const imgs = contentEl.querySelectorAll('img');
                    imgs.forEach(img => {
                        const src = img.src || img.getAttribute('data-src');
                        if (src && !contentImgs.find(i => i.src === src)) {
                            contentImgs.push({ src: src, alt: img.alt || '' });
                        }
                    });
                }
                
                return {
                    title: title,
                    description: description,
                    tags: tags,
                    hero_image: heroSrc,
                    content_html: contentHTML,
                    content_images: contentImgs
                };
            }""")

            if data:
                detail.update(data)

        except PlaywrightTimeout:
            logger.warning(f"  页面加载超时: {url}")
        except Exception as e:
            logger.error(f"  提取文章失败: {e}")
        finally:
            browser.close()

    return detail


# ────────────────────── HTML 构建 ──────────────────────

def build_html_content(article: dict, hero_b64: dict | None, content_images: list[dict]) -> str:
    """构建文章的 HTML 富文本内容"""
    parts = []

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f'<p style="color:#888;font-size:14px;">来源: FIFA Official Blog | {now_str} (UTC)</p>')

    # 副标题
    if article.get("description"):
        parts.append(f'<p style="font-size:16px;color:#555;font-style:italic;">{article["description"]}</p>')

    # 英雄图
    if hero_b64:
        parts.append(
            f'<p><img src="{hero_b64["src"]}" alt="{hero_b64["filename"]}" '
            f'style="max-width:100%;border-radius:8px;margin:8px 0;" /></p>'
        )

    # 正文内容（HTML 原文，过滤 iframe 等不需要的元素）
    if article.get("content_html"):
        # 保留原始 HTML 结构，去掉 iframe 标签
        import re
        clean_html = re.sub(r'<iframe[^>]*>.*?</iframe>', '', article["content_html"], flags=re.DOTALL)
        clean_html = re.sub(r'<script[^>]*>.*?</script>', '', clean_html, flags=re.DOTALL)
        parts.append(f'<div style="line-height:1.8;">{clean_html}</div>')

    # 内嵌内容图片
    for img in content_images[:5]:
        parts.append(
            f'<p><img src="{img["src"]}" alt="{img.get("filename", "")}" '
            f'style="max-width:100%;border-radius:8px;margin:8px 0;" /></p>'
        )

    # 原文链接
    if article.get("url"):
        parts.append(
            f'<p style="margin-top:24px;">'
            f'<a href="{article["url"]}" target="_blank" style="color:#1a73e8;text-decoration:none;">'
            f'阅读原文 &rarr;</a></p>'
        )

    return "\n".join(parts)


# ────────────────────── 入库 ──────────────────────

def insert_post(client: Client, title: str, content: str, author_id: str,
                 category_id: str, tag_names: list[str], existing_titles: set) -> bool:
    """插入帖子，标题已存在则跳过"""
    if title in existing_titles:
        logger.info(f"  [跳过] 已存在: {title[:50]}")
        return False

    now = datetime.now(timezone.utc).isoformat()
    resp = client.table("posts").insert({
        "title": title,
        "content": content,
        "author_id": author_id,
        "category_id": category_id,
        "status": "pending_review",
        "created_at": now,
        "updated_at": now,
    }).execute()
    post_id = resp.data[0]["id"]

    # 同步标签
    sync_tags_for_post(client, post_id, tag_names)
    existing_titles.add(title)
    return True


# ────────────────────── 主流程 ──────────────────────

def run(save_to_db: bool = False, max_articles: int = 50, output_file: str | None = None) -> list[dict]:
    logger.info("=== FIFA 世界杯 Blog 抓取 ===")

    # 1. 获取文章列表
    articles = fetch_article_list(max_articles)
    if not articles:
        logger.warning("未获取到任何文章")
        return []

    # 2. 准备入库环境
    client = None
    author_id = None
    category_id = None
    existing_titles = set()
    if save_to_db:
        client = get_supabase_client()
        author_id = lookup_author_id(client)
        category_id = lookup_category_id(client)
        existing_titles = load_existing_titles(client)

    # 3. 逐篇抓取详情
    results = []
    saved_count = 0

    for i, article in enumerate(articles):
        logger.info(f"[{i + 1}/{len(articles)}] {article['title'][:60]}")

        detail = fetch_article_detail(article["url"])
        if not detail or not detail.get("title"):
            logger.warning(f"  跳过，未能提取内容")
            continue

        # 合并列表页标签和详情页标签，追加 FIFA 默认标签
        all_tags = list(set(article.get("tags", []) + detail.get("tags", [])))
        all_tags.extend(["FWC26", "WorldCup"])

        # 下载图片
        hero_b64 = None
        if detail.get("hero_image"):
            hero_b64 = download_image_as_base64(detail["hero_image"])

        content_images = []
        for img in detail.get("content_images", [])[:5]:
            b64 = download_image_as_base64(img["src"])
            if b64:
                content_images.append(b64)

        # 构建 HTML
        html = build_html_content(detail, hero_b64, content_images)

        result = {
            "title": detail["title"],
            "description": detail.get("description", ""),
            "url": article["url"],
            "tags": all_tags,
            "hero_image": detail.get("hero_image", ""),
            "content_html": html,
        }
        results.append(result)

        # 入库
        if save_to_db and client:
            if insert_post(client, detail["title"], html, author_id, category_id, all_tags, existing_titles):
                saved_count += 1
                logger.info(f"  [入库] {detail['title'][:50]}")
            else:
                logger.info(f"  [跳过] {detail['title'][:50]}")

        time.sleep(1)  # 避免请求过快

    if save_to_db:
        logger.info(f"入库完成: 新增 {saved_count} 篇")

    logger.info("=== FIFA Blog 抓取完成 ===")
    return results


def main():
    parser = argparse.ArgumentParser(description="FIFA 世界杯 Blog 抓取")
    parser.add_argument("--save", action="store_true", help="直接入库")
    parser.add_argument("--max", type=int, default=50, help="最大抓取文章数 (默认 50)")
    parser.add_argument("--output", type=str, default=None, help="额外输出 JSON 文件(可选)")
    args = parser.parse_args()

    run(save_to_db=args.save, max_articles=args.max, output_file=args.output)


if __name__ == "__main__":
    main()
