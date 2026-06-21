"""Bitget Indonesia 新闻抓取脚本 (Playwright)

Bitget 新闻页为 SPA，需 Playwright 渲染 JS。

用法：
    python bitget_scraper/bitget_scraper.py --max 5
    python bitget_scraper/bitget_scraper.py --save --max 10
"""

import os, sys, random, logging, argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

NEWS_URL = "https://www.bitget.com/id/news"


def get_cat_id() -> str:
    name = os.environ.get("BITGET_CATEGORY_NAME", "news")
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


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def scrape_bitget(max_items: int = 10) -> list[dict]:
    logger.info("=== Bitget Indonesia 新闻抓取 (Playwright) ===")
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()

        logger.info(f"访问 {NEWS_URL} ...")
        try:
            page.goto(NEWS_URL, wait_until="networkidle", timeout=30000)
        except PlaywrightTimeout:
            logger.warning("列表页超时")
            browser.close()
            return results

        page.wait_for_timeout(3000)

        # 获取新闻链接: a[href*="/id/news/detail/"]
        link_els = page.query_selector_all('a[href*="/id/news/detail/"]')
        if not link_els:
            link_els = page.query_selector_all('a[href*="/news/detail/"]')

        items: list[dict] = []
        seen_hrefs = set()
        for el in link_els[:max_items * 2]:
            href = el.get_attribute("href") or ""
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            title = el.inner_text().strip()
            if not title or len(title) < 15:
                continue
            url = "https://www.bitget.com" + href if href.startswith("/") else href
            items.append({"title": title, "url": url})

        logger.info(f"列表页解析 {len(items)} 条")

        for item in items[:max_items]:
            try:
                page.goto(item["url"], wait_until="networkidle", timeout=25000)
                page.wait_for_timeout(1000)

                # 提取正文
                article = page.query_selector("article") or page.query_selector("main") or page.query_selector('[class*="content"]')
                full_text = article.inner_text()[:3000] if article else ""
                summary = full_text[:500] if full_text else ""

                results.append({
                    "title": item["title"],
                    "url": item["url"],
                    "summary": summary,
                    "full_text": full_text,
                })
                logger.info(f"  {item['title'][:50]}...")
            except PlaywrightTimeout:
                logger.warning(f"  详情页超时: {item['title'][:50]}")
            except Exception as e:
                logger.warning(f"  抓取详情失败: {e}")

        browser.close()
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
    title, summary, url = map(_e, [item["title"], item.get("summary", ""), item.get("url", "")])
    parts = [
        '<div style="background:#e8f5e9;padding:14px 16px;border-radius:10px;margin:0 0 14px;border-left:4px solid #4caf50;">'
        '<p style="font-weight:bold;color:#1b5e20;margin:0 0 4px;">🇮🇩 Bitget Indonesia</p>'
        f'<p style="font-size:16px;color:#1a1a1a;line-height:1.6;margin:0;">{title}</p></div>'
    ]
    if summary:
        parts.append(f'<div style="padding:0 4px;"><p style="font-size:14px;line-height:1.8;color:#444;margin:8px 0;">{summary}</p></div>')
    if url:
        parts.append(f'<p style="margin:8px 0;"><a href="{url}" target="_blank" rel="noopener" style="display:inline-block;background:#4caf50;color:#fff;padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;">🔗 Read more →</a></p>')
    return "\n".join(parts)


def sync_tags(post_id: str, tags: list[str]) -> None:
    if not tags:
        return
    unique = list(set(tags))
    em = {}
    if unique:
        placeholders = ", ".join(["%s"] * len(unique))
        rows = execute_sql(f"SELECT id, name FROM tags WHERE name IN ({placeholders})", tuple(unique))
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
            if not select_one("post_tags", {"post_id": post_id, "tag_id": tid}, columns="post_id"):
                insert_one("post_tags", {"post_id": post_id, "tag_id": tid})
        except Exception:
            pass


def run(save: bool = False, max_items: int = 10):
    items = deduplicate(scrape_bitget(max_items=max_items))
    logger.info(f"去重后共 {len(items)} 条")
    if not items:
        return
    print("\n" + "=" * 60 + "\n  Bitget Indonesia 新闻\n" + "=" * 60)
    for i, item in enumerate(items[:max_items]):
        print(f"\n[{i + 1}] {item['title'][:120]}\n  {item.get('url','')}")
    if save:
        cat_id = get_cat_id()
        new_items = filter_new_only(items[:max_items], cat_id)
        if not new_items:
            return
        now = datetime.now(timezone.utc).isoformat()
        saved = 0
        for item in new_items:
            bot = get_random_bot()
            try:
                result = insert_one("posts", {"title": item["title"][:200], "content": build_post_html(item), "author_id": bot["id"], "category_id": cat_id, "status": "pending_review", "created_at": now, "updated_at": now}, returning="id")
                sync_tags(result["id"], ["Bitget", "Indonesia", "Kripto", "News"])
                saved += 1
                logger.info(f"  [入库] [{bot['username']}] {item['title'][:50]}...")
            except Exception as e:
                logger.error(f"  入库失败: {e}")
        logger.info(f"[入库] {saved}/{len(new_items)} 条")
    logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="Bitget Indonesia 新闻抓取")
    p.add_argument("--save", action="store_true")
    p.add_argument("--max", type=int, default=10)
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
