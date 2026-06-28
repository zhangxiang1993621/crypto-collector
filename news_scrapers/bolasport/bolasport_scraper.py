"""BolaSport.com 足球+羽毛球新闻抓取 + 发帖

功能：从 bolasport.com 抓取足球和羽毛球新闻，直接写入 Supabase posts 表
用法：
    python bolasport_scraper.py                         # 抓取并打印预览
    python bolasport_scraper.py --save                  # 抓取并直接入库
    python bolasport_scraper.py --save --max 10         # 最多10条
"""

import os
import re
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.bolasport.com/"
BADMINTON_URL = "https://www.bolasport.com/bulu-tangkis"

TAGS_DEFAULT = ["BolaSport", "Olahraga", "Indonesia"]


def get_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME") or "indoAdmin"
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error("发帖账号 %s 不存在", username)
        sys.exit(1)
    logger.info("作者: %s (id=%s)", username, row["id"])
    return row["id"]


def get_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME") or "Sports Talk"
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error("未找到分类: %s", name)
        sys.exit(1)
    logger.info("分类: %s (id=%s)", name, row["id"])
    return row["id"]


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def extract_tags_from_text(title: str, category: str) -> list[str]:
    combined = (title + " " + category).lower()
    tags = []
    tag_keywords = {
        "Sepakbola": ["sepak bola", "bola", "liga", "pemain", "transfer", "striker"],
        "Badminton": ["bulu tangkis", "badminton", "raket", "ganda", "tunggal"],
        "PialaDunia2026": ["piala dunia", "world cup", "juara bola dunia"],
        "LigaIndonesia": ["super league", "liga 1", "persija", "persib", "arema"],
        "LigaInggris": ["inggris", "premier", "chelsea", "arsenal", "liverpool", "manchester"],
        "LaLiga": ["spanyol", "real madrid", "barcelona", "la liga"],
        "SerieA": ["italia", "serie a", "ac milan", "juventus", "inter"],
        "MotoGP": ["moto gp", "motogp", "marquez", "balap"],
    }
    for tag_name, keywords in tag_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(tag_name)
    if not tags:
        tags.append("Olahraga")
    return tags


def scrape_bolasport(max_articles: int = 20) -> list[dict]:
    logger.info("启动 Playwright 抓取 BolaSport...")
    articles = []

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
            locale="id-ID",
        )
        page = context.new_page()

        urls_to_scrape = [
            ("首页", BASE_URL),
            ("羽毛球", BADMINTON_URL),
        ]

        seen_urls = set()

        for label, url in urls_to_scrape:
            logger.info("访问 [%s] %s", label, url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning("页面加载失败 [%s]: %s", label, e)
                continue

            time.sleep(3)

            try:
                page.wait_for_selector(".news-list__item", timeout=10000)
            except PlaywrightTimeout:
                logger.warning("[%s] 等待超时，尝试继续...", label)

            items = page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('.news-list__item');
                for (const item of items) {
                    const titleEl = item.querySelector('h2.news-list__title');
                    const linkEl = item.querySelector('a.news-list__link');
                    const imgEl = item.querySelector('.news-list__thumb img');
                    const dateEl = item.querySelector('.news-list__date');
                    const catEl = item.querySelector('.news-list__category');

                    if (titleEl && linkEl) {
                        results.push({
                            title: titleEl.textContent.trim(),
                            url: linkEl.getAttribute('href') || '',
                            image_url: imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || '') : '',
                            date: dateEl ? dateEl.textContent.trim() : '',
                            category: catEl ? catEl.textContent.trim() : ''
                        });
                    }
                }
                return results;
            }""")

            for item in items:
                url_val = item.get("url", "")
                title = item.get("title", "")
                if not url_val or not title:
                    continue
                if url_val in seen_urls:
                    continue
                if "bola" not in url_val.lower() and "bolasport" not in url_val.lower():
                    if not url_val.startswith("https://"):
                        url_val = "https://www.bolasport.com" + (url_val if url_val.startswith("/") else "/" + url_val)
                seen_urls.add(url_val)
                articles.append({
                    "title": title,
                    "url": url_val,
                    "source": label,
                    "image_url": item.get("image_url", ""),
                    "date": item.get("date", ""),
                    "category": item.get("category", ""),
                })

            logger.info("[%s] 获取 %d 条", label, len(items))

            if len(articles) >= max_articles:
                break

        browser.close()

    logger.info("总计 %d 条", len(articles))
    return articles


def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    url = _e(item["url"])
    category = _e(item.get("category", ""))
    source_label = _e(item.get("source", "BolaSport"))

    parts = [
        '<div style="background:#e3f2fd;padding:12px 16px;border-radius:8px;'
        'margin:0 0 14px;border-left:4px solid #1565c0;">'
        '<p style="font-weight:bold;color:#0d47a1;margin:0 0 6px;">'
        f'<span style="background:#1565c0;color:#fff;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;margin-right:6px;">BOLASPORT</span>'
    ]

    if category:
        parts.append(
            f'<span style="color:#1565c0;font-size:12px;">[{category}]</span> '
        )

    parts.append(
        f'{source_label}</p>'
        f'<p style="font-size:16px;color:#0d47a1;line-height:1.6;margin:0;">{title}</p>'
        '</div>',
    )

    if item.get("date"):
        parts.append(
            f'<p style="color:#888;font-size:12px;margin:4px 0;">'
            f'{_e(item["date"])}</p>'
        )

    if url:
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener" '
            'style="color:#1565c0;text-decoration:none;font-weight:bold;font-size:13px;">'
            'Baca selengkapnya di BolaSport →</a>'
        )

    return "\n".join(parts)


def sync_tags(post_id: str, tags: list[str]) -> None:
    if not tags:
        return
    unique = list(set(tags))
    em = {}
    if unique:
        placeholders = ", ".join(["%s"] * len(unique))
        sql = f"SELECT id, name FROM tags WHERE name IN ({placeholders})"
        rows = execute_sql(sql, tuple(unique))
        if rows:
            em = {d["name"]: d["id"] for d in rows}
    new = [n for n in unique if n not in em]
    if new:
        for name in new:
            try:
                result = insert_one("tags", {"name": name, "posts_count": 0}, returning="id")
                if result:
                    em[name] = result["id"]
            except Exception:
                pass
    for name in unique:
        tid = em.get(name)
        if not tid:
            continue
        try:
            link = select_one("post_tags", {"post_id": post_id, "tag_id": tid}, columns="post_id")
            if not link:
                insert_one("post_tags", {"post_id": post_id, "tag_id": tid})
        except Exception:
            pass


def deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = item["title"].lower().strip()[:80]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def run(save: bool = False, max_items: int = 20):
    logger.info("=== BolaSport.com 新闻抓取 ===")

    articles = scrape_bolasport(max_articles=max_items)
    articles = deduplicate(articles)

    if not articles:
        logger.warning("无内容")
        return

    if len(articles) > max_items:
        articles = articles[:max_items]

    print("\n" + "=" * 60)
    print("  BolaSport.com 新闻")
    print("=" * 60)
    for i, art in enumerate(articles):
        cat = f"[{art.get('category', '')}]" if art.get("category") else ""
        time_str = f" {art.get('date', '')}" if art.get("date") else ""
        print(f"\n[{i + 1}] {cat} {art['title'][:100]}{time_str}")
        print(f"  {art['url']}")

    if save:
        author_id = get_author_id()
        category_id = get_category_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        for art in articles:
            content = build_post_html(art)
            tags = TAGS_DEFAULT + extract_tags_from_text(
                art["title"], art.get("category", "")
            )

            try:
                result = insert_one("posts", {
                    "title": art["title"][:200],
                    "content": content,
                    "author_id": author_id,
                    "category_id": category_id,
                    "post_type": "info",
                    "status": "pending_review",
                    "created_at": now,
                    "updated_at": now,
                }, returning="id")
                sync_tags(result["id"], tags)
                saved += 1
                logger.info("  [入库] %s... | tags=%s", art["title"][:50], tags[:5])
            except Exception as e:
                logger.error("  入库失败: %s", e)

        logger.info("[入库] %d/%d 条", saved, len(articles))

    logger.info("=== 完成 ===")


def main():
    parser = argparse.ArgumentParser(description="BolaSport.com 新闻抓取")
    parser.add_argument("--save", action="store_true", help="写入数据库")
    parser.add_argument("--max", type=int, default=20, help="最大条目数")
    args = parser.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
