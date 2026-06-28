"""Dailysports.id 多体育项目新闻抓取 + 发帖

功能：从 dailysports.id 抓取多种体育新闻，直接写入 Supabase posts 表
用法：
    python dailysports_scraper.py                    # 抓取并打印预览
    python dailysports_scraper.py --save             # 抓取并直接入库
    python dailysports_scraper.py --save --max 10    # 最多10条
"""

import os
import re
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://dailysports.id/"

TAGS_DEFAULT = ["Dailysports", "Olahraga", "Indonesia"]


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


def strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def extract_tags_from_text(title: str, summary: str, category: str) -> list[str]:
    combined = (title + " " + summary + " " + category).lower()
    tags = []
    tag_keywords = {
        "Sepakbola": ["sepak bola", "sepakbola", "liga 1", "super league", "prediksi"],
        "LigaInggris": ["premier league", "liga inggris", "liga primer", "arsenal", "chelsea", "liverpool"],
        "LaLiga": ["la liga", "real madrid", "barcelona"],
        "SerieA": ["serie a", "liga italia", "ac milan", "juventus"],
        "ChampionsLeague": ["liga champions", "ucl", "champions"],
        "PialaAsia": ["piala asia", "afc"],
        "PialaDunia": ["piala dunia", "world cup", "fifa"],
        "BuluTangkis": ["bulu tangkis", "badminton"],
        "MotoGP": ["moto gp", "motogp"],
        "TimnasIndonesia": ["timnas indonesia", "tim nasional", "indonesia u"],
        "Transfer": ["transfer", "bursa", "rekrut", "kontrak"],
    }
    for tag_name, keywords in tag_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(tag_name)
    if not tags:
        tags.append("Olahraga")
    return tags


def fetch_articles() -> list[dict]:
    logger.info("抓取 %s", BASE_URL)
    articles = []

    try:
        resp = httpx.get(
            BASE_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("HTTP %s", resp.status_code)
            return articles

        html = resp.text

        article_pattern = re.compile(
            r'<a\s[^>]*href="(https://dailysports\.id/([^/]+)/\d+/[^"]*)"[^>]*title="([^"]*)"[^>]*>',
            re.DOTALL,
        )
        matches = article_pattern.findall(html)
        seen_urls = set()

        for url, category, title in matches:
            url = url.strip()
            title = title.strip()
            if url in seen_urls or not title or len(title) < 10:
                continue
            seen_urls.add(url)

            cat_name = category.replace("-", " ").title() if category else ""

            articles.append({
                "title": title,
                "url": url,
                "source": "Dailysports.id",
                "category": cat_name,
                "summary": title,
            })

    except Exception as e:
        logger.error("抓取异常: %s", e)

    if not articles:
        alt_pattern = re.compile(
            r'<a\s[^>]*href="(https://dailysports\.id/[^"]+/(\d+)/[^"]*)"[^>]*>'
            r'\s*<img[^>]*alt="([^"]+)"',
            re.DOTALL,
        )
        alt_matches = alt_pattern.findall(resp.text if "resp" in dir() and resp.text else "")
        seen_urls = set()
        for url, article_id, alt_text in alt_matches:
            url = url.strip()
            alt_text = alt_text.strip()
            if url in seen_urls or not alt_text or len(alt_text) < 10:
                continue
            seen_urls.add(url)
            articles.append({
                "title": alt_text,
                "url": url,
                "source": "Dailysports.id",
                "category": "",
                "summary": alt_text,
            })

    logger.info("获取 %d 条", len(articles))
    return articles


def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    url = _e(item["url"])
    category = _e(item.get("category", ""))

    parts = [
        '<div style="background:#ede7f6;padding:12px 16px;border-radius:8px;'
        'margin:0 0 14px;border-left:4px solid #4527a0;">'
        '<p style="font-weight:bold;color:#311b92;margin:0 0 6px;">'
        f'<span style="background:#4527a0;color:#fff;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;margin-right:6px;">DS</span>'
        f'Dailysports.id',
    ]

    if category:
        parts.append(
            f' <span style="color:#7c4dff;font-size:12px;">[{category}]</span>'
        )

    parts.append(
        f'</p>'
        f'<p style="font-size:16px;color:#311b92;line-height:1.6;margin:0;">{title}</p>'
        '</div>',
    )

    if url:
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener" '
            'style="color:#4527a0;text-decoration:none;font-weight:bold;font-size:13px;">'
            'Baca selengkapnya di Dailysports →</a>'
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


def run(save: bool = False, max_items: int = 15):
    logger.info("=== Dailysports.id 新闻抓取 ===")

    articles = fetch_articles()
    articles = deduplicate(articles)
    logger.info("去重后 %d 条", len(articles))

    if not articles:
        logger.warning("无内容")
        return

    if len(articles) > max_items:
        articles = articles[:max_items]

    print("\n" + "=" * 60)
    print("  Dailysports.id 新闻")
    print("=" * 60)
    for i, art in enumerate(articles):
        cat = f"[{art.get('category', '')}]" if art.get("category") else ""
        print(f"\n[{i + 1}] {cat} {art['title'][:100]}")
        print(f"  {art['url']}")

    if save:
        author_id = get_author_id()
        category_id = get_category_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        for art in articles:
            content = build_post_html(art)
            tags = TAGS_DEFAULT + extract_tags_from_text(
                art["title"], "", art.get("category", "")
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
    parser = argparse.ArgumentParser(description="Dailysports.id 新闻抓取")
    parser.add_argument("--save", action="store_true", help="写入数据库")
    parser.add_argument("--max", type=int, default=15, help="最大条目数")
    args = parser.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
