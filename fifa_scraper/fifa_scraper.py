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
from supabase import create_client, Client
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


# ────────────────────── Supabase 工具 ──────────────────────

def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 环境变量")
        sys.exit(1)
    return create_client(url, key)


def lookup_author_id(client: Client) -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    result = client.table("profiles").select("id,username").eq("username", username).execute()
    if not result.data:
        logger.error(f"未找到作者: {username}")
        sys.exit(1)
    row = result.data[0]
    logger.info(f"作者: {row['username']} (id={row['id']})")
    return row["id"]


def lookup_category_id(client: Client) -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME", "Sports Talk")
    result = client.table("categories").select("id,name").eq("name", name).execute()
    if not result.data:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    row = result.data[0]
    logger.info(f"分类: {row['name']} (id={row['id']})")
    return row["id"]


def upsert_post(client: Client, title: str, content: str, author_id: str,
                category_id: str, tag_name: str) -> str:
    """检查标题是否存在 → 存在则更新，否则新增 → 同步标签"""
    result = client.table("posts").select("id").eq("title", title).execute()
    now = datetime.now(timezone.utc).isoformat()

    if result.data:
        post_id = result.data[0]["id"]
        client.table("posts").update({
            "content": content,
            "updated_at": now,
        }).eq("id", post_id).execute()
        logger.info(f"[更新] 帖子已存在，更新内容: {title}")
    else:
        resp = client.table("posts").insert({
            "title": title,
            "content": content,
            "author_id": author_id,
            "category_id": category_id,
            "status": "pending_review",
            "created_at": now,
            "updated_at": now,
        }).execute()
        post_id = resp.data[0]["id"]
        logger.info(f"[新增] 创建帖子: {title}")

    # 同步标签
    sync_tag(client, post_id, tag_name)
    return post_id


def sync_tag(client: Client, post_id: str, tag_name: str) -> None:
    """查找或创建标签，建立 post_tags 关联"""
    # 查 tag
    result = client.table("tags").select("id,name").eq("name", tag_name).execute()
    if result.data:
        tag_id = result.data[0]["id"]
    else:
        resp = client.table("tags").insert({"name": tag_name}).execute()
        tag_id = resp.data[0]["id"]
        logger.info(f"[Tag] 创建新标签: {tag_name}")

    # 查 post_tags 关联
    rel = client.table("post_tags").select("post_id").eq("post_id", post_id).eq("tag_id", tag_id).execute()
    if not rel.data:
        client.table("post_tags").insert({"post_id": post_id, "tag_id": tag_id}).execute()

    # 更新 posts_count
    count_resp = client.table("post_tags").select("*", count="exact").eq("tag_id", tag_id).execute()
    count = count_resp.count if count_resp.count else 1
    client.table("tags").update({"posts_count": count}).eq("id", tag_id).execute()


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
        client = get_supabase_client()
        author_id = lookup_author_id(client)
        category_id = lookup_category_id(client)
        html = build_html_schedule(result)
        upsert_post(client, "美加墨世界杯小组赛赛程安排", html, author_id, category_id, "美加墨世界杯")

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
