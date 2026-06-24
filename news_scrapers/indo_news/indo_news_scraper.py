"""印尼热点新闻爬取 + 发帖

功能：从 Google News RSS 抓取印度尼西亚每日热点新闻，
      从 trends24.in 抓取 X（Twitter）印尼区实时热搜话题，
      以机器人身份发布到 news 分类。

用法：
    python indo_news_scraper/indo_news_scraper.py                  # 仅抓取预览
    python indo_news_scraper/indo_news_scraper.py --save           # 抓取并入库
    python indo_news_scraper/indo_news_scraper.py --save --max 10  # 最多10条
"""

import os
import re
import sys
import json
import random
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree

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

# ────────────────────── 新闻源 ──────────────────────

NEWS_SOURCES = [
    # Google News RSS - 印尼语 / 印尼地区
    {
        "name": "Google News Indonesia",
        "url": "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "Google News Indonesia (Top)",
        "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFZxYUdjU0FtVnVHZ0pWVXlnQVAB?hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    # 全球印尼相关
    {
        "name": "Google News Indonesia (EN)",
        "url": "https://news.google.com/rss/search?q=Indonesia&hl=en-US&gl=US&ceid=US:en",
        "lang": "en",
    },
    # 社交媒体热点 / 病毒式传播话题 (X/Twitter 等效源)
    {
        "name": "Google News Trending",
        "url": "https://news.google.com/rss/search?q=indonesia+trending+today&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "Google News Viral",
        "url": "https://news.google.com/rss/search?q=viral+indonesia&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "Google News Jakarta",
        "url": "https://news.google.com/rss/search?q=jakarta&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    # ─── X / Twitter 印尼热点 ───
    {
        "name": "X Trending Indonesia",
        "url": "https://news.google.com/rss/search?q=twitter+indonesia+trending&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "X Viral Indonesia",
        "url": "https://news.google.com/rss/search?q=twitter+indonesia+viral&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "X Trending Indonesia (EN)",
        "url": "https://news.google.com/rss/search?q=indonesia+twitter+trending+today&hl=en-US&gl=US&ceid=US:en",
        "lang": "en",
    },
    {
        "name": "X News Indonesia",
        "url": "https://news.google.com/rss/search?q=%22twitter%22+indonesia+news&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    # 印尼社交网络热点综合
    {
        "name": "SocMed Trending",
        "url": "https://news.google.com/rss/search?q=indonesia+sosmed+trending+hari+ini&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "Netizen Indonesia",
        "url": "https://news.google.com/rss/search?q=netizen+indonesia+ramai&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
]

# ────────────────────── 关键词过滤 ──────────────────────
INDO_KEYWORDS = [
    "indonesia", "jakarta", "jokowi", "prabowo", "rupiah",
    "bali", "surabaya", "bandung", "gojek", "tokopedia",
    "pertamina", "garuda", "pln", "ojk", "bank indonesia",
    "idx", "ihsg", "kpu", "dpr", "pdi", "gerindra",
    "freeport", "nusantara", "ikn", "viral", "trending",
    "gempa", "banjir", "macet", "bpjs", "umkm",
]


# ────────────────────── 工具 ──────────────────────


def get_cat_id() -> str:
    name = os.environ.get("INDO_CATEGORY_NAME") or "news"
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
    return re.sub(r'<[^>]+>', '', str(text)).strip()


def truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ────────────────────── 抓取 ──────────────────────

def fetch_rss_news(url: str, source_name: str, lang: str) -> list[dict]:
    """抓取单个 RSS 源"""
    logger.info(f"  [{source_name}] 抓取...")
    articles = []

    try:
        resp = httpx.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }, timeout=30)

        if resp.status_code != 200:
            logger.warning(f"    HTTP {resp.status_code}")
            return articles

        # 解析 RSS
        root = ElementTree.fromstring(resp.text)
        for item in root.iter("item"):
            title = ""
            link = ""
            pub_date = ""
            description = ""

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
                    description = text

            if title and len(title) >= 10:
                articles.append({
                    "title": title,
                    "url": link,
                    "source": source_name,
                    "pub_date": pub_date,
                    "summary": truncate(strip_html(description), 400),
                    "lang": lang,
                })
    except Exception as e:
        logger.error(f"    {source_name} 异常: {e}")

    logger.info(f"    获取 {len(articles)} 条")
    return articles


def match_indonesia(item: dict) -> bool:
    """检查是否与印尼相关（非必须，RSS 源已是印尼频道）"""
    text = (item["title"] + " " + item["summary"]).lower()
    return any(kw in text for kw in INDO_KEYWORDS)


# ────────────────────── X / Twitter 趋势抓取 ──────────────────────

def fetch_x_trends_indonesia() -> list[dict]:
    """抓取 trends24.in 上印尼 X/Twitter 实时热点趋势"""
    logger.info("  [X Trends Indonesia] 抓取 trends24.in ...")
    items = []

    try:
        # trends24.in 提供各国 Twitter 趋势数据
        resp = httpx.get(
            "https://trends24.in/indonesia/",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=20,
            follow_redirects=True,
        )

        if resp.status_code != 200:
            logger.warning(f"    HTTP {resp.status_code}")
            return items

        html = resp.text

        # trends24.in 趋势列表在 <ol class="trend-card__list"> 内
        # 每个趋势卡片格式: <a href="/indonesia/trends/...">#TopicName</a>
        # 或 <span class="trend-name">TopicName</span>

        # 方法1: 匹配趋势链接 /indonesia/trends/
        trend_pattern = re.compile(
            r'/indonesia/trends/[^"]*"[^>]*>\s*#?([^<]{2,80})\s*</a>',
        )
        matches = trend_pattern.findall(html)

        # 方法2: 如果方法1匹配不到，用更宽泛的模式
        if len(matches) < 5:
            # 匹配 trend-name span
            alt_pattern = re.compile(
                r'trend-name[^>]*>\s*#?([^<]{2,80})\s*</',
            )
            matches = alt_pattern.findall(html)

        # 按出现顺序去重，限制最多 30 条
        seen = set()
        for topic in matches:
            topic = topic.strip()
            topic = re.sub(r'\s+', ' ', topic)
            if len(topic) < 2 or len(topic) > 80:
                continue
            if topic.lower() in seen:
                continue
            seen.add(topic.lower())

            if len(items) >= 30:  # 最多 30 条趋势
                break

            # X 搜索页
            x_url = f"https://x.com/search?q={topic.replace(' ', '%20')}&f=top"

            items.append({
                "title": f"🔥 X 印尼热搜：{topic}",
                "url": x_url,
                "source": "X Trends Indonesia",
                "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "summary": f"#{topic} 正在 X（Twitter）印尼区热门话题中热议",
                "lang": "id",
                "is_x_trend": True,
            })

        logger.info(f"    获取 {len(items)} 条 X 趋势")

    except Exception as e:
        logger.error(f"    X Trends 异常: {e}")

    return items


def build_x_trend_html(item: dict) -> str:
    """X 风格 HTML 模板（区别于普通新闻卡片）"""
    title = _e(item["title"])
    summary = _e(item.get("summary", ""))
    url = _e(item["url"])

    parts = [
        '<div style="background:#1da1f2;padding:14px 16px;border-radius:12px;'
        'margin:0 0 14px;color:#fff;">'
        '<div style="display:flex;align-items:center;margin-bottom:8px;">'
        '<span style="font-size:20px;margin-right:8px;">🐦</span>'
        '<span style="font-weight:bold;font-size:13px;opacity:0.9;">X · Trending in Indonesia</span>'
        '</div>'
        f'<p style="font-size:16px;font-weight:bold;line-height:1.5;margin:0 0 6px;">{title}</p>',
    ]

    if summary:
        parts.append(
            f'<p style="font-size:13px;line-height:1.6;opacity:0.85;margin:0 0 10px;">'
            f'{summary}</p>'
        )

    parts.append(
        f'<a href="{url}" target="_blank" rel="noopener" '
        'style="display:inline-block;background:rgba(255,255,255,0.2);color:#fff;'
        'padding:6px 14px;border-radius:20px;text-decoration:none;font-size:12px;'
        'font-weight:bold;">🔍 Lihat di X →</a>'
        '</div>'
    )

    return "\n".join(parts)


# ────────────────────── HTML 构建 ──────────────────────

def build_post_html(item: dict) -> str:
    title = _e(item["title"])
    summary = _e(item.get("summary", ""))
    url = _e(item["url"])
    source = _e(item["source"])

    parts = [
        '<div style="background:#fff3e0;padding:12px 16px;border-radius:8px;'
        'margin:0 0 14px;border-left:4px solid #ff9800;">'
        f'<p style="font-weight:bold;color:#e65100;margin:0 0 6px;">'
        f'🇮🇩 {source}</p>'
        f'<p style="font-size:16px;color:#bf360c;line-height:1.6;margin:0;">{title}</p>'
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
            'style="color:#ff9800;text-decoration:none;font-weight:bold;font-size:13px;">'
            '🔗 Baca selengkapnya →</a>'
        )

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


# ────────────────────── 去重 ──────────────────────

def deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = item["title"].lower().strip()[:80]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ────────────────────── 主流程 ──────────────────────

def run(save: bool = False, max_items: int = 20):
    logger.info("=== 印尼热点新闻 + X/Twitter 趋势抓取 ===")

    # ── 第一步：抓取 RSS 新闻 ──
    all_articles = []
    for src in NEWS_SOURCES:
        articles = fetch_rss_news(src["url"], src["name"], src["lang"])
        all_articles.extend(articles)

    # ── 第二步：抓取 X/Twitter 实时趋势 ──
    x_trends = fetch_x_trends_indonesia()

    # 将 X 趋势合并到文章列表（X 趋势优先展示）
    combined = x_trends[:8] + all_articles

    # 去重
    combined = deduplicate(combined)
    logger.info(f"共计 {len(combined)} 条（去重后，含 X 趋势 {len(x_trends)} 条）")

    if not combined:
        logger.warning("无内容")
        return

    # 截取
    if len(combined) > max_items:
        combined = combined[:max_items]

    # 打印预览
    print("\n" + "=" * 60)
    print("  印尼热点新闻 + X/Twitter 趋势")
    print("=" * 60)
    for i, art in enumerate(combined):
        badge = "[X]" if art.get("is_x_trend") else "[新闻]"
        print(f"\n[{i + 1}] {badge} [{art['source']}]")
        print(f"  {art['title'][:120]}")
        if art.get("url"):
            print(f"  {art['url']}")

    if save:
        cat_id = get_cat_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        for art in combined:
            bot = get_random_bot()

            # X 趋势用特殊模板，普通新闻用原有模板
            if art.get("is_x_trend"):
                content = build_x_trend_html(art)
                tags = ["Indonesia", "Twitter", "XTrending", "IndoTrending"]
            else:
                content = build_post_html(art)
                tags = ["Indonesia", "IndoNews"]

            try:
                result = insert_one("posts", {
                    "title": art["title"][:200],
                    "content": content,
                    "author_id": bot["id"],
                    "category_id": cat_id,
                    "post_type": "info",
                    "status": "pending_review",
                    "created_at": now,
                    "updated_at": now,
                }, returning="id")
                sync_tags(result["id"], tags)
                saved += 1
                logger.info(f"  [入库] [{bot['username']}] {art['title'][:50]}...")
            except Exception as e:
                logger.error(f"  入库失败: {e}")

        logger.info(f"[入库] {saved}/{len(combined)} 条")

    logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="印尼热点新闻抓取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=20, help="最大条目数")
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
