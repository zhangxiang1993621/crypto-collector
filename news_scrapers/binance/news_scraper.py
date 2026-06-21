"""币安广场新闻抓取 + 入库脚本

功能：从币安广场新闻页面抓取新闻，直接写入 Supabase posts 表
用法：
    python news_scraper.py                              # 抓取并打印
    python news_scraper.py --save                       # 抓取并直接入库
    python news_scraper.py --save --scroll 10 --max 50  # 自定义参数
"""

import os
import sys
import time
import base64
import logging
import argparse
from pathlib import Path
from typing import Any

# 子进程执行时需要项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
# 直连数据库（绕过 REST API 作业限制）
from db_direct import select_one, select_all, insert_one, upsert_one, get_connection, execute_sql
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.binance.bh/zh-CN/square/news/all"


def scrape_binance_news(scroll_times=3, max_articles=50):
    """抓取币安广场新闻

    参数:
        scroll_times: 向下滚动的次数(加载更多)
        max_articles: 最大抓取条目数
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = context.new_page()
        # 隐藏 webdriver 特征
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)

        logger.info(f"正在访问 {BASE_URL} ...")
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.error(f"页面加载失败: {e}")
            browser.close()
            return []

        # 等待文章列表加载
        logger.info("等待页面内容加载...")
        try:
            page.wait_for_selector("h3", timeout=15000)
        except PlaywrightTimeout:
            logger.warning("等待超时，尝试继续抓取...")

        time.sleep(3)

        # 滚动加载更多文章
        for i in range(scroll_times):
            logger.info(f"第 {i + 1}/{scroll_times} 次滚动加载...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

        # 回到顶部
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)

        # 使用 JavaScript 在浏览器端提取所有文章数据
        logger.info("开始通过 JS 提取文章数据...")
        articles_data = page.evaluate("""(maxCount) => {
            const results = [];
            const seen = new Set();

            const headings = document.querySelectorAll('h3');

            for (const h3 of headings) {
                const title = h3.textContent.trim();
                if (!title || seen.has(title)) continue;
                seen.add(title);

                // 向上查找文章卡片容器 (class 包含 FeedBuzzBaseViewRoot)
                let card = h3.parentElement;
                for (let i = 0; i < 15 && card && card !== document.body; i++) {
                    const cls = (card.className || '').toString();
                    if (cls.includes('FeedBuzzBaseViewRoot')) break;
                    card = card.parentElement;
                }

                if (!card || card === document.body) continue;

                const article = {
                    title: title,
                    source: 'Binance News',
                    content: '',
                    time: '',
                    coins: [],
                    url: '',
                };

                // 遍历卡片内所有文本节点
                const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                const contentParts = [];
                let node;
                while (node = walker.nextNode()) {
                    const txt = node.textContent.trim();
                    if (!txt) continue;

                    // 时间: 匹配相对时间或绝对日期格式
                    if (!article.time && (
                        /^[0-9]+(小时|分钟|天|秒)/.test(txt) ||
                        /^[0-9]+月[0-9]+日/.test(txt) ||
                        /^[0-9]{4}-[0-9]{2}-[0-9]{2}/.test(txt)
                    )) {
                        article.time = txt;
                        continue;
                    }

                    // 正文: 长文本 (排除标题本身)
                    if (txt.length > 40 && txt !== title) {
                        contentParts.push(txt);
                    }
                }
                // 去重：过滤掉被其他段落包含的短文本
                const deduped = contentParts.filter((p, i) => {
                    return !contentParts.some((other, j) => i !== j && other.includes(p));
                });
                article.content = deduped.join('\\n');

                // 提取链接信息
                const allLinks = card.querySelectorAll('a');
                for (const link of allLinks) {
                    const href = link.getAttribute('href') || '';

                    // 币种标签
                    if (href.includes('/trade/') || href.includes('/futures/') || href.includes('/alpha/')) {
                        const coinText = link.textContent.trim();
                        if (coinText && coinText.length < 40) {
                            const url = href.startsWith('http') ? href : 'https://www.binance.bh' + href;
                            if (!article.coins.find(c => c.text === coinText)) {
                                article.coins.push({ text: coinText, url: url });
                            }
                        }
                    }

                    // 文章链接
                    if (!article.url && href.includes('/square/post/')) {
                        article.url = href.startsWith('http') ? href : 'https://www.binance.bh' + href;
                    }
                }

                results.push(article);
            }

            return results.slice(0, maxCount);
        }""", max_articles)

        browser.close()

    # 添加索引
    for i, article in enumerate(articles_data):
        article["index"] = i + 1

    return articles_data


# ═══════════════════════ 数据库入库相关（直连 PostgreSQL） ═══════════════════════


def lookup_author_id() -> str:
    """根据配置的用户名查询 author_id"""
    username = os.environ.get("POSTS_AUTHOR_USERNAME")
    if not username:
        logger.error("缺少 POSTS_AUTHOR_USERNAME 环境变量")
        sys.exit(1)
    row = select_one("profiles", {"username": username}, columns="id,username")
    if row:
        logger.info(f"作者: {username} (id={row['id']})")
        return row["id"]
    logger.error(f"未找到用户: {username}")
    sys.exit(1)


def lookup_category_id() -> str:
    """根据配置的分类名查询 category_id"""
    name = os.environ.get("POSTS_CATEGORY_NAME", "news")
    row = select_one("categories", {"name": name}, columns="id,name")
    if row:
        logger.info(f"分类: {name} (id={row['id']})")
        return row["id"]
    logger.error(f"未找到分类: {name}")
    sys.exit(1)


def load_existing_titles() -> set[str]:
    """加载数据库中已有的帖子标题"""
    rows = select_all("posts", columns="title")
    titles = {r["title"] for r in rows}
    logger.info(f"数据库中已有 {len(titles)} 条帖子")
    return titles


def download_image_as_base64(url: str) -> dict | None:
    """下载图片并转为 base64 data URI"""
    if not url:
        return None
    if url.startswith("/"):
        url = f"https://www.binance.bh{url}"
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/png")
        b64 = base64.b64encode(resp.content).decode("utf-8")
        data_uri = f"data:{content_type};base64,{b64}"
        filename = url.split("/")[-1].split("?")[0] or "image.png"
        if "." not in filename:
            filename = f"image.{content_type.split('/')[-1]}"
        return {"src": data_uri, "filename": filename}
    except Exception as e:
        logger.warning(f"  图片下载失败 ({url[:60]}...): {e}")
        return None


def build_html_content(article: dict, image_list: list[dict]) -> str:
    """将单条新闻构建为 HTML 富文本内容"""
    parts = []

    # 元信息行
    meta_parts = [f"来源: {article.get('source', 'Binance News')}"]
    if article.get("time"):
        meta_parts.append(f"{article['time']}前")
    parts.append(f'<p style="color:#888;font-size:14px;">{" | ".join(meta_parts)}</p>')

    # 内嵌图片（最多 3 张）
    for img in image_list[:3]:
        parts.append(
            f'<p><img src="{img["src"]}" alt="{img["filename"]}" '
            f'style="max-width:100%;border-radius:8px;margin:8px 0;" /></p>'
        )

    # 币种标签
    if article.get("coins"):
        coin_spans = []
        for coin in article["coins"]:
            text = coin.get("text", "")
            coin_url = coin.get("url", "")
            if coin_url:
                coin_spans.append(
                    f'<a href="{coin_url}" target="_blank" '
                    f'style="display:inline-block;margin:2px 4px;padding:2px 8px;'
                    f'background:#f0b90b;color:#000;border-radius:4px;text-decoration:none;font-size:13px;">{text}</a>'
                )
            else:
                coin_spans.append(
                    f'<span style="display:inline-block;margin:2px 4px;padding:2px 8px;'
                    f'background:#f0b90b;color:#000;border-radius:4px;font-size:13px;">{text}</span>'
                )
        parts.append(f'<p>{"".join(coin_spans)}</p>')

    # 正文
    content = article.get("content", "")
    if content:
        for p in content.split("\n"):
            p = p.strip()
            if not p:
                continue
            p_escaped = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            parts.append(f"<p>{p_escaped}</p>")

    # 原文链接
    if article.get("url"):
        parts.append(
            f'<p style="margin-top:16px;">'
            f'<a href="{article["url"]}" target="_blank" style="color:#f0b90b;text-decoration:none;">'
            f'查看原文 &rarr;</a></p>'
        )

    return "\n".join(parts)


def sync_tags_for_post(post_id: str, tag_names: list[str]) -> None:
    """为单篇文章同步标签：查找/创建 tag，写入 post_tags 关联"""
    if not tag_names:
        return

    # tag 不加 # 前缀（与 indo_news_scraper 等保持一致）
    cleaned = [t.lstrip("#") for t in tag_names]
    unique_names = list(set(cleaned))

    # 查询已有标签（使用 IN 查询）
    existing_map = {}
    if unique_names:
        placeholders = ", ".join(["%s"] * len(unique_names))
        sql = f'SELECT id, name FROM tags WHERE name IN ({placeholders})'
        rows = execute_sql(sql, tuple(unique_names))
        if rows:
            existing_map = {r["name"]: r["id"] for r in rows}

    # 创建新标签
    new_names = [n for n in unique_names if n not in existing_map]
    if new_names:
        for name in new_names:
            try:
                result = insert_one("tags", {"name": name, "posts_count": 0}, returning="id")
                if result:
                    existing_map[name] = result["id"]
            except Exception:
                pass

    # 写入 post_tags 关联 + 更新计数
    for name in unique_names:
        tag_id = existing_map.get(name)
        if not tag_id:
            continue
        try:
            # 检查是否已有关联
            link = select_one("post_tags", {"post_id": post_id, "tag_id": tag_id}, columns="post_id")
            if not link:
                insert_one("post_tags", {"post_id": post_id, "tag_id": tag_id})
                # 更新 posts_count
                tag = select_one("tags", {"id": tag_id}, columns="posts_count")
                if tag:
                    new_count = (tag.get("posts_count", 0) or 0) + 1
                    update_one("tags", {"posts_count": new_count}, {"id": tag_id})
        except Exception:
            pass


def insert_one_post(article: dict, author_id: str, category_id: str,
                    existing_titles: set) -> bool:
    """将单条新闻组装并入库，标题已存在则跳过"""
    title = article["title"]

    if title in existing_titles:
        logger.info(f"  [跳过] 已存在: {title[:40]}")
        return False

    # 下载图片转 base64
    image_list = []
    seen = set()
    for img in article.get("images", [])[:5]:
        src = img.get("src", "")
        if not src or src in seen:
            continue
        seen.add(src)
        info = download_image_as_base64(src)
        if info:
            image_list.append(info)

    # 构建 HTML
    html_content = build_html_content(article, image_list)

    # 插入 posts 表
    post_data = {
        "title": title,
        "content": html_content,
        "author_id": author_id,
        "category_id": category_id,
        "status": "pending_review",
        "images": image_list,
        "is_hot": False,
        "is_pinned": False,
    }

    try:
        result = insert_one("posts", post_data, returning="id")
        if result:
            post_id = result["id"]
            existing_titles.add(title)

            # 同步标签
            tags = article.get("tags", [])
            if tags:
                sync_tags_for_post(post_id, tags)

            logger.info(f"  [入库] {title[:40]} | tags={tags}")
            return True
        return False
    except Exception as e:
        logger.error(f"  [失败] {title[:40]}: {e}")
        return False


# ═══════════════════════ 抓取相关 ═══════════════════════

def fetch_article_images(page, articles: list[dict],
                         save_to_db=False, author_id=None,
                         category_id=None, existing_titles=None) -> None:
    """访问每篇文章详情页，抓取图片和标签，可选直接入库"""
    saved = 0
    for article in articles:
        if not article.get("url"):
            article["images"] = []
            article["tags"] = []
            continue

        try:
            logger.info(f"抓取: {article['title'][:40]}...")
            try:
                page.goto(article["url"], wait_until="load", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(5000)
            try:
                page.wait_for_selector('h1, h2, [class*="title"]', timeout=10000)
            except Exception:
                pass
            time.sleep(2)

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            result = page.evaluate("""() => {
                const urls = [];
                const seen = new Set();
                const tags = new Set();

                document.querySelectorAll('img[class*="images-box-item"]').forEach(img => {
                    const src = img.getAttribute('src') || '';
                    if (src && !seen.has(src)) {
                        seen.add(src);
                        urls.push({ src: src, alt: img.getAttribute('alt') || '', type: 'content' });
                    }
                });

                document.querySelectorAll('img[class*="css-mqxzqu"]').forEach(img => {
                    const src = img.getAttribute('src') || '';
                    if (src && !seen.has(src)) {
                        seen.add(src);
                        urls.push({ src: src, alt: img.getAttribute('alt') || '', type: 'banner' });
                    }
                });

                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while (node = walker.nextNode()) {
                    const parent = node.parentElement;
                    if (!parent) continue;
                    const tag = parent.tagName.toLowerCase();
                    const cls = (parent.className || '').toString();
                    if (tag === 'script' || tag === 'style' || cls.includes('cookie') || cls.includes('ot-')) continue;
                    const text = node.textContent;
                    const matches = text.match(/#[\\w\\u4e00-\\u9fff\\u3040-\\u309f\\u30a0-\\u30ff-]+/g);
                    if (matches) {
                        matches.forEach(m => {
                            const clean = m.replace(/^#/, '').trim();
                            if (clean.length >= 2 && !/^\\d+$/.test(clean) && clean !== 'ot' && clean !== 'Square') {
                                tags.add(clean);
                            }
                        });
                    }
                }

                return {
                    images: urls.filter(u => !u.src.includes('avatar') && !u.src.includes('nftstatic')),
                    tags: [...tags].slice(0, 10)
                };
            }""")

            article["images"] = result.get("images", [])
            article["tags"] = result.get("tags", [])
            logger.info(f"  图片 {len(article['images'])} 张, 标签 {len(article['tags'])} 个")

            # 直接入库
            if save_to_db and author_id and category_id and existing_titles is not None:
                if insert_one_post(article, author_id, category_id, existing_titles):
                    saved += 1

        except Exception as e:
            logger.warning(f"  失败: {e}")
            article["images"] = []
            article["tags"] = []

    if save_to_db:
        logger.info(f"入库完成: {saved}/{len(articles)} 条")


def print_summary(articles):
    """打印汇总"""
    print("\n" + "=" * 60)
    print(f"抓取完成！共获取 {len(articles)} 条新闻")
    print("=" * 60)
    for article in articles[:5]:
        print(f"\n[{article['index']}] {article['title']}")
        print(f"    时间: {article['time']}")
        if article["coins"]:
            print(f"    币种: {', '.join(c['text'] for c in article['coins'])}")
        if article["url"]:
            print(f"    链接: {article['url']}")
    if len(articles) > 5:
        print(f"\n... 还有 {len(articles) - 5} 条新闻")


def main():
    parser = argparse.ArgumentParser(description="币安广场新闻抓取 + 入库工具")
    parser.add_argument("--scroll", type=int, default=10, help="滚动次数(默认 10)")
    parser.add_argument("--max", type=int, default=100, help="最大抓取条数(默认 100)")
    parser.add_argument("--save", action="store_true", help="直接入库（需配置环境变量）")
    args = parser.parse_args()

    logger.info("=== 币安广场新闻抓取 ===")

    # 列表页抓取
    articles = scrape_binance_news(scroll_times=args.scroll, max_articles=args.max)
    if not articles:
        logger.warning("未抓取到任何新闻（本网络无法访问 www.binance.bh）")
        return

    # 准备入库环境
    author_id = category_id = existing_titles = None
    if args.save:
        author_id = lookup_author_id()
        category_id = lookup_category_id()
        existing_titles = load_existing_titles()

    # 逐篇抓取详情（图片 + 标签）并可选入库
    logger.info(f"开始处理 {len(articles)} 篇文章...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = context.new_page()
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception:
            pass

        fetch_article_images(page, articles,
                             save_to_db=args.save,
                             author_id=author_id,
                             category_id=category_id,
                             existing_titles=existing_titles)
        browser.close()

    print_summary(articles)
    logger.info("=== 抓取完成 ===")


if __name__ == "__main__":
    main()
