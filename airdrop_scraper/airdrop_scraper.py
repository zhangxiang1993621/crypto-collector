"""Web3 交易所空投福利信息爬取

功能：抓取主流交易所（Binance/Bybit/OKX）公告中的空投、福利、上币信息，
      过滤筛选后发布到 Hot Tokens 分类

用法：
    python airdrop_scraper/airdrop_scraper.py                  # 仅抓取打印
    python airdrop_scraper/airdrop_scraper.py --save           # 抓取并入库
    python airdrop_scraper/airdrop_scraper.py --save --max 20  # 最多20条
"""

import os
import sys
import re
import json
import logging
import argparse
from pathlib import Path
from typing import TYPE_CHECKING
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv
from supabase_client import get_client

if TYPE_CHECKING:
    from supabase import Client

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ────────────────────── 交易所 API / 页面配置 ──────────────────────
# Binance 内部 API（catalogId=48 是最新动态/上币）
BINANCE_API = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
    "?catalogId=48&pageNo=1&pageSize=40"
)
# 备用：用搜索 API 搜 "airdrop"
BINANCE_SEARCH_API = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&pageNo=1&pageSize=30"
)

# OKX 公告 API（web 端点）
OKX_API = "https://www.okx.com/priapi/v1/support/announcements/list?page=1&limit=30&t={ts}"

# Bybit 公告 API
BYBIT_API = "https://api.bybit.com/v5/announcements/index?locale=en-US&limit=20&page=1"


# ────────────────────── 关键词过滤 ──────────────────────
AIRDROP_KEYWORDS = [
    "airdrop", "空投", "giveaway", "奖励", "reward", "earn",
    "token listing", "上线", "listing", "launchpool", "launchpad",
    "staking", "质押", "bonus", "福利", "免费", "领取", "free",
    "trading competition", "交易赛", "campaign", "活动",
    "distribution", "分配", "snapshot", "快照",
    "futures listing", "现货上线", "合约上线",
    "赠金", "红包", "返佣", "new listing",
    "locked products", "simple earn", "dual investment",
    "megadrop", "hodler airdrops", "holders",
]


# ────────────────────── 工具函数 ──────────────────────

def get_headers() -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }




def get_cat_id(client):
    name = os.environ.get("HOT_TOKENS_CATEGORY_NAME", "Hot Tokens")
    return client.table("categories").select("id").eq("name", name).execute().data[0]["id"]


def lookup_author(client):
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    return client.table("profiles").select("id,username").eq("username", username).execute().data[0]


def match_airdrop(text: str) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in AIRDROP_KEYWORDS)


def truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text


def strip_html(text) -> str:
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', str(text)).strip()


def _safe_print(text: str) -> None:
    """安全打印，处理 Windows GBK 控制台不支持 emoji 的问题"""
    try:
        print(text)
    except UnicodeEncodeError:
        # 用 ASCII 替换非 GBK 字符
        print(text.encode("gbk", errors="replace").decode("gbk", errors="replace"))


def extract_date(text: str) -> str | None:
    """尝试从文本中提取日期"""
    patterns = [
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{2}-\d{2}-\d{4})',
        r'(\w+ \d{1,2},?\s*\d{4})',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


# ────────────────────── 交易所抓取 ──────────────────────

def scrape_binance() -> list[dict]:
    """通过 Binance 内部 API 抓取公告"""
    logger.info("  [Binance] 抓取公告...")
    items = []

    def _fetch(url: str) -> list:
        try:
            resp = httpx.get(
                url,
                headers={
                    **get_headers(),
                    "Referer": "https://www.binance.com/en/support/announcement",
                    "lang": "en",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            # data.articles 或 data.catalogs[n].articles
            articles = data.get("data", {}).get("articles", [])
            if articles:
                return articles
            # 也可能嵌套在 catalogs 下
            for cat in data.get("data", {}).get("catalogs", []):
                articles.extend(cat.get("articles", []))
            return articles
        except Exception:
            return []

    articles_raw = _fetch(BINANCE_API)
    if not articles_raw:
        articles_raw = _fetch(BINANCE_SEARCH_API)

    for a in articles_raw:
        title = a.get("title", "")
        if not title:
            continue
        items.append({
            "title": title,
            "url": f"https://www.binance.com/en/support/announcement/{a.get('code', '')}",
            "exchange": "Binance",
            "release_date": datetime.fromtimestamp(
                a.get("releaseDate", 0) / 1000
            ).strftime("%Y-%m-%d") if a.get("releaseDate") else "",
            "summary": truncate(strip_html(a.get("body", "")), 300),
        })

    logger.info(f"    获取 {len(items)} 条")
    return items


def scrape_okx() -> list[dict]:
    """通过 OKX API 抓取公告"""
    logger.info("  [OKX] 抓取公告...")
    items = []

    try:
        import time

        url = OKX_API.replace("{ts}", str(int(time.time() * 1000)))
        resp = httpx.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://www.okx.com/help",
            },
            timeout=30,
        )

        if resp.status_code != 200:
            logger.warning(f"    OKX API 返回 {resp.status_code}")
            return items

        data = resp.json()
        announcements = data.get("data", {}).get("list", [])

        for a in announcements:
            title = a.get("title", "") or a.get("name", "")
            if not title:
                continue
            aid = a.get("id", "") or a.get("articleId", "")
            items.append({
                "title": title,
                "url": f"https://www.okx.com/help/{aid}" if aid else "",
                "exchange": "OKX",
                "release_date": (a.get("publishTime") or a.get("createTime", ""))[:10],
                "summary": truncate(strip_html(a.get("description", a.get("summary", ""))), 300),
            })
    except Exception as e:
        logger.warning(f"    OKX 不可达（本网络无法访问 www.okx.com）: {e}")

    logger.info(f"    获取 {len(items)} 条")
    return items


def scrape_bybit() -> list[dict]:
    """通过 Bybit API 抓取公告"""
    logger.info("  [Bybit] 抓取公告...")
    items = []

    try:
        resp = httpx.get(BYBIT_API, headers=get_headers(), timeout=30)
        if resp.status_code != 200:
            logger.warning(f"    Bybit API 返回 {resp.status_code}")
            return items

        data = resp.json()
        announcements = data.get("result", {}).get("list", [])

        for a in announcements:
            title = a.get("title", "")
            items.append({
                "title": title,
                "url": a.get("url", ""),
                "exchange": "Bybit",
                "release_date": datetime.fromtimestamp(
                    int(a.get("createdAt", "0")) / 1000
                ).strftime("%Y-%m-%d") if a.get("createdAt") else "",
                "summary": truncate(strip_html(a.get("description", a.get("summary", ""))), 300),
            })
    except Exception as e:
        logger.error(f"    Bybit 异常: {e}")

    logger.info(f"    获取 {len(items)} 条")
    return items


# ────────────────────── HTML 构建 ──────────────────────

def build_item_card(item: dict, index: int) -> str:
    """构建单条空投信息卡片"""
    escaped_title = _e(item["title"])
    escaped_summary = _e(item.get("summary", ""))
    escaped_url = _e(item["url"])
    exchange = _e(item["exchange"])
    date_str = item.get("release_date", "")

    date_line = ""
    if date_str:
        date_line = f'<span style="color:#888;font-size:12px;">📅 {date_str}</span>'

    parts = [
        '<div style="background:#f0fdf4;padding:12px 16px;border-radius:8px;'
        'margin:0 0 10px;border-left:4px solid #22c55e;">'
        f'<p style="font-weight:bold;color:#166534;margin:0 0 4px;">'
        f'🪂 #{index} {exchange} · 空投/福利 {date_line}</p>'
        f'<p style="font-size:15px;color:#14532d;margin:0 0 4px;">{escaped_title}</p>',
    ]

    if escaped_summary:
        parts.append(
            f'<p style="font-size:13px;color:#555;line-height:1.6;'
            f'margin:4px 0;">{escaped_summary}</p>'
        )

    if escaped_url:
        parts.append(
            f'<a href="{escaped_url}" target="_blank" rel="noopener" '
            'style="color:#3b82f6;text-decoration:none;font-size:13px;">'
            '🔗 原文 →</a>'
        )

    parts.append('</div>')
    return "\n".join(parts)


def build_daily_post_html(items: list[dict], day_str: str, update_time: str) -> str:
    """构建每日空投汇总帖 HTML"""
    cards = []
    for i, item in enumerate(items):
        cards.append(build_item_card(item, i + 1))

    return "\n".join([
        f'<div style="text-align:center;padding:4px 0 16px;">'
        f'<h2 style="margin:0;color:#1a1a2e;">🪂 加密空投/福利日报</h2>'
        f'<p style="color:#888;font-size:13px;margin:6px 0 0;">'
        f'� {day_str} ｜ 已收录 {len(items)} 条 ｜ 更新于 {update_time} UTC</p>'
        f'</div>',
        '<hr>',
        *cards,
        '<hr>',
        '<p style="font-size:11px;color:#aaa;text-align:center;">'
        '🤖 信息由 Airdrop 爬虫自动采集，仅供参考，请以交易所官方公告为准。</p>',
    ])


def extract_existing_urls(html: str) -> set[str]:
    """从已有帖子的 HTML 中提取所有链接 URL"""
    urls = set()
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        urls.add(m.group(1))
    return urls


def build_daily_title(day_str: str) -> str:
    return f"🪂 加密空投/福利日报 | {day_str}"


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ────────────────────── 标签 ──────────────────────

def sync_tags(client, post_id: str, tags: list[str]):
    if not tags:
        return
    unique = list(set(tags))
    em = {}
    try:
        r = client.table("tags").select("id,name").in_("name", unique).execute()
        em = {d["name"]: d["id"] for d in r.data}
    except Exception:
        pass
    new = [n for n in unique if n not in em]
    if new:
        try:
            r = client.table("tags").insert([{"name": n, "posts_count": 0} for n in new]).execute()
            for d in r.data:
                em[d["name"]] = d["id"]
        except Exception:
            pass
    for name in unique:
        tid = em.get(name)
        if not tid:
            continue
        try:
            lk = client.table("post_tags").select("post_id").eq("post_id", post_id).eq("tag_id", tid).execute()
            if not lk.data:
                client.table("post_tags").insert({"post_id": post_id, "tag_id": tid}).execute()
        except Exception:
            pass


# ────────────────────── 去重 ──────────────────────

def deduplicate(items: list[dict]) -> list[dict]:
    """按标题去重"""
    seen = set()
    result = []
    for item in items:
        key = item["title"].lower().strip()[:80]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ────────────────────── 每日帖管理 ──────────────────────

def find_today_post(client, cat_id: str, day_str: str) -> dict | None:
    """查找今天的空投日报帖"""
    title_match = f"🪂 加密空投/福利日报 | {day_str}"
    result = client.table("posts").select("id,title,content").eq(
        "category_id", cat_id
    ).eq("title", title_match).order("created_at", desc=True).limit(1).execute()
    return result.data[0] if result.data else None


def merge_items(existing: list[dict], new: list[dict]) -> tuple[list[dict], list[dict]]:
    """合并新旧条目，返回 (完整合并列表, 新增条目列表)

    去重依据: url（优先）或 title[:80]（url 为空时）
    """
    seen_urls = set()
    for item in existing:
        if item.get("url"):
            seen_urls.add(item["url"])
        else:
            seen_urls.add(item["title"].strip().lower()[:80])

    merged = list(existing)
    added = []
    for item in new:
        key = item.get("url") or item["title"].strip().lower()[:80]
        if key not in seen_urls:
            seen_urls.add(key)
            merged.append(item)
            added.append(item)

    return merged, added


# ────────────────────── 主流程 ──────────────────────

def run(save: bool = False, max_items: int = 20):
    logger.info("=== Web3 空投福利信息爬取 ===")

    scrapers = {
        "Binance": scrape_binance,
        "Bybit": scrape_bybit,
        "OKX": scrape_okx,
    }

    all_items = []
    for name, fn in scrapers.items():
        try:
            items = fn()
            matched = [i for i in items if match_airdrop(i["title"])]
            logger.info(f"  [{name}] 匹配空投关键词: {len(matched)}/{len(items)}")
            all_items.extend(matched)
        except Exception as e:
            logger.error(f"  [{name}] 出错: {e}")

    # 去重
    all_items = deduplicate(all_items)
    logger.info(f"共计 {len(all_items)} 条空投信息（去重后）")

    if not all_items:
        logger.warning("无匹配信息")
        return

    # 打印预览（处理 Windows GBK 编码）
    _safe_print("\n" + "=" * 60)
    _safe_print("  空投/福利信息汇总")
    _safe_print("=" * 60)
    for i, item in enumerate(all_items[:max_items]):
        _safe_print(f"\n[{i + 1}] [{item['exchange']}] {item.get('release_date', '')}")
        _safe_print(f"  {item['title'][:120]}")
        if item.get("url"):
            _safe_print(f"  {item['url']}")
        if item.get("summary"):
            _safe_print(f"  {item['summary'][:200]}")

    if save:
        client = get_client()
        author = lookup_author(client)
        cat_id = get_cat_id(client)

        now_utc = datetime.now(timezone.utc)
        day_str = now_utc.strftime("%Y-%m-%d")
        update_time = now_utc.strftime("%H:%M")

        existing_post = find_today_post(client, cat_id, day_str)

        if existing_post:
            # 已存在今日日报 → 追加新条目卡片
            logger.info(f"已存在今日日报 id={existing_post['id'][:8]}...，准备追加")
            existing_urls = extract_existing_urls(existing_post["content"])
            new_items = [i for i in all_items if i.get("url") not in existing_urls]

            if not new_items:
                logger.info("无新条目，跳过更新")
                return

            logger.info(f"发现 {len(new_items)} 条新信息，追加到日报")

            # 在免责声明前插入新卡片
            existing_html = existing_post["content"]
            split_marker = '<p style="font-size:11px;color:#aaa;text-align:center;">'
            if split_marker in existing_html:
                before, after = existing_html.split(split_marker, 1)
            else:
                before, after = existing_html, ""

            # 统计新增后总数
            card_count = len(_parse_items_from_cards(existing_html)) + len(new_items)
            # 更新头部统计
            header_end = "</div>\n<hr>"
            old_header, rest = before.split(header_end, 1) if header_end in before else (before, "")
            new_header = re.sub(
                r'已收录 \d+ 条',
                f'已收录 {card_count} 条',
                old_header
            )
            new_header = re.sub(
                r'更新于 \d{2}:\d{2} UTC',
                f'更新于 {update_time} UTC',
                new_header
            )

            new_cards = []
            start_idx = len(_parse_items_from_cards(existing_html))
            for i, item in enumerate(new_items):
                new_cards.append(build_item_card(item, start_idx + i + 1))

            new_content = new_header + header_end + rest + "\n" + "\n".join(new_cards) + "\n" + split_marker + after

            client.table("posts").update({
                "content": new_content,
                "updated_at": now_utc.isoformat(),
            }).eq("id", existing_post["id"]).execute()

            logger.info(f"[追加] 日报 {existing_post['id'][:8]}... +{len(new_items)} 条 → 共 {card_count} 条")
        else:
            # 新日报
            items_to_post = all_items[:max_items]
            title = build_daily_title(day_str)
            html = build_daily_post_html(items_to_post, day_str, update_time)

            resp = client.table("posts").insert({
                "title": title,
                "content": html,
                "author_id": author["id"],
                "category_id": cat_id,
                "status": "published",
                "created_at": now_utc.isoformat(),
                "updated_at": now_utc.isoformat(),
            }).execute()

            pid = resp.data[0]["id"]
            all_tags = list(set(["Airdrop"] + [i["exchange"] for i in items_to_post]))
            sync_tags(client, pid, all_tags)
            logger.info(f"[入库] 日报 id={pid[:8]}... {len(items_to_post)} 条")

        # 更新所有标签
        all_tags = list(set(["Airdrop"] + [i["exchange"] for i in all_items]))
        sync_tags(client, existing_post["id"] if existing_post else pid, all_tags)

    logger.info("=== 完成 ===")


def _parse_items_from_cards(html: str) -> list[dict]:
    """从已有日报 HTML 卡片中粗略提取条目信息（用于统计）"""
    items = []
    cards = re.split(r'<div style="background:#f0fdf4', html)
    for card in cards[1:]:
        title_m = re.search(r'color:#14532d;margin:0 0 4px;">([^<]+)</p>', card)
        url_m = re.search(r'href="(https?://[^"]+)"', card)
        items.append({
            "title": title_m.group(1).strip() if title_m else "",
            "url": url_m.group(1) if url_m else "",
        })
    return items


def main():
    p = argparse.ArgumentParser(description="Web3 空投福利信息爬取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=20, help="最大条目数")
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
