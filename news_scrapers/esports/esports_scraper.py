"""印尼电子竞技新闻爬取 + 发帖 (DuniaGames)

功能：从 duniagames.co.id/discover/category/event 抓取印尼电子竞技活动新闻，
      以 indoAdmin 机器人身份发布到 E-Sports 分类。

用法：
    python esports_scraper/esports_scraper.py                  # 仅抓取预览
    python esports_scraper/esports_scraper.py --save           # 抓取并入库
    python esports_scraper/esports_scraper.py --save --max 10  # 最多10条
"""

import os
import re
import sys
import json
import random
import logging
import argparse
from pathlib import Path

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

# ────────────────────── 配置 ──────────────────────

DUNIAGAMES_CATEGORY_SLUG = "event"
DUNIAGAMES_LIST_API = "https://api.duniagames.co.id/api/content-article/v1/article"
DUNIAGAMES_ARTICLE_BASE = "https://duniagames.co.id/discover/article"

API_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "ciam-type": "FR",
    "accept-language": "id",
    "referer": "https://duniagames.co.id/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

# ────────────────────── 工具（直连 PostgreSQL） ──────────────────────


def get_cat_id() -> str:
    name = os.environ.get("ESPers_CATEGORY_NAME", "E-Sports")
    row = select_one("categories", {"name": name}, columns="id")
    if not row:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    return row["id"]


def get_random_indo_admin() -> dict:
    """从 indoAdmin 用户中随机抽取发帖人"""
    rows = select_all("profiles", {}, columns="id,username")
    indo_admin_rows = [r for r in rows if "indoAdmin" in r.get("username", "")]
    if not indo_admin_rows:
        # 回退：使用普通机器人
        bot_rows = [r for r in rows if r.get("is_bot")]
        if not bot_rows:
            logger.error("无可用发帖人")
            sys.exit(1)
        return random.choice(bot_rows)
    return random.choice(indo_admin_rows)


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text


# ────────────────────── 抓取列表 ──────────────────────

def fetch_article_list(max_page: int = 3, per_page: int = 10) -> list[dict]:
    """通过 API 获取活动文章列表"""
    all_articles = []

    for page in range(1, max_page + 1):
        logger.info(f"  获取第 {page} 页...")
        try:
            resp = httpx.get(
                DUNIAGAMES_LIST_API,
                params={
                    "slug": DUNIAGAMES_CATEGORY_SLUG,
                    "limit": per_page,
                    "page": page,
                    "status": "published",
                },
                headers=API_HEADERS,
                timeout=30,
            )

            if resp.status_code != 200:
                logger.warning(f"    API HTTP {resp.status_code}")
                continue

            data = resp.json()
            articles = data.get("data", [])
            if not articles:
                break

            for a in articles:
                all_articles.append({
                    "source_id": str(a.get("id", "")),
                    "title": a.get("title", ""),
                    "slug": a.get("slug", ""),
                    "summary": a.get("shortDesc", ""),
                    "image": a.get("image", ""),
                    "author_name": a.get("authorName", ""),
                    "published_at": a.get("publishedDate", ""),
                    "url": f"{DUNIAGAMES_ARTICLE_BASE}/{a.get('slug', '')}",
                })

            meta = data.get("meta", {})
            total_pages = meta.get("total_page", 0)
            if page >= total_pages:
                break

        except Exception as e:
            logger.error(f"    API 异常: {e}")
            break

    logger.info(f"  共获取 {len(all_articles)} 篇文章")
    return all_articles


# ────────────────────── 抓取文章详情 ──────────────────────

def fetch_article_detail(page: "Page", article: dict) -> dict:
    """用 Playwright 访问文章详情页，提取正文和标签"""
    url = article["url"]
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # 等待文章内容加载
        page.wait_for_selector("article", timeout=10000)

        # 提取正文段落
        paragraphs = page.eval_on_selector_all(
            "article p",
            "els => els.map(el => el.textContent.trim()).filter(t => t.length > 0)"
        )
        content = "\n".join(paragraphs) if paragraphs else ""

        # 提取标签
        tags = page.eval_on_selector_all(
            'a[href*="/discover/tag/"]',
            "els => [...new Set(els.map(el => el.textContent.trim()).filter(t => t))]"
        )

        # 尝试提取 JSON-LD 元数据
        try:
            jsonld_text = page.evaluate("""() => {
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                for (const s of scripts) {
                    try {
                        const d = JSON.parse(s.textContent);
                        if (d['@type'] === 'Article') return JSON.stringify(d);
                    } catch(e) {}
                }
                return '';
            }""")
            if jsonld_text:
                jsonld = json.loads(jsonld_text)
                article["meta_image"] = jsonld.get("image", "")
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"    详情页访问失败: {e}")
        content = ""

    article["content"] = content
    article["tags"] = tags if tags else []
    return article


# ────────────────────── HTML 构建 ──────────────────────

def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    summary = _e(item.get("summary", ""))
    url = _e(item["url"])
    image = _e(item.get("image", ""))
    author = _e(item.get("author_name", "DuniaGames"))

    parts = [
        '<div style="background:#1a1a2e;padding:14px 16px;border-radius:12px;'
        'margin:0 0 14px;border:1px solid #e94560;">'
        f'<p style="color:#e94560;font-weight:bold;font-size:12px;margin:0 0 8px;">'
        f'🎮 E-Sports · {author}</p>',
    ]

    if image:
        parts.append(
            f'<img src="{image}" alt="{title}" '
            'style="width:100%;border-radius:8px;margin-bottom:10px;" />'
        )

    parts.append(
        f'<p style="font-size:16px;font-weight:bold;color:#fff;line-height:1.5;'
        f'margin:0 0 8px;">{title}</p>'
    )

    if summary:
        parts.append(
            f'<p style="font-size:13px;color:#ccc;line-height:1.6;margin:0 0 10px;">'
            f'{truncate(summary, 300)}</p>'
        )

    if url:
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener" '
            'style="display:inline-block;background:#e94560;color:#fff;'
            'padding:6px 14px;border-radius:20px;text-decoration:none;font-size:12px;'
            'font-weight:bold;">🔗 Baca Selengkapnya →</a>'
        )

    parts.append('</div>')
    return "\n".join(parts)


# ────────────────────── 标签（直连 PostgreSQL） ──────────────────────

def sync_tags(post_id: str, tags: list[str]) -> None:
    if not tags:
        return
    unique = list(set(tags))
    em = {}
    if unique:
        placeholders = ", ".join(["%s"] * len(unique))
        sql = f'SELECT id, name FROM tags WHERE name IN ({placeholders})'
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


# ────────────────────── 去重（直连 PostgreSQL） ──────────────────────

def deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = item["title"].lower().strip()[:80]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def check_existing_posts(cat_id: str, items: list[dict]) -> list[dict]:
    """检查数据库中是否已有相同标题的帖子"""
    if not items:
        return items

    titles = [item["title"][:200] for item in items]
    placeholders = ", ".join(["%s"] * len(titles))
    sql = f'SELECT title FROM posts WHERE category_id = %s AND title IN ({placeholders})'
    rows = execute_sql(sql, (cat_id, *titles))
    existing_titles = {d["title"] for d in rows} if rows else set()
    new_items = [item for item in items if item["title"][:200] not in existing_titles]
    skipped = len(items) - len(new_items)
    if skipped:
        logger.info(f"  跳过 {skipped} 篇已存在的文章")
    return new_items


# ────────────────────── 主流程 ──────────────────────

def run(save: bool = False, max_items: int = 10):
    logger.info("=== 印尼电子竞技新闻抓取 (DuniaGames) ===")

    # ── 第一步：获取文章列表 ──
    articles = fetch_article_list(max_page=2, per_page=10)
    articles = deduplicate(articles)

    if len(articles) > max_items:
        articles = articles[:max_items]

    if not articles:
        logger.warning("无内容")
        return

    # ── 第二步：打印预览 ──
    print("\n" + "=" * 60)
    print("  印尼电子竞技新闻 (DuniaGames Event)")
    print("=" * 60)
    for i, art in enumerate(articles):
        print(f"\n[{i + 1}] {art['title'][:120]}")
        print(f"  {art['url']}")
        if art.get("summary"):
            print(f"  {art['summary'][:100]}")

    if save:
        # ── 第三步：连接数据库 ──
        cat_id = get_cat_id()

        # ── 第四步：去重（检查已有帖子）──
        articles = check_existing_posts(cat_id, articles)
        if not articles:
            logger.info("无新文章需要发布")
            return

        # ── 第五步：初始化 Playwright ──
        from playwright.sync_api import sync_playwright

        now_iso = None
        saved = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            for art in articles:
                # 获取详情
                art = fetch_article_detail(page, art)

                # 选发帖人
                bot = get_random_indo_admin()
                if now_iso is None:
                    from datetime import datetime, timezone
                    now_iso = datetime.now(timezone.utc).isoformat()

                # 构建 HTML
                content = build_post_html(art)

                # 组合标签：原文标签 + 固定标签
                tags = art.get("tags", [])
                tags.extend(["E-Sports", "Indonesia", "DuniaGames"])

                try:
                    result = insert_one("posts", {
                        "title": art["title"][:200],
                        "content": content,
                        "author_id": bot["id"],
                        "category_id": cat_id,
                        "status": "pending_review",
                        "created_at": now_iso,
                        "updated_at": now_iso,
                    }, returning="id")
                    sync_tags(result["id"], tags)
                    saved += 1
                    logger.info(f"  [入库] [{bot['username']}] {art['title'][:50]}...")
                except Exception as e:
                    logger.error(f"  入库失败: {e}")

            browser.close()

        logger.info(f"[入库] {saved}/{len(articles)} 条")
        logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="印尼电子竞技新闻抓取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=10, help="最大条目数")
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
