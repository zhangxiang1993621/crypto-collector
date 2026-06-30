"""Mainbasket.com IBL 篮球新闻抓取 + 发帖

功能：从 mainbasket.com/c/5/berita/ibl 抓取印尼IBL联赛新闻
用法：
    python mainbasket_scraper.py                         # 抓取并打印预览
    python mainbasket_scraper.py --save                  # 抓取并直接入库
    python mainbasket_scraper.py --save --max 10         # 最多10条
"""

import os
import re
import sys
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

IBL_URL = "https://www.mainbasket.com/c/5/berita/ibl"

TAGS_DEFAULT = ["Mainbasket", "IBL", "Basket", "Indonesia"]


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


def extract_tags_from_text(title: str, desc: str) -> list[str]:
    combined = (title + " " + desc).lower()
    tags = []
    tag_keywords = {
        "IBL": ["ibl", "indonesia basketball league"],
        "Final": ["final", "juara", "gim", "game"],
        "MVP": ["mvp", "player of"],
        "PelitaJaya": ["pelita jaya"],
        "Hornbills": ["hornbills"],
        "SatriaMuda": ["satria muda"],
        "Prawira": ["prawira"],
        "DewaUnited": ["dewa united"],
        "Timnas": ["timnas", "tim nasional", "indonesia"],
        "Transfer": ["transfer", "kontrak", "pindah", "bursa"],
    }
    for tag_name, keywords in tag_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(tag_name)
    if not tags:
        tags.append("Basket")
    return tags


def fetch_articles(max_pages: int = 3) -> list[dict]:
    logger.info("抓取 IBL 新闻: %s", IBL_URL)
    articles = []
    seen_urls = set()

    for page_num in range(1, max_pages + 1):
        url = IBL_URL if page_num == 1 else f"{IBL_URL}?page={page_num}"
        logger.info("第 %d 页: %s", page_num, url)

        try:
            resp = httpx.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=30,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.warning("HTTP %s", resp.status_code)
                continue

            html = resp.text

            post_pattern = re.compile(
                r'<a\s[^>]*href="(https://www\.mainbasket\.com/r/\d+/[^"]+)"[^>]*>'
                r'([^<]{10,200})</a>'
                r'.*?'
                r'<span class="post-date">([^<]+)</span>'
                r'.*?'
                r'<div class="no-padding post-body">\s*([^<]+)',
                re.DOTALL,
            )
            matches = post_pattern.findall(html)

            for url_val, title, date, body in matches:
                url_val = url_val.strip()
                title = strip_html(title).strip()
                date = date.strip()
                body = strip_html(body).strip()

                if not title or len(title) < 10:
                    continue
                if url_val in seen_urls:
                    continue
                seen_urls.add(url_val)

                articles.append({
                    "title": title,
                    "url": url_val,
                    "source": "Mainbasket",
                    "date": date,
                    "description": body,
                })

            logger.info("第 %d 页获取 %d 条", page_num, len(matches))

        except Exception as e:
            logger.error("抓取异常: %s", e)

    logger.info("总计 %d 条", len(articles))
    return articles


def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    url = _e(item["url"])

    parts = [
        '<div style="background:#fff8e1;padding:12px 16px;border-radius:8px;'
        'margin:0 0 14px;border-left:4px solid #ff8f00;">'
        '<p style="font-weight:bold;color:#e65100;margin:0 0 6px;">'
        '<span style="background:#ff8f00;color:#fff;padding:2px 8px;border-radius:4px;'
        'font-size:11px;margin-right:6px;">🏀</span>'
        'Mainbasket · IBL</p>'
        f'<p style="font-size:16px;color:#bf360c;line-height:1.6;margin:0;">{title}</p>'
        '</div>',
    ]

    if item.get("description"):
        parts.append(
            f'<p style="font-size:14px;line-height:1.8;color:#555;margin:6px 4px;">'
            f'{_e(item["description"])}</p>'
        )

    if item.get("date"):
        parts.append(
            f'<p style="color:#888;font-size:12px;margin:4px 4px;">{_e(item["date"])}</p>'
        )

    if url:
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener" '
            'style="color:#ff8f00;text-decoration:none;font-weight:bold;font-size:13px;">'
            'Baca selengkapnya di Mainbasket →</a>'
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
    logger.info("=== Mainbasket IBL 新闻抓取 ===")

    articles = fetch_articles()
    articles = deduplicate(articles)

    if not articles:
        logger.warning("无内容")
        return

    if len(articles) > max_items:
        articles = articles[:max_items]

    print("\n" + "=" * 60)
    print("  Mainbasket IBL 新闻")
    print("=" * 60)
    for i, art in enumerate(articles):
        time_str = f" ({art.get('date', '')})" if art.get("date") else ""
        print(f"\n[{i + 1}] {art['title'][:100]}{time_str}")
        print(f"  {art['url']}")
        if art.get("description"):
            print(f"  {art['description'][:120]}")

    if save:
        author_id = get_author_id()
        category_id = get_category_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        for art in articles:
            content = build_post_html(art)
            tags = TAGS_DEFAULT + extract_tags_from_text(
                art["title"], art.get("description", "")
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
    parser = argparse.ArgumentParser(description="Mainbasket IBL 新闻抓取")
    parser.add_argument("--save", action="store_true", help="写入数据库")
    parser.add_argument("--max", type=int, default=20, help="最大条目数")
    args = parser.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
