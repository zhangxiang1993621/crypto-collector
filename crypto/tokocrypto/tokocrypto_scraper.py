"""Tokocrypto 活动/公告抓取脚本 (Binance 印尼站)

功能：通过 Tokocrypto 官方 API 获取活动列表，抓取活动详情页，
      以机器人身份发布到 forum 对应分类。

数据源：
  - API: https://www.tokocrypto.com/v1/activity-menus?sourceSeat=1
  - 详情: https://www.tokocrypto.com/en/campaign/{id}

用法：
    python tokocrypto_scraper/tokocrypto_scraper.py                  # 仅抓取打印
    python tokocrypto_scraper/tokocrypto_scraper.py --save           # 抓取并入库
    python tokocrypto_scraper/tokocrypto_scraper.py --save --max 10  # 最多10条
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

ACTIVITY_API = "https://www.tokocrypto.com/v1/activity-menus?sourceSeat=1"
CAMPAIGN_BASE = "https://www.tokocrypto.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
    ),
}


def get_cat_id() -> str:
    name = os.environ.get("TOKOCRYPTO_CATEGORY_NAME") or "Hot Tokens"
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


# ────────────────────── 抓取 ──────────────────────


def fetch_activity_list() -> list[dict]:
    """从 Tokocrypto API 获取活动列表"""
    logger.info("抓取 Tokocrypto 活动列表...")
    try:
        resp = httpx.get(ACTIVITY_API, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"API 请求失败: {e}")
        return []

    if data.get("code") != 0:
        logger.error(f"API 返回错误: {data}")
        return []

    items: list[dict] = []
    for act in data.get("data", {}).get("list", []):
        name = act.get("name", "").strip()
        if not name:
            continue

        redirect_addr = act.get("redirectAddress", "")
        redirect_type = act.get("redirectType", 0)

        items.append({
            "api_id": act.get("id"),
            "name": name,
            "description": act.get("description", ""),
            "redirect_type": redirect_type,
            "redirect_address": redirect_addr,
            "image_url": act.get("fullImgUrl", ""),
        })

    logger.info(f"获取 {len(items)} 条活动")
    return items


def fetch_campaign_detail(campaign_url: str) -> dict:
    """抓取活动详情页 HTML 内容"""
    detail = {"full_text": "", "date_str": ""}
    try:
        resp = httpx.get(campaign_url, headers=HEADERS, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"    详情页 HTTP {resp.status_code}: {campaign_url}")
            return detail
        html = resp.text

        # 提取正文内容：<div class="content"> 或 <article> 区域
        content_match = re.search(
            r'<article[^>]*>(.*?)</article>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if content_match:
            text = strip_html(content_match.group(1))
        else:
            # fallback: 取 body 内文本
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
            text = strip_html(body_match.group(1)) if body_match else html
            # 截断到合理长度
            text = text[:3000]

        # 清理多余空白
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        detail["full_text"] = text.strip()

        # 尝试提取日期
        date_match = re.search(
            r'(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4})',
            html
        )
        if date_match:
            detail["date_str"] = date_match.group(1)

    except Exception as e:
        logger.warning(f"    抓取详情页失败: {e}")

    return detail


def scrape_tokocrypto(max_items: int = 20) -> list[dict]:
    """主抓取逻辑：活动列表 + 详情页"""
    activities = fetch_activity_list()
    results: list[dict] = []

    for act in activities:
        if len(results) >= max_items:
            break

        redirect_addr = act.get("redirect_address", "")
        redirect_type = act.get("redirect_type", 0)

        # redirectType 1 = 内部 campaign 页，3 = 外部链接
        if redirect_type == 1 and redirect_addr:
            # 内部活动页
            if not redirect_addr.startswith("http"):
                campaign_url = CAMPAIGN_BASE + redirect_addr
            else:
                campaign_url = redirect_addr

            # Zendesk support 页面有反爬，直接用 API 数据
            if "support.tokocrypto.com" in campaign_url:
                summary = act["description"]
                full_text = ""
            else:
                detail = fetch_campaign_detail(campaign_url)
                full_text = detail.get("full_text", "")
                summary = full_text[:500] if full_text else act["description"]
        else:
            campaign_url = redirect_addr if redirect_addr else ""
            summary = act["description"]
            full_text = ""

        results.append({
            "title": act["name"],
            "url": campaign_url,
            "summary": summary,
            "full_text": full_text,
            "image_url": act.get("image_url", ""),
            "date_str": detail.get("date_str", "") if redirect_type == 1 else "",
            "redirect_type": redirect_type,
        })

    return results


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
    summary = _e(item.get("summary", ""))
    url = _e(item.get("url", ""))
    image_url = _e(item.get("image_url", ""))
    date_str = _e(item.get("date_str", ""))
    is_external = item.get("redirect_type", 1) == 3

    badge = "🔗 Link Eksternal" if is_external else "🎯 Campaign Tokocrypto"

    parts = [
        '<div style="background:#fff8e1;padding:14px 16px;border-radius:10px;'
        'margin:0 0 14px;border-left:4px solid #f5a623;">'
        f'<p style="font-weight:bold;color:#e65100;margin:0 0 4px;">'
        f'🇮🇩 Tokocrypto · {badge}</p>'
    ]

    if date_str:
        parts.append(
            f'<p style="font-size:11px;color:#999;margin:0 0 6px;">'
            f'{date_str}</p>'
        )

    parts.append(
        f'<p style="font-size:16px;color:#1a1a1a;line-height:1.6;margin:0 0 6px;">'
        f'{title}</p>'
        '</div>'
    )

    if image_url:
        parts.append(
            f'<div style="text-align:center;margin:0 0 10px;">'
            f'<img src="{image_url}" alt="{title}" '
            f'style="max-width:100%;border-radius:8px;max-height:400px;" />'
            f'</div>'
        )

    if summary:
        parts.append(
            '<div style="padding:0 4px;">'
            f'<p style="font-size:14px;line-height:1.8;color:#444;margin:8px 0;">{summary}</p>'
            '</div>'
        )

    if url:
        label = "Lihat detail →" if not is_external else "Buka link eksternal →"
        parts.append(
            f'<p style="margin:8px 0;">'
            f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="display:inline-block;background:#f5a623;color:#fff;'
            f'padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;">'
            f'🔗 {label}</a></p>'
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
    logger.info("=== Tokocrypto 活动抓取 ===")

    items = scrape_tokocrypto(max_items=max_items)
    items = deduplicate(items)
    logger.info(f"去重后共 {len(items)} 条")

    if not items:
        logger.warning("无内容")
        return

    if max_items and len(items) > max_items:
        items = items[:max_items]

    print("\n" + "=" * 60)
    print("  Tokocrypto 活动")
    print("=" * 60)
    for i, item in enumerate(items):
        badge = "[外部]" if item.get("redirect_type") == 3 else "[活动]"
        print(f"\n[{i + 1}] {badge} {item['title'][:100]}")
        if item.get("url"):
            print(f"  {item['url']}")

    if save:
        cat_id = get_cat_id()
        now = datetime.now(timezone.utc).isoformat()

        new_items = filter_new_only(items, cat_id)
        if not new_items:
            logger.info("无新活动，跳过入库")
            return

        saved = 0
        for item in new_items:
            bot = get_random_bot()
            content = build_post_html(item)
            tags = ["Tokocrypto", "Indonesia", "Kripto", "Campaign"]

            try:
                result = insert_one("posts", {
                    "title": item["title"][:200],
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
                logger.info(f"  [入库] [{bot['username']}] {item['title'][:50]}...")
            except Exception as e:
                logger.error(f"  入库失败: {e}")

        logger.info(f"[入库] {saved}/{len(new_items)} 条")

    logger.info("=== 完成 ===")


def main():
    p = argparse.ArgumentParser(description="Tokocrypto 活动抓取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=20, help="最大条目数")
    args = p.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
