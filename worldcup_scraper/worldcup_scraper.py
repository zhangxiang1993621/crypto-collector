"""2026 美加墨世界杯比分爬虫 + 发帖

功能：
  - 第一次爬取：为每场比赛创建一条帖子（Sports Talk 分类，indoAdmin 用户）
  - 后续爬取：仅更新已有帖子的比分，不创建新帖子

数据源：
  - 比赛列表：FIFA 官方 API（https://fifaworldcup26.hospitality.fifa.com）
  - 实时比分：Playwright 访问 FIFA 比赛中心页面或搜狐体育提取

用法：
    python worldcup_scraper/worldcup_scraper.py                     # 仅抓取预览
    python worldcup_scraper/worldcup_scraper.py --save              # 抓取并入库
    python worldcup_scraper/worldcup_scraper.py --save --today      # 仅当天比赛
    python worldcup_scraper/worldcup_scraper.py --save --scores     # 仅更新比分
"""

import os
import re
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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

# ────────────────────── 配置 ──────────────────────
FIFA_API = "https://fifaworldcup26.hospitality.fifa.com/next-api/matches-all?productCode=26FWC&productType=5"
FIFA_MATCH_CENTRE = "https://www.fifa.com/en/match-centre/matches"
# 搜狐体育世界杯文字直播比分页（中文，结构相对稳定）
SOHU_WC_SCORES = "https://sports.sohu.com/s/2026shijiebei/"

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ────────────────────── 国家队国旗映射 ──────────────────────

# 2026 世界杯 48 支参赛队伍：英文名 → {国旗 emoji, 中文国名}
# 2026 世界杯 48 支参赛队伍：英文名（FIFA API ExternalName）→ {国旗 emoji, 中文国名}
TEAM_FLAGS: dict[str, dict[str, str]] = {
    # ── AFC 亚洲（9 队）──
    "Australia":       {"flag": "🇦🇺", "country": "澳大利亚"},
    "IR Iran":         {"flag": "🇮🇷", "country": "伊朗"},
    "Iran":            {"flag": "🇮🇷", "country": "伊朗"},
    "Iraq":            {"flag": "🇮🇶", "country": "伊拉克"},
    "Japan":           {"flag": "🇯🇵", "country": "日本"},
    "Jordan":          {"flag": "🇯🇴", "country": "约旦"},
    "Korea Republic":  {"flag": "🇰🇷", "country": "韩国"},
    "Qatar":           {"flag": "🇶🇦", "country": "卡塔尔"},
    "Saudi Arabia":    {"flag": "🇸🇦", "country": "沙特阿拉伯"},
    "Uzbekistan":      {"flag": "🇺🇿", "country": "乌兹别克斯坦"},
    # ── CAF 非洲（10 队）──
    "Algeria":         {"flag": "🇩🇿", "country": "阿尔及利亚"},
    "Cabo Verde":      {"flag": "🇨🇻", "country": "佛得角"},
    "Congo DR":        {"flag": "🇨🇩", "country": "刚果(金)"},
    "Côte d'Ivoire":   {"flag": "🇨🇮", "country": "科特迪瓦"},
    "Egypt":           {"flag": "🇪🇬", "country": "埃及"},
    "Ghana":           {"flag": "🇬🇭", "country": "加纳"},
    "Morocco":         {"flag": "🇲🇦", "country": "摩洛哥"},
    "Senegal":         {"flag": "🇸🇳", "country": "塞内加尔"},
    "South Africa":    {"flag": "🇿🇦", "country": "南非"},
    "Tunisia":         {"flag": "🇹🇳", "country": "突尼斯"},
    # ── CONCACAF 北美（6 队）──
    "Canada":          {"flag": "🇨🇦", "country": "加拿大"},
    "Curaçao":         {"flag": "🇨🇼", "country": "库拉索"},
    "Haiti":           {"flag": "🇭🇹", "country": "海地"},
    "Mexico":          {"flag": "🇲🇽", "country": "墨西哥"},
    "Panama":          {"flag": "🇵🇦", "country": "巴拿马"},
    "USA":             {"flag": "🇺🇸", "country": "美国"},
    # ── CONMEBOL 南美（6 队）──
    "Argentina":       {"flag": "🇦🇷", "country": "阿根廷"},
    "Brazil":          {"flag": "🇧🇷", "country": "巴西"},
    "Colombia":        {"flag": "🇨🇴", "country": "哥伦比亚"},
    "Ecuador":         {"flag": "🇪🇨", "country": "厄瓜多尔"},
    "Paraguay":        {"flag": "🇵🇾", "country": "巴拉圭"},
    "Uruguay":         {"flag": "🇺🇾", "country": "乌拉圭"},
    # ── OFC 大洋洲（1 队）──
    "New Zealand":     {"flag": "🇳🇿", "country": "新西兰"},
    # ── UEFA 欧洲（16 队）──
    "Austria":         {"flag": "🇦🇹", "country": "奥地利"},
    "Belgium":         {"flag": "🇧🇪", "country": "比利时"},
    "Bosnia and Herzegovina": {"flag": "🇧🇦", "country": "波黑"},
    "Croatia":         {"flag": "🇭🇷", "country": "克罗地亚"},
    "Czechia":         {"flag": "🇨🇿", "country": "捷克"},
    "England":         {"flag": "🏴", "country": "英格兰"},
    "France":          {"flag": "🇫🇷", "country": "法国"},
    "Germany":         {"flag": "🇩🇪", "country": "德国"},
    "Netherlands":     {"flag": "🇳🇱", "country": "荷兰"},
    "Norway":          {"flag": "🇳🇴", "country": "挪威"},
    "Portugal":        {"flag": "🇵🇹", "country": "葡萄牙"},
    "Scotland":        {"flag": "🏴", "country": "苏格兰"},
    "Spain":           {"flag": "🇪🇸", "country": "西班牙"},
    "Sweden":          {"flag": "🇸🇪", "country": "瑞典"},
    "Switzerland":     {"flag": "🇨🇭", "country": "瑞士"},
    "Türkiye":         {"flag": "🇹🇷", "country": "土耳其"},
}


def get_team_info(team_name: str) -> tuple[str, str]:
    """根据英文队名查找国旗和中文名

    Args:
        team_name: FIFA API 返回的英文队名，如 "Brazil"、"Korea Republic"

    Returns:
        (flag_emoji, country_cn) 元组，未匹配时返回 ("🏳️", team_name)
    """
    # 精确匹配
    # 过滤占位符（如 "1A", "W73", "FINAL", "FIXTURE" 等）
    if not team_name or team_name[0].isdigit() or team_name.startswith("W") or team_name in ("FINAL", "FIXTURE", "BRONZE"):
        return "🏳️", team_name

    if team_name in TEAM_FLAGS:
        info = TEAM_FLAGS[team_name]
        return info["flag"], info["country"]

    lower_name = team_name.lower()
    for key, info in TEAM_FLAGS.items():
        if key.lower() == lower_name:
            return info["flag"], info["country"]

    # 子串兜底（如 "IR Iran" 含 "Iran"）
    for key, info in TEAM_FLAGS.items():
        if key.lower() in lower_name or lower_name in key.lower():
            return info["flag"], info["country"]

    logger.warning(f"  未匹配国旗映射: {team_name}")
    return "🏳️", team_name


# ────────────────────── 数据库工具 ──────────────────────

def get_cat_id(client: "Client") -> str:
    """获取 Sports Talk 分类 ID"""
    name = os.environ.get("FIFA_CATEGORY_NAME", "Sports Talk")
    r = client.table("categories").select("id").eq("name", name).execute()
    if not r.data:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    return r.data[0]["id"]


def get_author_id(client: "Client") -> str:
    """获取 indoAdmin 用户 ID"""
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    r = client.table("profiles").select("id,username").eq("username", username).execute()
    if not r.data:
        logger.error(f"未找到用户: {username}")
        sys.exit(1)
    return r.data[0]["id"]


def sync_tag(client: "Client", post_id: str, tag_name: str) -> None:
    """查找或创建标签，建立 post_tags 关联"""
    r = client.table("tags").select("id,name").eq("name", tag_name).execute()
    if r.data:
        tag_id = r.data[0]["id"]
    else:
        resp = client.table("tags").insert({"name": tag_name, "posts_count": 0}).execute()
        tag_id = resp.data[0]["id"]

    rel = client.table("post_tags").select("post_id").eq(
        "post_id", post_id
    ).eq("tag_id", tag_id).execute()
    if not rel.data:
        client.table("post_tags").insert({
            "post_id": post_id, "tag_id": tag_id
        }).execute()

    count_r = client.table("post_tags").select("*", count="exact").eq("tag_id", tag_id).execute()
    count = count_r.count if count_r.count else 1
    client.table("tags").update({"posts_count": count}).eq("id", tag_id).execute()


# ────────────────────── 比赛列表 ──────────────────────

def fetch_matches_from_api() -> list[dict]:
    """从 FIFA API 获取 2026 世界杯所有比赛"""
    logger.info("正在从 FIFA API 获取比赛列表...")
    r = httpx.get(FIFA_API, timeout=30)
    r.raise_for_status()
    raw_matches = r.json()
    logger.info(f"获取到 {len(raw_matches)} 场比赛")

    matches = []
    for m in raw_matches:
        matches.append({
            "match_number": m["MatchNumber"],
            "stage": m["Stage"],
            "group": m.get("Group", ""),
            "host_team": m["HostTeam"]["ExternalName"],
            "away_team": m["OpposingTeam"]["ExternalName"],
            "venue": m["Venue"]["Name"],
            "town": m["Venue"]["Town"],
            "country": m["Venue"]["Country"],
            "match_date": m["MatchDate"],
            "match_time": m["MatchDayTime"],
            "datetime_iso": m["Date"],
            "is_knockout": m.get("IsKnockoutRound", False),
        })
    return matches


def filter_today_or_upcoming(matches: list[dict]) -> list[dict]:
    """筛选今天或未来的比赛（已开始的比赛不创建新帖）"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_matches = []
    for m in matches:
        try:
            # 解析 ISO 日期
            dt = m["datetime_iso"]
            m_date = dt[:10] if dt else ""
            if m_date >= today:
                today_matches.append(m)
        except Exception:
            today_matches.append(m)
    return today_matches


# ────────────────────── 比分抓取 ──────────────────────

def fetch_scores_from_cctv() -> dict[int, dict]:
    """从央视体育文字报道中提取最新比分

    通过 Playwright 访问搜狐世界杯页或央视体育页，抓取文字比分信息。
    返回: {match_number: {"host_goals": int, "away_goals": int, "status": str, "winner": str}}
    """
    scores: dict[int, dict] = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page()

            # 方案1: 搜狐世界杯比分页
            try:
                logger.info("  尝试从搜狐体育获取比分...")
                page.goto(SOHU_WC_SCORES, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # 提取页面上包含比分的文本
                text_content = page.evaluate("() => document.body.innerText")
                scores = _parse_sohu_text(text_content)
                if scores:
                    logger.info(f"  从搜狐体育获取到 {len(scores)} 场比赛的比分")
                    browser.close()
                    return scores
            except Exception as e:
                logger.debug(f"  搜狐体育不可达: {e}")

            # 方案2: FIFA 比赛中心
            try:
                logger.info("  尝试从 FIFA 比赛中心获取比分...")
                page.goto(FIFA_MATCH_CENTRE, timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(5000)

                # 等待比分元素出现
                score_data = page.evaluate("""() => {
                    const cards = document.querySelectorAll('[data-testid="match-card"]');
                    const results = {};
                    cards.forEach(card => {
                        const teamNames = card.querySelectorAll('[class*="team-name"]');
                        const scores = card.querySelectorAll('[class*="score"]');
                        if (teamNames.length >= 2 && scores.length >= 2) {
                            const hName = teamNames[0].textContent.trim();
                            const aName = teamNames[1].textContent.trim();
                            const hScore = parseInt(scores[0].textContent.trim()) || 0;
                            const aScore = parseInt(scores[1].textContent.trim()) || 0;
                            results[hName + '|' + aName] = {
                                host_goals: hScore, away_goals: aScore, status: 'live'
                            };
                        }
                    });
                    return JSON.stringify(results);
                }""")
                if score_data and score_data != "{}":
                    parsed = json.loads(score_data)
                    for key, val in parsed.items():
                        # 需要 match_number 才能映射，FIFA DOM 可能不暴露
                        pass
                    logger.info(f"  FIFA DOM 提取到 {len(parsed)} 条比分")
            except Exception as e:
                logger.debug(f"  FIFA 比赛中心不可达: {e}")

            # 方案3: XHR 拦截获取比分（最高效）
            try:
                logger.info("  尝试拦截 FIFA XHR 获取比分...")
                api_responses = []

                def capture_response(response):
                    if "/match-centre/api/" in response.url and response.status == 200:
                        try:
                            api_responses.append(response.json())
                        except Exception:
                            pass

                page.on("response", capture_response)
                page.goto(FIFA_MATCH_CENTRE, timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(5000)

                for resp_data in api_responses:
                    scores.update(_parse_fifa_api_response(resp_data))
            except Exception as e:
                logger.debug(f"  无法拦截 FIFA XHR: {e}")

            browser.close()

    except Exception as e:
        logger.warning(f"比分抓取异常: {e}")

    return scores


def _parse_sohu_text(text: str) -> dict[int, dict]:
    """从搜狐体育文本中解析比分

    格式示例: "巴西 3:1 阿根廷" 或 "瑞士1-1卡塔尔"
    需要与 match list 中的队伍名做模糊匹配
    """
    scores: dict[int, dict] = {}
    # 匹配比分模式: 文字 数字[:：-]数字 文字
    pattern = re.compile(
        r'([\u4e00-\u9fff\w\s]+?)\s*(\d+)\s*[:：\-–]\s*(\d+)\s*([\u4e00-\u9fff\w]+)',
    )
    for m in pattern.finditer(text):
        host_name = m.group(1).strip()
        host_goals = int(m.group(2))
        away_goals = int(m.group(3))
        away_name = m.group(4).strip()
        if len(host_name) > 1 and len(away_name) > 1:
            key = f"{host_name}|{away_name}"
            scores[key] = {"host_goals": host_goals, "away_goals": away_goals, "status": "finished"}
    return scores


def _parse_fifa_api_response(data: dict) -> dict[int, dict]:
    """从 FIFA API 响应中提取比分"""
    scores: dict[int, dict] = {}
    if isinstance(data, dict) and "Results" in data:
        for result in data.get("Results", []):
            mn = result.get("MatchNumber") or result.get("IdMatch")
            if mn:
                scores[mn] = {
                    "host_goals": result.get("HomeTeamScore", 0),
                    "away_goals": result.get("AwayTeamScore", 0),
                    "status": result.get("MatchStatus", "unknown"),
                }
    return scores


def match_scores_to_matches(
    matches: list[dict],
    scores: dict[int, dict] | dict[str, dict],
) -> None:
    """将抓取到的比分匹配到比赛列表中（原地修改）"""
    for m in matches:
        mn = m["match_number"]

        # 按 match_number 匹配
        if mn in scores:
            s = scores[mn]
            m["host_goals"] = s.get("host_goals", 0)
            m["away_goals"] = s.get("away_goals", 0)
            m["match_status"] = s.get("status", "unknown")
            continue

        # 按队伍名模糊匹配
        for key, s in scores.items():
            if isinstance(key, str):
                h = m["host_team"].lower()
                a = m["away_team"].lower()
                k = key.lower()
                if h in k and a in k:
                    m["host_goals"] = s.get("host_goals", 0)
                    m["away_goals"] = s.get("away_goals", 0)
                    m["match_status"] = s.get("status", "unknown")
                    break


# ────────────────────── 比赛状态判断 ──────────────────────

def get_match_display_status(m: dict) -> str:
    """返回比赛的中文状态标签"""
    if "match_status" in m and m["match_status"] == "finished":
        return "已结束"
    if "match_status" in m and m["match_status"] == "live":
        return "进行中"
    # 根据时间判断
    try:
        dt = datetime.fromisoformat(m["datetime_iso"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt < now:
            return "已结束"
        if dt < now.replace(hour=now.hour + 2):
            return "即将开始"
    except Exception:
        pass
    return "未开始"


def get_score_text(m: dict) -> str:
    """获取比分显示文本"""
    hg = m.get("host_goals")
    ag = m.get("away_goals")
    if hg is not None and ag is not None:
        return f"{hg} : {ag}"
    status = get_match_display_status(m)
    return f"VS ({status})"


# ────────────────────── HTML 构建 ──────────────────────

def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_post_html(m: dict) -> str:
    """构建单场比赛的帖子 HTML（含国旗、中文国名）"""
    host = _e(m["host_team"])
    away = _e(m["away_team"])
    host_flag, host_country = get_team_info(m["host_team"])
    away_flag, away_country = get_team_info(m["away_team"])
    venue = _e(f"{m['venue']} ({m['town']}, {m['country']})")
    stage = _e(m["stage"].replace("GROUP STAGE MATCHES", "小组赛"))
    stage_label = "小组赛" if "GROUP" in m["stage"] else "淘汰赛"
    group_info = f" · {m['group']}组" if m.get("group") else ""
    date_time = f"{m['match_date']} {m['match_time']}"
    score_text = get_score_text(m)
    status = get_match_display_status(m)

    # 比分高亮颜色
    if "host_goals" in m and "away_goals" in m:
        hg, ag = m["host_goals"], m["away_goals"]
        if hg > ag:
            score_color = "#e74c3c"
            winner_indicator = f' <span style="font-size:11px;color:#e74c3c;">(胜)</span>'
            loser_indicator = ""
        elif ag > hg:
            score_color = "#e74c3c"
            winner_indicator = ""
            loser_indicator = f' <span style="font-size:11px;color:#e74c3c;">(胜)</span>'
        else:
            score_color = "#f39c12"
            winner_indicator = loser_indicator = ""
    else:
        score_color = "#666"
        winner_indicator = loser_indicator = ""

    html = f"""<div style="background:linear-gradient(135deg,#0a1628,#1a2a4a);padding:20px 24px;border-radius:16px;margin:0 0 16px;color:#fff;border:1px solid rgba(255,255,255,0.1);">
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px;">
  <span style="background:rgba(255,255,255,0.1);padding:4px 12px;border-radius:20px;font-size:12px;color:#aac;">🏆 {stage_label}{group_info}</span>
  <span style="font-size:14px;color:#889;">{_e(date_time)}</span>
</div>

<div style="display:flex;align-items:center;justify-content:center;gap:20px;flex-wrap:wrap;margin-bottom:16px;">
  <div style="text-align:center;min-width:120px;">
    <div style="font-size:40px;line-height:1;">{host_flag}</div>
    <div style="font-size:32px;font-weight:bold;color:#fff;line-height:1.3;">{host}{winner_indicator}</div>
    <div style="font-size:13px;color:#889;margin-top:2px;">{_e(host_country)}</div>
  </div>
  <div style="text-align:center;min-width:80px;">
    <div style="font-size:48px;font-weight:bold;color:{score_color};line-height:1;">{score_text}</div>
    <div style="font-size:11px;color:#889;margin-top:4px;">{status}</div>
  </div>
  <div style="text-align:center;min-width:120px;">
    <div style="font-size:40px;line-height:1;">{away_flag}</div>
    <div style="font-size:32px;font-weight:bold;color:#fff;line-height:1.3;">{away}{loser_indicator}</div>
    <div style="font-size:13px;color:#889;margin-top:2px;">{_e(away_country)}</div>
  </div>
</div>

<div style="display:flex;justify-content:center;gap:16px;flex-wrap:wrap;font-size:12px;color:#889;">
  <span>📍 {venue}</span>
  <span>🔢 场次 #{m['match_number']}</span>
</div>
</div>"""
    return html


# ────────────────────── 帖子名前缀 ──────────────────────

def make_post_title(m: dict) -> str:
    """生成帖子标题，用作去重主键"""
    group = f" — {m['group']}组" if m.get("group") else ""
    stage = "小组赛" if "GROUP" in m.get("stage", "") else "淘汰赛"
    return f"⚽ {m['host_team']} vs {m['away_team']} — {stage}{group}"


# ────────────────────── 入库（upsert）──────────────────────

def upsert_post(
    client: "Client",
    m: dict,
    author_id: str,
    cat_id: str,
    tag_names: list[str],
) -> str:
    """创建或更新帖子

    - 首次爬取：标题不存在 → INSERT 新帖子
    - 后续爬取：标题已存在 → UPDATE 比分内容
    """
    title = make_post_title(m)
    content = build_post_html(m)
    now = datetime.now(timezone.utc).isoformat()

    # 检查帖子是否已存在
    r = client.table("posts").select("id").eq("title", title).execute()

    if r.data:
        post_id = r.data[0]["id"]
        client.table("posts").update({
            "content": content,
            "updated_at": now,
        }).eq("id", post_id).execute()
        logger.info(f"  [更新] {title[:60]}... 比分: {get_score_text(m)}")
    else:
        resp = client.table("posts").insert({
            "title": title[:200],
            "content": content,
            "author_id": author_id,
            "category_id": cat_id,
            "status": "published",
            "created_at": now,
            "updated_at": now,
        }).execute()
        post_id = resp.data[0]["id"]
        logger.info(f"  [新建] {title[:60]}...")

    # 同步标签
    if post_id:
        for tag in tag_names:
            sync_tag(client, post_id, tag)

    return post_id


# ────────────────────── 主流程 ──────────────────────

def run(
    save: bool = False,
    max_items: int = 20,
    today_only: bool = True,
    scores_only: bool = False,
) -> None:
    logger.info("=== 2026 美加墨世界杯比分抓取 ===")

    # ── Step 1: 获取比赛列表 ──
    all_matches = fetch_matches_from_api()

    # 按阶段排序：小组赛 → 淘汰赛
    stage_order = {"GROUP STAGE MATCHES": 0, "ROUND OF 32": 1, "ROUND OF 16": 2,
                   "QUARTER-FINAL": 3, "SEMI-FINAL": 4, "THIRD PLACE": 5, "FINAL": 6}
    all_matches.sort(key=lambda m: (stage_order.get(m["stage"], 99), m["match_number"]))

    # 筛选
    if today_only:
        matches = filter_today_or_upcoming(all_matches)
    else:
        matches = all_matches

    if max_items and len(matches) > max_items:
        matches = matches[:max_items]

    logger.info(f"处理 {len(matches)} 场比赛（共 {len(all_matches)} 场）")

    # ── Step 2: 抓取比分 ──
    scores = fetch_scores_from_cctv()
    if scores:
        match_scores_to_matches(matches, scores)

    # ── Step 3: 打印预览 ──
    print("\n" + "=" * 60)
    print("  2026 美加墨世界杯比分")
    print("=" * 60)
    for i, m in enumerate(matches):
        stage_label = "小组赛" if "GROUP" in m["stage"] else m["stage"]
        group = f" ({m['group']}组)" if m.get("group") else ""
        print(f"\n[{i + 1}] {m['host_team']} {get_score_text(m)} {m['away_team']}")
        print(f"    {stage_label}{group} | {m['match_date']} | {m['venue']}")

    if not save:
        logger.info("预览模式，不写入数据库。添加 --save 参数入库。")
        return

    # ── Step 4: 入库 ──
    client = get_client()
    author_id = get_author_id(client)
    cat_id = get_cat_id(client)

    tag_names = ["2026世界杯", "WorldCup2026", "美加墨世界杯"]
    if scores_only:
        # 仅更新已有帖子的比分
        existing = client.table("posts").select("id,title").eq(
            "category_id", cat_id
        ).execute()
        existing_titles = {d["title"].split(" — ")[0] if " — " in d["title"] else d["title"]: d["id"]
                          for d in existing.data}

        updated = 0
        for m in matches:
            title = make_post_title(m).split(" — ")[0]
            if title in existing_titles:
                content = build_post_html(m)
                pid = existing_titles[title]
                client.table("posts").update({
                    "content": content,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", pid).execute()
                updated += 1
                logger.info(f"  [比分更新] {m['host_team']} vs {m['away_team']}: {get_score_text(m)}")
        logger.info(f"比分更新完成: {updated} 条")
    else:
        # 首次爬取：为每场比赛创建帖子
        for m in matches:
            try:
                upsert_post(client, m, author_id, cat_id, tag_names)
            except Exception as e:
                logger.error(f"  入库失败 [{m['host_team']} vs {m['away_team']}]: {e}")

    logger.info("=== 完成 ===")


def main() -> None:
    p = argparse.ArgumentParser(description="2026 世界杯比分抓取")
    p.add_argument("--save", action="store_true", help="写入数据库")
    p.add_argument("--max", type=int, default=20, help="最大比赛数")
    p.add_argument("--today", action="store_true", default=True, help="仅当天及未来比赛")
    p.add_argument("--all", action="store_true", help="处理所有比赛")
    p.add_argument("--scores", action="store_true", help="仅更新已有帖子的比分")
    args = p.parse_args()

    today_only = not args.all
    run(save=args.save, max_items=args.max, today_only=today_only, scores_only=args.scores)


if __name__ == "__main__":
    main()
