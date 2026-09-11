"""Goal.com 世界杯 2026 比分抓取 + 入库脚本

功能：从 Goal.com 抓取 2026 世界杯每场比赛的比分（已结束 + 进行中），存入 Supabase posts 表。
数据来源：
  1. Goal.com SSR 页面中 __NEXT_DATA__ 的 gamesets 配置（获取各阶段 gameSetTypeId + roundIds）
  2. /api/competition-matches 接口逐阶段获取完整比赛数据（需浏览器 cookie）
  3. /api/live-scores/refresh 接口获取实时比分更新（可选，--live）

用法：
    python sport/goal/goal_scraper.py                  # 仅抓取打印
    python sport/goal/goal_scraper.py --save           # 抓取并直接入库
    python sport/goal/goal_scraper.py --save --live    # 同时检查实时比分 API 更新
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, update_one
from playwright.sync_api import sync_playwright

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PAGE_URL = "https://www.goal.com/en-in/world-cup/fixtures-results/70excpe1synn9kadnbppahdn7"
API_MATCHES_URL = "https://www.goal.com/api/competition-matches"
LIVE_SCORES_URL = "https://www.goal.com/api/live-scores/refresh"
COMPETITION_ID = "70excpe1synn9kadnbppahdn7"

STATUS_LABELS: dict[str, str] = {
    "RESULT": "FT",
    "LIVE": "LIVE",
    "FIXTURE": "Upcoming",
    "SCHEDULED": "Upcoming",
    "POSTPONED": "PP",
    "CANCELLED": "Cancelled",
}

STATUS_EMOJI: dict[str, str] = {
    "RESULT": "\u23f0",
    "LIVE": "\U0001F534",
    "FIXTURE": "\U0001F5D3\uFE0F",
    "SCHEDULED": "\U0001F5D3\uFE0F",
}


# ---------------------------------------------------------------------------
# 数据库工具
# ---------------------------------------------------------------------------

def lookup_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error(f"\u672a\u627e\u5230\u4f5c\u8005: {username}")
        sys.exit(1)
    logger.info(f"\u4f5c\u8005: {row['username']} (id={row['id']})")
    return row["id"]


def lookup_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME") or "Sports Talk"
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error(f"\u672a\u627e\u5230\u5206\u7c7b: {name}")
        sys.exit(1)
    logger.info(f"\u5206\u7c7b: {row['name']} (id={row['id']})")
    return row["id"]


# ---------------------------------------------------------------------------
# 数据抓取
# ---------------------------------------------------------------------------

def _extract_gameset_configs(page: Any) -> list[dict]:
    """从 __NEXT_DATA__ 提取各比赛阶段的 gameSetTypeId 和 roundIds 配置。

    Returns:
        [{"name": "Game Week 1", "gameSetTypeId": "xxx", "roundIds": ["r1","r2",...]}, ...]
    """
    raw = page.evaluate("""() => {
        const el = document.getElementById('__NEXT_DATA__');
        if (!el) return [];
        const data = JSON.parse(el.textContent);
        const content = data?.props?.pageProps?.content;
        return (content?.gamesets || []).map(gs => ({
            name: gs.name,
            gameSetTypeId: gs.gameSetTypeId,
            roundIds: (gs.rounds || []).map(r => r.id).filter(Boolean)
        }));
    }""")
    return raw


def _fetch_stage_matches(page: Any, game_set_type_id: str, round_ids: list[str]) -> list[dict]:
    """通过 Goal.com competition-matches API 获取单阶段的比赛数据。

    必须在 Playwright page 上下文中调用（需要浏览器 cookie）。
    如果 API 失败则返回空列表，不阻断其他阶段的数据抓取。

    Goal.com API 要求：
      - edition 参数（如 en-in），否则返回 404
      - roundIds 以多个同名查询参数传递（每个小组一个）
      - 需要浏览器的 cookie + User-Agent + Accept 头
    """
    round_params = "".join(f"&roundIds={rid}" for rid in round_ids)
    api_url = (
        f"{API_MATCHES_URL}?id={COMPETITION_ID}"
        f"&gameSetTypeIds={game_set_type_id}{round_params}"
        f"&edition=en-in"
    )

    try:
        result = page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {
                    credentials: 'include',
                    headers: { 'Accept': 'application/json, text/plain, */*' }
                });
                if (!resp.ok) return null;
                return await resp.json();
            }""",
            api_url,
        )
    except Exception as exc:
        logger.warning(f"API 请求异常 ({game_set_type_id}): {exc}")
        return []

    if not result:
        logger.warning(f"API 返回空 ({game_set_type_id})")
        return []

    # API 响应结构: {"gamesets": [{"matches": [...]}]}
    gamesets = result.get("gamesets", [])
    all_matches: list[dict] = []
    for gs in gamesets:
        all_matches.extend(gs.get("matches", []))
    return all_matches


def fetch_all_match_data() -> list[dict]:
    """通过 Goal.com API 逐阶段拉取世界杯所有比赛数据。

    流程：
      1. Playwright 加载页面获取浏览器 cookie
      2. 从 __NEXT_DATA__ 提取各阶段配置（gameSetTypeId + roundIds）
      3. 对每个阶段调用 competition-matches API
      4. 合并、去重（按 match id）、返回
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()

        logger.info(f"访问页面: {PAGE_URL}")
        try:
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            logger.error(f"页面加载失败: {exc}")
            browser.close()
            return []

        # 1. 提取阶段配置
        stage_configs = _extract_gameset_configs(page)
        logger.info(f"提取到 {len(stage_configs)} 个比赛阶段配置")

        # 2. 逐阶段拉取比赛数据
        all_matches: dict[str, dict] = {}  # match_id -> match, 去重用
        for cfg in stage_configs:
            name = cfg["name"]
            gs_id = cfg["gameSetTypeId"]
            round_ids = cfg["roundIds"]
            if not gs_id:
                logger.warning(f"跳过阶段 {name}：无 gameSetTypeId")
                continue

            matches = _fetch_stage_matches(page, gs_id, round_ids)
            for m in matches:
                mid = m.get("id")
                if mid:
                    # 保留最新的数据（cachedAt 更大）
                    existing = all_matches.get(mid)
                    if not existing or (m.get("cachedAt", "") > existing.get("cachedAt", "")):
                        all_matches[mid] = m
                else:
                    all_matches[f"_no_id_{len(all_matches)}"] = m

            logger.info(f"  {name}: {len(matches)} 场比赛 (累计 {len(all_matches)} 场)")

        browser.close()

    result = list(all_matches.values())
    logger.info(f"全部比赛数据拉取完成: {len(result)} 场")
    return result


def fetch_live_scores() -> dict[str, dict]:
    """从 Goal.com live-scores API 获取实时比分更新。

    Returns:
        {match_id: live_score_data, ...}
    """
    try:
        r = httpx.get(
            LIVE_SCORES_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": PAGE_URL,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.warning(f"实时比分 API 请求失败: {exc}")
        return {}

    live_map: dict[str, dict] = {}
    for m in data.get("matches", []):
        live_map[m["id"]] = m
    logger.info(f"实时比分 API 返回 {len(live_map)} 场比赛")
    return live_map


# ---------------------------------------------------------------------------
# 内容构建
# ---------------------------------------------------------------------------

MATCH_ROW_TEMPLATE = """<tr>
    <td style="text-align:center;padding:8px 10px;border-bottom:1px solid #333">
        <span style="font-size:12px;color:#888">{date_str}</span>
    </td>
    <td style="padding:8px 10px;border-bottom:1px solid #333;text-align:right">
        <span style="font-weight:bold;font-size:16px;color:#fff">{team_a_name}</span>
        <span style="font-size:11px;color:#888;margin-left:4px">{team_a_code}</span>
    </td>
    <td style="text-align:center;padding:8px 6px;border-bottom:1px solid #333">
        <span style="font-size:22px;font-weight:bold;color:#00d4ff">{score_a}</span>
    </td>
    <td style="text-align:center;padding:8px 6px;border-bottom:1px solid #333;color:#888">VS</td>
    <td style="text-align:center;padding:8px 6px;border-bottom:1px solid #333">
        <span style="font-size:22px;font-weight:bold;color:#00d4ff">{score_b}</span>
    </td>
    <td style="padding:8px 10px;border-bottom:1px solid #333;text-align:left">
        <span style="font-weight:bold;font-size:16px;color:#fff">{team_b_name}</span>
        <span style="font-size:11px;color:#888;margin-left:4px">{team_b_code}</span>
    </td>
    <td style="text-align:center;padding:8px 10px;border-bottom:1px solid #333">
        <span style="font-size:12px;background:#1a3a5c;color:#00d4ff;padding:3px 8px;border-radius:12px">{status_label}</span>
    </td>
</tr>"""


def build_match_row(match: dict) -> str:
    """构建单场比赛的 HTML 行。"""
    team_a = match.get("teamA") or {}
    team_b = match.get("teamB") or {}
    score = match.get("score") or {}
    status = match.get("status", "")
    start_date = match.get("startDate", "")

    if start_date:
        try:
            dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%m/%d %H:%M")
        except (ValueError, TypeError):
            date_str = str(start_date)[:16]
    else:
        date_str = ""

    status_label = STATUS_LABELS.get(status, status)

    return MATCH_ROW_TEMPLATE.format(
        date_str=date_str,
        team_a_name=team_a.get("name", "TBD"),
        team_a_code=team_a.get("codeName", ""),
        team_b_name=team_b.get("name", "TBD"),
        team_b_code=team_b.get("codeName", ""),
        score_a=score.get("teamA", "-"),
        score_b=score.get("teamB", "-"),
        status_label=status_label,
    )


def build_html_content(matches: list[dict], competition_name: str = "World Cup") -> str:
    """构建包含多场比赛的完整 HTML 内容。"""
    rows = "\n".join(build_match_row(m) for m in matches)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<div style="background:#1a1a2e;color:#e0e0e0;font-family:Arial,sans-serif;padding:20px;border-radius:12px;max-width:800px">
<h2 style="color:#00d4ff;text-align:center;margin-bottom:4px">\u26bd {competition_name} \u6bd4\u5206</h2>
<p style="text-align:center;color:#888;font-size:12px;margin-bottom:16px">\u66f4\u65b0\u4e8e {now} | \u6570\u636e\u6765\u6e90: Goal.com</p>
<table style="width:100%;border-collapse:collapse;color:#e0e0e0">
<thead><tr style="background:#16213e">
    <th style="padding:8px 10px;text-align:center;font-size:12px;color:#888">\u65e5\u671f</th>
    <th style="padding:8px 10px;text-align:right">\u4e3b\u961f</th>
    <th style="padding:8px 6px;text-align:center;width:40px">\u5f97\u5206</th>
    <th style="padding:8px 6px;text-align:center;width:30px"></th>
    <th style="padding:8px 6px;text-align:center;width:40px">\u5f97\u5206</th>
    <th style="padding:8px 10px;text-align:left">\u5ba2\u961f</th>
    <th style="padding:8px 10px;text-align:center">\u72b6\u6001</th>
</tr></thead>
<tbody>
{rows}
</tbody></table>
</div>"""


def build_match_title(match: dict) -> str:
    """构建比赛标题，如 \"🇧🇷 Brazil 3-1 France — Group A\"。"""
    team_a = (match.get("teamA") or {}).get("name", "TBD")
    team_b = (match.get("teamB") or {}).get("name", "TBD")
    score = match.get("score") or {}
    status = match.get("status", "")
    round_info = (match.get("round") or {}).get("name", "")

    # 标题只使用稳定字段（队名 + 轮次），比分与状态只体现在正文中；
    # 否则 LIVE→FT 或比分变化都会生成新标题，导致同一场比赛反复新建帖子。
    title = f"⚽ {team_a} vs {team_b}"

    if round_info:
        title += f" \u2014 {round_info}"

    return title


# ---------------------------------------------------------------------------
# 入库逻辑
# ---------------------------------------------------------------------------

def sync_tag(post_id: str, tag_name: str) -> None:
    """为帖子关联标签。"""
    row = select_one("tags", {"name": tag_name}, columns="id,name")
    if row:
        tag_id = row["id"]
    else:
        result = insert_one("tags", {"name": tag_name}, returning="id")
        tag_id = result["id"]
        logger.info(f"[Tag] Tag baru dibuat: {tag_name}")

    rel = select_one("post_tags", {"post_id": post_id, "tag_id": tag_id}, columns="post_id")
    if not rel:
        insert_one("post_tags", {"post_id": post_id, "tag_id": tag_id})

    rows = select_all("post_tags", {"tag_id": tag_id}, columns="*")
    count = len(rows) if rows else 1
    update_one("tags", {"posts_count": count}, {"id": tag_id})


def upsert_post(
    title: str,
    content: str,
    author_id: str,
    category_id: str,
    match_id: str,
) -> str | None:
    """新建或更新帖子，按 title 去重。"""
    now = datetime.now(timezone.utc).isoformat()

    existing = select_one("posts", {"title": title}, columns="id")
    if existing:
        post_id = existing["id"]
        update_one("posts", {"content": content, "updated_at": now}, {"id": post_id})
        logger.info(f"[\u66f4\u65b0] {title[:60]}")
    else:
        result = insert_one(
            "posts",
            {
                "title": title,
                "content": content,
                "author_id": author_id,
                "category_id": category_id,
                "post_type": "info",
                "status": "published",
                "images": [],
                "created_at": now,
                "updated_at": now,
            },
            returning="id",
        )
        post_id = result["id"]
        logger.info(f"[\u65b0\u5efa] {title[:60]}")

    sync_tag(post_id, "Piala Dunia FIFA 2026")
    sync_tag(post_id, "Skor Langsung")
    return post_id


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(save_to_db: bool = False, use_live: bool = False) -> list[dict]:
    """主抓取流程。

    Args:
        save_to_db: 是否入库到 Supabase posts 表。
        use_live: 是否同时查询实时比分 API 更新。

    Returns:
        已处理的比赛列表（含标题、比分等）。
    """
    logger.info("=== Goal.com \u4e16\u754c\u676f\u6bd4\u5206\u6293\u53d6 ===")

    # 1. 抓取全部比赛数据
    all_matches = fetch_all_match_data()
    if not all_matches:
        logger.error("\u672a\u83b7\u53d6\u5230\u6bd4\u8d5b\u6570\u636e")
        return []

    # 2. 可选：合并实时比分
    if use_live:
        live_scores = fetch_live_scores()
        for m in all_matches:
            mid = m.get("id", "")
            if mid in live_scores:
                live = live_scores[mid]
                m["status"] = live.get("status", m.get("status"))
                if live.get("totalScore"):
                    m["score"] = {
                        "teamA": live["totalScore"].get("teamA", 0),
                        "teamB": live["totalScore"].get("teamB", 0),
                    }
                m["_live_period"] = live.get("period")

    # 3. 分类：有比分 vs 未来比赛
    scored_matches: list[dict] = []
    fixture_matches: list[dict] = []
    for m in all_matches:
        status = m.get("status", "")
        if status in ("RESULT", "LIVE"):
            scored_matches.append(m)
        elif status in ("FIXTURE", "SCHEDULED"):
            fixture_matches.append(m)

    logger.info(f"\u6709\u6bd4\u5206\u7684\u6bd4\u8d5b: {len(scored_matches)} \u573a")
    logger.info(f"\u5c06\u6765\u6bd4\u8d5b: {len(fixture_matches)} \u573a")

    # 4. 数据库准备
    author_id: str | None = None
    category_id: str | None = None
    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()

    # 5. 按日期排序，处理每场比赛
    scored_matches.sort(key=lambda m: m.get("startDate", ""))

    result: list[dict] = []
    for match in scored_matches:
        title = build_match_title(match)
        content = build_html_content([match])

        team_a = (match.get("teamA") or {}).get("name", "TBD")
        team_b = (match.get("teamB") or {}).get("name", "TBD")
        score = match.get("score") or {}
        status = match.get("status", "")
        round_name = (match.get("round") or {}).get("name", "")

        print(
            f"  {status:8s} | {team_a:20s} "
            f"{str(score.get('teamA', '-')):>2} - {str(score.get('teamB', '-')):<2} "
            f"{team_b:20s} | {round_name}"
        )

        if save_to_db and author_id and category_id:
            upsert_post(title, content, author_id, category_id, match.get("id", ""))

        result.append({
            "id": match.get("id"),
            "title": title,
            "status": status,
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score.get("teamA"),
            "score_b": score.get("teamB"),
            "round": round_name,
        })

    logger.info(f"=== \u6293\u53d6\u5b8c\u6210: {len(result)} \u573a\u6709\u6bd4\u5206\u7684\u6bd4\u8d5b ===")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Goal.com \u4e16\u754c\u676f 2026 \u6bd4\u5206\u6293\u53d6")
    parser.add_argument("--save", action="store_true", help="\u5165\u5e93\u5230 Supabase posts \u8868")
    parser.add_argument(
        "--live", action="store_true", help="\u540c\u65f6\u4ece\u5b9e\u65f6\u6bd4\u5206 API \u83b7\u53d6\u6700\u65b0\u6570\u636e"
    )
    args = parser.parse_args()
    run(save_to_db=args.save, use_live=args.live)


if __name__ == "__main__":
    main()
