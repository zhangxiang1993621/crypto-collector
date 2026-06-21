"""OSL Indonesia 公告抓取脚本 (Playwright)

OSL 使用 Nuxt.js SPA，需 Playwright 渲染 JS 后才能获取公告列表和详情。

用法：
    python osl_scraper/osl_scraper.py --max 5
    python osl_scraper/osl_scraper.py --save --max 10
"""

import os, sys, re, random, logging, argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

BASE_URL = "https://www.osl.com"
ANNOUNCE_URL = f"{BASE_URL}/en-id/announcement"


def get_cat_id() -> str:
    name = os.environ.get("OSL_CATEGORY_NAME", "news")
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


def scrape_osl(max_items: int = 20) -> list[dict]:
    logger.info("=== OSL Indonesia 公告抓取 (Playwright) ===")
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()

        logger.info(f"访问 {ANNOUNCE_URL} ...")
        try:
            page.goto(ANNOUNCE_URL, wait_until="networkidle", timeout=30000)
        except PlaywrightTimeout:
            logger.warning("列表页加载超时")
            browser.close()
            return results

        # 等待公告链接出现
        try:
            page.wait_for_selector('a[href*="/en-id/announcement/"]', timeout=15000)
        except PlaywrightTimeout:
            logger.warning("未找到公告链接")
            browser.close()
            return results

        # 获取所有公告链接
        link_elements = page.query_selector_all('a[href*="/en-id/announcement/"]')
        items: list[dict] = []
        seen_slugs = set()
        for el in link_elements:
            href = el.get_attribute("href") or ""
            if not href or "/en-id/announcement/" not in href:
                continue
            slug = href.rstrip("/").split("/")[-1]
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            title = el.inner_text().strip()
            if len(title) < 10:
                continue
            items.append({"slug": slug, "title": title, "url": BASE_URL + href})

        logger.info(f"列表页解析 {len(items)} 条公告")

        # 抓取详情
        for item in items[:max_items]:
            try:
                detail = {"full_text": "", "date_str": "", "image_url": ""}
                page.goto(item["url"], wait_until="networkidle", timeout=25000)
                page.wait_for_timeout(1000)  # 额外等待渲染

                # 提取正文
                article = page.query_selector("article")
                if article:
                    detail["full_text"] = article.inner_text()[:3000]
                else:
                    body = page.query_selector("main") or page.query_selector("body")
                    if body:
                        detail["full_text"] = body.inner_text()[:2000]

                # 提取图片
                img = page.query_selector("article img, main img")
                if img:
                    detail["image_url"] = img.get_attribute("src") or ""

                # 提取日期
                detail["date_str"] = ""
                date_el = page.query_selector("time")
                if date_el:
                    detail["date_str"] = date_el.inner_text().strip()

                summary = detail["full_text"][:500] if detail["full_text"] else ""

                results.append({
                    "title": item["title"],
                    "url": item["url"],
                    "summary": summary,
                    "full_text": detail["full_text"],
                    "image_url": detail["image_url"],
                    "date_str": detail["date_str"],
                })
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
    new_items = [item for item in items if item["title"][:200] not in existing]
    skipped = len(items) - len(new_items)
    if skipped:
        logger.info(f"  跳过 {skipped} 篇已存在")
    return new_items


def build_post_html(item: dict) -> str:
    title, summary, url, img, date = map(_e, [item["title"], item.get("summary", ""), item.get("url", ""), item.get("image_url", ""), item.get("date_str", "")])
    parts = [
        '<div style="background:#e3f2fd;padding:14px 16px;border-radius:10px;margin:0 0 14px;border-left:4px solid #1976d2;">'
        '<p style="font-weight:bold;color:#0d47a1;margin:0 0 4px;">🇮🇩 OSL Indonesia</p>'
    ]
    if date:
        parts.append(f'<p style="font-size:11px;color:#999;margin:0 0 6px;">{date}</p>')
    parts.append(f'<p style="font-size:16px;color:#1a1a1a;line-height:1.6;margin:0;">{title}</p></div>')
    if img:
        parts.append(f'<div style="text-align:center;margin:0 0 10px;"><img src="{img}" style="max-width:100%;border-radius:8px;max-height:400px;" /></div>')
    if summary:
        parts.append(f'<div style="padding:0 4px;"><p style="font-size:14px;line-height:1.8;color:#444;margin:8px 0;">{summary}</p></div>')
    if url:
        parts.append(f'<p style="margin:8px 0;"><a href="{url}" target="_blank" rel="noopener" style="display:inline-block;background:#1976d2;color:#fff;padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;">🔗 Read full announcement →</a></p>')
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
    items = deduplicate(scrape_osl(max_items=max_items))
    logger.info(f"去重后共 {len(items)} 条")
    if not items:
        return
    if max_items and len(items) > max_items:
        items = items[:max_items]
    print("\n" + "=" * 60 + "\n  OSL Indonesia 公告\n" + "=" * 60)
    for i, item in enumerate(items):
        print(f"\n[{i + 1}] {item['title'][:120]}\n  {item.get('url', '')}")
    if save:
        cat_id = get_cat_id()
        now = datetime.now(timezone.utc).isoformat()
        new_items = filter_new_only(items, cat_id)
        if not new_items:
            logger.info("无新公告")
            return
        saved = 0
        for item in new_items:
            bot = get_random_bot()
            content = build_post_html(item)
            try:
                result = insert_one("posts", {"title": item["title"][:200], "content": content, "author_id": bot["id"], "category_id": cat_id, "status": "pending_review", "created_at": now, "updated_at": now}, returning="id")
                sync_tags(result["id"], ["OSL", "Indonesia", "Kripto", "Announcement"])
                saved += 1
                logger.info(f"  [入库] [{bot['username']}] {item['title'][:50]}...")
            except Exception as e:
                logger.error(f"  入库失败: {e}")
        logger.info(f"[入库] {saved}/{len(new_items)} 条")
    logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="OSL Indonesia 公告抓取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=10, help="最大条目数")
    run(save=p.parse_args().save, max_items=p.parse_args().max)


if __name__ == "__main__":
    main()
