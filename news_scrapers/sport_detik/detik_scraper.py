"""Detik Sport 新闻抓取 + 发帖

功能：从 sport.detik.com RSS 抓取足球 + 羽毛球新闻，直接写入 Supabase posts 表
用法：
    python detik_scraper.py                    # 抓取并打印预览
    python detik_scraper.py --save             # 抓取并直接入库
    python detik_scraper.py --save --max 10    # 最多10条
"""

import os
import re
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree

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

RSS_URLS = [
    "https://sport.detik.com/rss.xml",
]

INTEREST_CATEGORIES = ["sepakbola", "raket", "bola-dunia", "juara-bola-dunia-2026"]

TAGS_DEFAULT = ["Detik", "Sport", "Indonesia", "Olahraga"]


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


def extract_tags_from_text(title: str, summary: str) -> list[str]:
    combined = (title + " " + summary).lower()
    tags = []
    tag_keywords = {
        "Sepakbola": ["sepak bola", "sepakbola", "liga", "pemain", "striker", "gelandang"],
        "Badminton": ["bulu tangkis", "badminton", "raket", "ganda putra", "ganda campuran"],
        "PialaDunia2026": ["piala dunia", "world cup", "juara bola dunia"],
        "LigaIndonesia": ["super league", "liga 1", "liga indonesia", "persija", "persib", "arema"],
        "BolaInternasional": ["premier league", "la liga", "serie a", "bundesliga", "ligue 1", "champions"],
        "MotoGP": ["moto gp", "motogp", "marc marquez", "balap"],
        "Transfer": ["transfer", "bursa", "resmi", "kontrak"],
    }
    for tag_name, keywords in tag_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(tag_name)
    if not tags:
        tags.append("Olahraga")
    return tags


def fetch_rss(url: str) -> list[dict]:
    logger.info("抓取 RSS: %s", url)
    articles = []
    try:
        resp = httpx.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
                "Accept": "application/rss+xml, application/xml, text/xml",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("HTTP %s", resp.status_code)
            return articles

        root = ElementTree.fromstring(resp.text)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}

        for item in root.iter("item"):
            title = ""
            link = ""
            pub_date = ""
            description = ""
            image_url = ""

            for child in item:
                tag = child.tag.lower() if "}" not in child.tag else child.tag.split("}")[1].lower()
                text = (child.text or "").strip()
                if tag == "title":
                    title = text
                elif tag == "link":
                    link = text if text else child.get("href", "")
                elif tag == "pubdate":
                    pub_date = text
                elif tag == "description":
                    description = strip_html(text)
                elif tag == "enclosure":
                    image_url = child.get("url", "")

            for encoded in item.findall("content:encoded", ns):
                description = strip_html(encoded.text or "")

            if not description:
                description = ""

            if not title or len(title) < 10:
                continue

            link_lower = link.lower()
            if not any(cat in link_lower for cat in INTEREST_CATEGORIES):
                continue

            summary = description[:300] + "..." if len(description) > 300 else description
            articles.append({
                "title": title,
                "url": link,
                "source": "Detik Sport",
                "pub_date": pub_date,
                "summary": summary,
                "image_url": image_url,
                "description": description,
            })

    except Exception as e:
        logger.error("RSS 抓取异常: %s", e)

    logger.info("获取 %d 条", len(articles))
    return articles


def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    summary = _e(item.get("summary", ""))
    url = _e(item["url"])

    parts = [
        '<div style="background:#e8f5e9;padding:12px 16px;border-radius:8px;'
        'margin:0 0 14px;border-left:4px solid #2e7d32;">'
        '<p style="font-weight:bold;color:#1b5e20;margin:0 0 6px;">'
        f'<span style="background:#2e7d32;color:#fff;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;margin-right:6px;">DETIK</span>'
        f'Detik Sport</p>'
        f'<p style="font-size:16px;color:#1b5e20;line-height:1.6;margin:0;">{title}</p>'
        '</div>',
    ]

    if summary:
        parts.append(
            '<div style="padding:0 4px;">'
            f'<p style="font-size:14px;line-height:1.8;color:#333;'
            f'margin:8px 0;">{summary}</p>'
            '</div>'
        )

    if url:
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener" '
            'style="color:#2e7d32;text-decoration:none;font-weight:bold;font-size:13px;">'
            'Selengkapnya di Detik Sport →</a>'
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
    logger.info("=== Detik Sport 新闻抓取 ===")

    all_articles = []
    for rss_url in RSS_URLS:
        articles = fetch_rss(rss_url)
        all_articles.extend(articles)

    all_articles = deduplicate(all_articles)
    logger.info("去重后 %d 条", len(all_articles))

    if not all_articles:
        logger.warning("无内容")
        return

    if len(all_articles) > max_items:
        all_articles = all_articles[:max_items]

    print("\n" + "=" * 60)
    print("  Detik Sport 新闻")
    print("=" * 60)
    for i, art in enumerate(all_articles):
        print(f"\n[{i + 1}] {art['title'][:100]}")
        print(f"  {art['url']}")
        if art.get("pub_date"):
            print(f"  时间: {art['pub_date']}")
        if art.get("image_url"):
            print(f"  图片: {art['image_url'][:80]}")

    if save:
        author_id = get_author_id()
        category_id = get_category_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        for art in all_articles:
            content = build_post_html(art)
            tags = TAGS_DEFAULT + extract_tags_from_text(
                art["title"], art.get("summary", "")
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

        logger.info("[入库] %d/%d 条", saved, len(all_articles))

    logger.info("=== 完成 ===")


def main():
    parser = argparse.ArgumentParser(description="Detik Sport 新闻抓取")
    parser.add_argument("--save", action="store_true", help="写入数据库")
    parser.add_argument("--max", type=int, default=15, help="最大条目数")
    args = parser.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
