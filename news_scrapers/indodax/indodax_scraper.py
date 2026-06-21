"""Indodax 博客新闻抓取脚本 (印尼最大本土交易所)

功能：通过 WordPress REST API 抓取 Indodax 官方博客文章，
      以机器人身份发布到 forum 对应分类。

数据源：
  - API: https://blog.indodax.com/wp-json/wp/v2/posts
  - 分类: https://blog.indodax.com/wp-json/wp/v2/categories

用法：
    python indodax_scraper/indodax_scraper.py                  # 仅抓取打印
    python indodax_scraper/indodax_scraper.py --save           # 抓取并入库
    python indodax_scraper/indodax_scraper.py --save --max 10  # 最多10条
"""

import os
import sys
import re
import random
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
# 直连数据库（绕过 REST API 作业限制）
from db_direct import select_one, select_all, insert_one, execute_sql

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

WP_API_POSTS = "https://blog.indodax.com/wp-json/wp/v2/posts"
WP_API_CATEGORIES = "https://blog.indodax.com/wp-json/wp/v2/categories"
PER_PAGE = 30
MAX_PAGES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
    ),
}


def get_cat_id() -> str:
    name = os.environ.get("INDODAX_CATEGORY_NAME") or "news"
    row = select_one("categories", {"name": name}, columns="id")
    if not row:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    return row["id"]


def get_random_bot() -> dict:
    rows = select_all("profiles", {"is_bot": True}, columns="id,username")
    if not rows:
        logger.error("无可用机器人")
        sys.exit(1)
    return random.choice(rows)


def strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ────────────────────── 抓取 WP API ──────────────────────


def fetch_indodax_news(max_items: int = 30) -> list[dict]:
    logger.info("抓取 Indodax Blog 新闻...")

    cat_map: dict[int, str] = {}
    try:
        resp = httpx.get(f"{WP_API_CATEGORIES}?per_page=100", timeout=20, headers=HEADERS)
        if resp.status_code == 200:
            for cat in resp.json():
                cat_map[cat["id"]] = cat["name"]
        logger.info(f"  获取 {len(cat_map)} 个分类")
    except Exception as e:
        logger.warning(f"  获取分类列表失败: {e}")

    all_items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        if len(all_items) >= max_items:
            break
        try:
            resp = httpx.get(
                f"{WP_API_POSTS}?per_page={PER_PAGE}&page={page}&_embed=true",
                timeout=30,
                headers=HEADERS,
            )
            if resp.status_code != 200:
                logger.warning(f"  第 {page} 页 HTTP {resp.status_code}，停止翻页")
                break
            posts = resp.json()
            if not posts:
                break

            for post in posts:
                title = strip_html(post.get("title", {}).get("rendered", "")).strip()
                if not title:
                    continue

                excerpt = strip_html(post.get("excerpt", {}).get("rendered", "")).strip()
                link = post.get("link", "")
                date_str = post.get("date", "")

                author_name = ""
                embedded_author = post.get("_embedded", {}).get("author", [])
                if embedded_author:
                    author_name = embedded_author[0].get("name", "")

                category_names = []
                for cid in post.get("categories", []):
                    name = cat_map.get(cid, "")
                    if name:
                        category_names.append(name)

                image_url = ""
                media = post.get("_embedded", {}).get("wp:featuredmedia", [])
                if media:
                    sizes = media[0].get("media_details", {}).get("sizes", {})
                    for size_key in ("large", "medium", "full"):
                        src = sizes.get(size_key, {}).get("source_url", "")
                        if src:
                            image_url = src
                            break
                    if not image_url:
                        image_url = media[0].get("source_url", "")

                all_items.append({
                    "wp_id": post.get("id"),
                    "title": title,
                    "excerpt": excerpt,
                    "link": link,
                    "date": date_str,
                    "author": author_name,
                    "image_url": image_url,
                    "category_names": category_names,
                })

            logger.info(f"  第 {page} 页: 抓取 {len(posts)} 条")
        except Exception as e:
            logger.warning(f"  第 {page} 页抓取失败: {e}")
            continue

    logger.info(f"共抓取 {len(all_items)} 条 Indodax 新闻")
    return all_items


# ────────────────────── 去重 ──────────────────────


def deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = item["title"].lower().strip()[:100]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def filter_new_only(items: list[dict], cat_id: str) -> list[dict]:
    if not items:
        return items
    titles = [item["title"][:200] for item in items]
    placeholders = ", ".join(["%s"] * len(titles))
    sql = f"SELECT title FROM posts WHERE category_id = %s AND title IN ({placeholders})"
    rows = execute_sql(sql, (cat_id, *titles))
    existing_titles = {r["title"] for r in rows} if rows else set()
    new_items = [item for item in items if item["title"][:200] not in existing_titles]
    skipped = len(items) - len(new_items)
    if skipped:
        logger.info(f"  跳过 {skipped} 篇已存在的文章")
    return new_items


# ────────────────────── HTML 构建 ──────────────────────


def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    excerpt = _e(item.get("excerpt", ""))
    link = _e(item.get("link", ""))
    author = _e(item.get("author", ""))
    image_url = _e(item.get("image_url", ""))
    category_names = item.get("category_names", [])

    cat_badges = ""
    if category_names:
        cat_badges = " ".join(
            f'<span style="display:inline-block;background:#e8eaf6;color:#283593;'
            f'padding:2px 10px;border-radius:12px;font-size:11px;margin-right:4px;">'
            f'{_e(c)}</span>'
            for c in category_names[:3]
        )

    parts = [
        '<div style="background:#e8eaf6;padding:14px 16px;border-radius:10px;'
        'margin:0 0 14px;border-left:4px solid #283593;">'
        f'<p style="font-weight:bold;color:#1a237e;margin:0 0 4px;">'
        f'🇮🇩 Indodax Blog</p>'
    ]

    if cat_badges:
        parts.append(f'<div style="margin:0 0 8px;">{cat_badges}</div>')

    parts.append(
        f'<p style="font-size:16px;color:#1a1a1a;line-height:1.6;margin:0 0 6px;">{title}</p>'
        '</div>'
    )

    if image_url:
        parts.append(
            f'<div style="text-align:center;margin:0 0 10px;">'
            f'<img src="{image_url}" alt="{title}" '
            f'style="max-width:100%;border-radius:8px;max-height:400px;" />'
            f'</div>'
        )

    if excerpt:
        parts.append(
            '<div style="padding:0 4px;">'
            f'<p style="font-size:14px;line-height:1.8;color:#444;margin:8px 0;">{excerpt}</p>'
            '</div>'
        )

    if link:
        parts.append(
            f'<p style="margin:8px 0;">'
            f'<a href="{link}" target="_blank" rel="noopener" '
            f'style="display:inline-block;background:#283593;color:#fff;'
            f'padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;">'
            f'🔗 Baca selengkapnya →</a></p>'
        )

    if author:
        parts.append(
            f'<p style="font-size:11px;color:#999;margin:4px 0 0;">'
            f'Penulis: {author}</p>'
        )

    return "\n".join(parts)


# ────────────────────── 标签 ──────────────────────


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


# ────────────────────── 主流程 ──────────────────────


def run(save: bool = False, max_items: int = 20):
    logger.info("=== Indodax Blog 新闻抓取 ===")

    all_items = fetch_indodax_news(max_items=max_items)
    all_items = deduplicate(all_items)
    logger.info(f"去重后共 {len(all_items)} 条")

    if not all_items:
        logger.warning("无内容")
        return

    if max_items and len(all_items) > max_items:
        all_items = all_items[:max_items]

    print("\n" + "=" * 60)
    print("  Indodax Blog 新闻")
    print("=" * 60)
    for i, item in enumerate(all_items):
        cat_str = ", ".join(item.get("category_names", [])[:3])
        print(f"\n[{i + 1}] [{cat_str or 'General'}]")
        print(f"  {item['title'][:120]}")
        if item.get("link"):
            print(f"  {item['link']}")

    if save:
        cat_id = get_cat_id()
        now = datetime.now(timezone.utc).isoformat()

        new_items = filter_new_only(all_items, cat_id)
        if not new_items:
            logger.info("无新文章，跳过入库")
            return

        saved = 0
        for item in new_items:
            bot = get_random_bot()
            content = build_post_html(item)
            tags = ["Indodax", "Indonesia", "Kripto"]
            for cn in item.get("category_names", [])[:2]:
                tags.append(cn)

            try:
                result = insert_one("posts", {
                    "title": item["title"][:200],
                    "content": content,
                    "author_id": bot["id"],
                    "category_id": cat_id,
                    "status": "pending_review",
                    "created_at": now,
                    "updated_at": now,
                }, returning="id")
                sync_tags(result["id"], tags)
                saved += 1
                logger.info(f"  [入库] [{bot['username']}] {item['title'][:50]}...")
            except Exception as e:
                logger.error(f"  入库失败: {e}")

        logger.info(f"[入库] {saved}/{len(new_items)} 条")

    logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="Indodax 博客新闻抓取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=20, help="最大条目数")
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
