"""FIFA 2026 世界杯赛程抓取 + 入库脚本

功能：从 FIFA 官方页面抓取 2026 世界杯赛程，存入 Supabase posts 表
用法：
    python fifa_scraper.py                  # 仅抓取并打印
    python fifa_scraper.py --save           # 抓取并直接入库
    python fifa_scraper.py --save --output backup.json  # 入库 + 额外存 JSON
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
# 直连数据库（绕过 REST API 作业限制）
from db_direct import select_one, select_all, insert_one, upsert_one, update_one, execute_sql
from playwright.sync_api import sync_playwright

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

FIFA_API = "https://fifaworldcup26.hospitality.fifa.com/next-api/matches-all?productCode=26FWC&productType=5"
FIFA_PAGE = "https://fifaworldcup26.hospitality.fifa.com/us/en/choose-matches?scheduleView=true"


# ────────────────────── 数据库工具（直连 PostgreSQL） ──────────────────────

def lookup_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error(f"未找到作者: {username}")
        sys.exit(1)
    logger.info(f"作者: {row['username']} (id={row['id']})")
    return row["id"]


def lookup_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME", "Sports Talk")
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    logger.info(f"分类: {row['name']} (id={row['id']})")
    return row["id"]


def upsert_post(title: str, content: str, author_id: str,
                category_id: str, tag_name: str) -> str:
    """检查标题是否存在 → 存在则更新，否则新增 → 同步标签"""
    row = select_one("posts", {"title": title}, columns="id")
    now = datetime.now(timezone.utc).isoformat()

    if row:
        post_id = row["id"]
        update_one("posts", {"content": content, "updated_at": now}, {"id": post_id})
        logger.info(f"[更新] 帖子已存在，更新内容: {title}")
    else:
        result = insert_one("posts", {
            "title": title,
            "content": content,
            "author_id": author_id,
            "category_id": category_id,
            "status": "pending_review",
            "created_at": now,
            "updated_at": now,
        }, returning="id")
        post_id = result["id"]
        logger.info(f"[新增] 创建帖子: {title}")

    # 同步标签
    sync_tag(post_id, tag_name)
    return post_id


def sync_tag(post_id: str, tag_name: str) -> None:
    """查找或创建标签，建立 post_tags 关联"""
    # 查 tag
    row = select_one("tags", {"name": tag_name}, columns="id,name")
    if row:
        tag_id = row["id"]
    else:
        result = insert_one("tags", {"name": tag_name}, returning="id")
        tag_id = result["id"]
        logger.info(f"[Tag] 创建新标签: {tag_name}")

    # 查 post_tags 关联
    rel = select_one("post_tags", {"post_id": post_id, "tag_id": tag_id}, columns="post_id")
    if not rel:
        insert_one("post_tags", {"post_id": post_id, "tag_id": tag_id})

    # 更新 posts_count
    rows = select_all("post_tags", {"tag_id": tag_id}, columns="*")
    count = len(rows) if rows else 1
    update_one("tags", {"posts_count": count}, {"id": tag_id})


# ────────────────────── 数据抓取 ──────────────────────

def fetch_matches_from_api() -> list[dict]:
    """从 FIFA API 获取所有比赛数据"""
    logger.info("正在从 FIFA API 获取比赛数据...")
    r = httpx.get(FIFA_API, timeout=30)
    r.raise_for_status()
    data = r.json()
    logger.info(f"获取到 {len(data)} 场比赛")
    return data


def fetch_group_mapping() -> dict[int, str]:
    """从页面抓取 MatchNumber → Group 映射（小组赛才有分组）"""
    logger.info("正在从页面抓取分组信息...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        page.goto(FIFA_PAGE, wait_until="load", timeout=60000)
        page.wait_for_timeout(8000)

        mapping = page.evaluate("""() => {
            const containers = document.querySelectorAll('.schedule-view-tile__container');
            const mapping = {};
            containers.forEach(el => {
                const infoEl = el.querySelector('.schedule-view-tile__info-container');
                if (!infoEl) return;
                const text = infoEl.textContent.trim();
                const groupMatch = text.match(/Group\\s+([A-L])\\b/i);
                const numMatch = text.match(/\\bM(\\d+)/);
                if (groupMatch && numMatch) {
                    mapping[parseInt(numMatch[1])] = groupMatch[1];
                }
            });
            return mapping;
        }""")

        browser.close()
    logger.info(f"获取到 {len(mapping)} 场比赛的分组信息")
    return mapping


# ────────────────────── HTML 构建 ──────────────────────

def build_html_schedule(matches: list[dict]) -> str:
    """将比赛列表构建为 HTML 富文本表格，按组排列"""
    # 按组分组
    groups: dict[str, list[dict]] = {}
    for m in matches:
        g = m.get("group", "淘汰赛")
        groups.setdefault(g, []).append(m)

    sorted_groups = sorted([g for g in groups if g != "淘汰赛"]) + (["淘汰赛"] if "淘汰赛" in groups else [])

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_parts = [
        '<h2>美加墨世界杯小组赛赛程安排</h2>',
        f'<p style="color:#666;font-size:14px">更新时间：{now_str} (UTC) | 数据来源：FIFA 官方</p>',
        '<hr>',
    ]

    for g_name in sorted_groups:
        group_matches = groups[g_name]
        html_parts.append(f'<h3 style="margin-top:24px">{g_name} 组</h3>')
        html_parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
        html_parts.append('<tr style="background:#f0f0f0">'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">日期</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">时间</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">主队</th>'
                          '<th style="padding:8px;text-align:center;border:1px solid #ddd">VS</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">客队</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">场馆</th>'
                          '</tr>')
        for m in group_matches:
            html_parts.append('<tr>'
                              f'<td style="padding:6px 8px;border:1px solid #eee">{m["match_date"]}</td>'
                              f'<td style="padding:6px 8px;border:1px solid #eee">{m["match_time"]}</td>'
                              f'<td style="padding:6px 8px;border:1px solid #eee">{m["host_team"]}</td>'
                              f'<td style="padding:6px 8px;border:1px solid #eee;text-align:center;font-weight:bold">vs</td>'
                              f'<td style="padding:6px 8px;border:1px solid #eee">{m["away_team"]}</td>'
                              f'<td style="padding:6px 8px;border:1px solid #eee">{m["venue"]}</td>'
                              '</tr>')
        html_parts.append('</table>')

    html_parts.append('<hr>')
    html_parts.append('<p style="font-size:12px;color:#999">赛后将更新淘汰赛赛程，敬请关注。</p>')
    return '\n'.join(html_parts)


# ────────────────────── 主流程 ──────────────────────

def run(save_to_db: bool = False) -> list[dict]:
    api_matches = fetch_matches_from_api()
    group_map = fetch_group_mapping()

    # 合并数据：只取小组赛
    result = []
    for m in api_matches:
        if m["Stage"] != "GROUP STAGE MATCHES":
            continue
        mn = m["MatchNumber"]
        result.append({
            "match_number": mn,
            "host_team": m["HostTeam"]["ExternalName"],
            "away_team": m["OpposingTeam"]["ExternalName"],
            "venue": f"{m['Venue']['Name']} ({m['Venue']['Town']}, {m['Venue']['Country']})",
            "match_date": m["MatchDate"],
            "match_time": m["MatchDayTime"],
            "group": group_map.get(mn, ""),
        })

    logger.info(f"小组赛共 {len(result)} 场")

    # 通过已知分组的比赛推导队伍→组映射，修复缺失分组
    team_group = {}
    for m in result:
        if m["group"]:
            team_group[m["host_team"]] = m["group"]
            team_group[m["away_team"]] = m["group"]
    fixed = 0
    for m in result:
        if not m["group"]:
            g = team_group.get(m["host_team"]) or team_group.get(m["away_team"])
            if g:
                m["group"] = g
                fixed += 1
    if fixed:
        logger.info(f"推导修复 {fixed} 场缺失分组")

    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()
        html = build_html_schedule(result)
        upsert_post("美加墨世界杯小组赛赛程安排", html, author_id, category_id, "美加墨世界杯")

    return result


def main():
    parser = argparse.ArgumentParser(description="FIFA 2026 世界杯赛程抓取")
    parser.add_argument("--save", action="store_true", help="直接入库")
    args = parser.parse_args()

    logger.info("=== FIFA 2026 世界杯赛程抓取 ===")
    run(save_to_db=args.save)
    logger.info("=== 抓取完成 ===")


if __name__ == "__main__":
    main()
