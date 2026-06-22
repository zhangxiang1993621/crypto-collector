"""Mobee 加密新闻抓取脚本

Mobee 是印尼持牌加密交易所，使用 Webflow CMS 构建。
新闻可从 market-update 列表页直接解析。

用法：
    python mobee_scraper/mobee_scraper.py --max 5
    python mobee_scraper/mobee_scraper.py --save --max 10
"""

import os, sys, re, random, logging, argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

NEWS_URL = "https://mobee.com/en/mobee-academy/market-update"
BASE_URL = "https://mobee.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"}


def get_cat_id() -> str:
    name = os.environ.get("MOBEE_CATEGORY_NAME") or "Hot Tokens"
    row = select_one("categories", {"name": name}, columns="id")
    if not row:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    return row["id"]


def get_random_bot() -> dict:
    username = os.environ.get("POSTS_AUTHOR_USERNAME") or "indoAdmin"
    rows = select_all("profiles", {"username": username, "is_bot": True}, columns="id,username")
    if not rows:
        logger.error("发帖账号 %s 不存在或未设为机器人", username)
        sys.exit(1)
    return rows[0]


def strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fetch_news_list() -> list[dict]:
    logger.info("抓取 Mobee market-update 列表...")
    try:
        resp = httpx.get(NEWS_URL, headers=HEADERS, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            logger.error(f"HTTP {resp.status_code}")
            return []
        html = resp.text
    except Exception as e:
        logger.error(f"请求失败: {e}")
        return []

    items = []
    # Webflow 文章区块: <a href="/en/mobee-academy/market-update/{slug}">Title</a>
    # 日期格式: June 15, 2026 / May 28, 2026
    pattern = re.compile(
        r'href="(/en/mobee-academy/market-update/[^"]+)"[^>]*>',
        re.DOTALL,
    )
    links = pattern.findall(html)
    seen = set()
    for href in links:
        if href in seen:
            continue
        seen.add(href)
        if href == "/en/mobee-academy/market-update":
            continue

        # 提取标题：紧跟着链接后面的文本
        full_url = BASE_URL + href
        items.append({
            "url": full_url,
            "slug": href.split("/")[-1],
        })

    logger.info(f"列表页解析 {len(items)} 条")
    return items


def fetch_article_detail(url: str) -> dict:
    detail = {"title": "", "full_text": "", "date_str": "", "image_url": ""}
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return detail
        html = resp.text

        # 标题: 通常在 <h1> 或 <h2> 或 og:title
        title_m = re.search(r'<title>([^<]+)</title>', html)
        if title_m:
            detail["title"] = strip_html(title_m.group(1)).split("|")[0].strip()

        if not detail["title"]:
            h1_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            if h1_m:
                detail["title"] = strip_html(h1_m.group(1)).strip()

        # 正文
        body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
        if body_m:
            text = strip_html(body_m.group(1))
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r" {2,}", " ", text)
            detail["full_text"] = text.strip()[:3000]

        # 日期
        date_m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}', html)
        if date_m:
            detail["date_str"] = date_m.group(0)

        # 图片: og:image 或 article img
        img_m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
        if img_m:
            detail["image_url"] = img_m.group(1)
        else:
            img_m = re.search(r'<img[^>]*src="(https://cdn\.prod\.webflow-files\.com[^"]+)"', html)
            if img_m:
                detail["image_url"] = img_m.group(1)

    except Exception as e:
        logger.warning(f"抓取详情失败: {e}")

    return detail


def scrape_mobee(max_items: int = 20) -> list[dict]:
    items = fetch_news_list()
    results = []
    for item in items:
        if len(results) >= max_items:
            break
        detail = fetch_article_detail(item["url"])
        title = detail["title"] or item["slug"].replace("-", " ").title()
        if len(title) < 5:
            continue
        results.append({
            "title": title,
            "url": item["url"],
            "summary": detail["full_text"][:500] if detail["full_text"] else "",
            "full_text": detail["full_text"],
            "image_url": detail["image_url"],
            "date_str": detail["date_str"],
        })
    return results


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
    existing = {r["title"] for r in rows} if rows else set()
    return [item for item in items if item["title"][:200] not in existing]


def build_post_html(item: dict) -> str:
    title, summary, url, img, date = map(_e, [item["title"], item.get("summary", ""), item.get("url", ""), item.get("image_url", ""), item.get("date_str", "")])
    parts = [
        '<div style="background:#fce4ec;padding:14px 16px;border-radius:10px;margin:0 0 14px;border-left:4px solid #e91e63;">'
        '<p style="font-weight:bold;color:#880e4f;margin:0 0 4px;">🇮🇩 Mobee News</p>'
    ]
    if date:
        parts.append(f'<p style="font-size:11px;color:#999;margin:0 0 6px;">{date}</p>')
    parts.append(f'<p style="font-size:16px;color:#1a1a1a;line-height:1.6;margin:0;">{title}</p></div>')
    if img:
        parts.append(f'<div style="text-align:center;margin:0 0 10px;"><img src="{img}" style="max-width:100%;border-radius:8px;max-height:400px;" /></div>')
    if summary:
        parts.append(f'<div style="padding:0 4px;"><p style="font-size:14px;line-height:1.8;color:#444;margin:8px 0;">{summary}</p></div>')
    if url:
        parts.append(f'<p style="margin:8px 0;"><a href="{url}" target="_blank" rel="noopener" style="display:inline-block;background:#e91e63;color:#fff;padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;">🔗 Read more →</a></p>')
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
    for name in [n for n in unique if n not in em]:
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


def run(save: bool = False, max_items: int = 10):
    logger.info("=== Mobee 新闻抓取 ===")
    items = deduplicate(scrape_mobee(max_items=max_items))
    logger.info(f"去重后共 {len(items)} 条")
    if not items:
        return
    if max_items and len(items) > max_items:
        items = items[:max_items]
    print("\n" + "=" * 60 + "\n  Mobee 新闻\n" + "=" * 60)
    for i, item in enumerate(items):
        print(f"\n[{i + 1}] {item['title'][:120]}\n  {item.get('url','')}")
    if save:
        cat_id = get_cat_id()
        now = datetime.now(timezone.utc).isoformat()
        new_items = filter_new_only(items, cat_id)
        if not new_items:
            return
        saved = 0
        for item in new_items:
            bot = get_random_bot()
            try:
                result = insert_one("posts", {"title": item["title"][:200], "content": build_post_html(item), "author_id": bot["id"], "category_id": cat_id, "post_type": "info", "status": "pending_review", "created_at": now, "updated_at": now}, returning="id")
                sync_tags(result["id"], ["Mobee", "Indonesia", "Kripto", "News"])
                saved += 1
                logger.info(f"  [入库] [{bot['username']}] {item['title'][:50]}...")
            except Exception as e:
                logger.error(f"  入库失败: {e}")
        logger.info(f"[入库] {saved}/{len(new_items)} 条")
    logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="Mobee 新闻抓取")
    p.add_argument("--save", action="store_true")
    p.add_argument("--max", type=int, default=10)
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
