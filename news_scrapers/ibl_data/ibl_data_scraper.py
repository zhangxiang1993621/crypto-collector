"""IBL Gopay 2026 赛事数据抓取 + 发帖

功能：从 FIBA LiveStats 关联数据源抓取印尼篮球联赛(IBL)2026赛季数据
     包含：积分榜排名、赛程赛果、四节比分
数据源：7m.com.cn (FIBA LiveStats 官方数据同步)
用法：
    python ibl_data_scraper.py                    # 抓取并打印预览
    python ibl_data_scraper.py --save             # 抓取并直接入库
    python ibl_data_scraper.py --save --max 10    # 最多10条
"""

import os
import re
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_URL = "https://bdata.7m.com.cn/basketball_match_data/253/big/match_list.js"

TEAM_NAME_MAP = {
    "普利達再也": "Pelita Jaya Jakarta",
    "年輕騎士": "Satria Muda Pertamina",
    "婆羅洲犀鳥": "Bogor Hornbills",
    "德瓦聯": "Dewa United Banten",
    "漢都亞": "Hangtuah Sumsel",
    "坦格朗鷹隊": "Tangerang Hawks",
    "本加萬騎士": "Kesatria Bengawan Solo",
    "蘭斯辛巴": "Rans Simba Bogor",
    "棉蘭白頭鷹": "Rajawali Medan",
    "太平洋凱撒": "Pacific Caesar Surabaya",
    "沙提也華卡拿": "Satya Wacana Salatiga",
}

TAGS_DEFAULT = ["IBL", "Basket", "Indonesia", "FIBA", "Klasemen"]


def get_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME") or "indoAdmin"
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error("发帖账号 %s 不存在", username)
        sys.exit(1)
    logger.info("作者: %s (id=%s)", username, row["id"])
    return row["id"]


def get_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME") or "Sports Talk"
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error("未找到分类: %s", name)
        sys.exit(1)
    logger.info("分类: %s (id=%s)", name, row["id"])
    return row["id"]


def _e(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_js_array(text: str, var_name: str) -> list:
    pattern = rf"var {var_name}\s*=\s*\[(.*?)\];"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return []
    raw = match.group(1).strip()
    items = re.findall(r"'([^']*)'|\"([^\"]*)\"|(-?\d+)", raw)
    result = []
    for a, b, c in items:
        if a:
            result.append(a)
        elif b:
            result.append(b)
        else:
            result.append(int(c))
    return result


def fetch_data() -> dict:
    logger.info("抓取 IBL 数据: %s", DATA_URL)
    try:
        resp = httpx.get(
            DATA_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
                "Referer": "https://bdata.7m.com.cn/basketball_match_data/253/big/index.shtml",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("HTTP %s", resp.status_code)
            return {}

        js_text = resp.text

        standings = []
        names = parse_js_array(js_text, "mr_name")
        wins = parse_js_array(js_text, "mr_w")
        losses = parse_js_array(js_text, "mr_l")
        gf = parse_js_array(js_text, "mr_g")
        ga = parse_js_array(js_text, "mr_m")
        diff = parse_js_array(js_text, "mr_d")

        for i in range(len(names)):
            en_name = TEAM_NAME_MAP.get(names[i], names[i])
            standings.append({
                "rank": i + 1,
                "team": en_name,
                "played": (wins[i] + losses[i]) if i < len(wins) and i < len(losses) else 0,
                "won": wins[i] if i < len(wins) else 0,
                "lost": losses[i] if i < len(losses) else 0,
                "pf": gf[i] if i < len(gf) else 0,
                "pa": ga[i] if i < len(ga) else 0,
                "diff": diff[i] if i < len(diff) else 0,
            })

        matches = []
        start_times = parse_js_array(js_text, "Start_time_arr")
        team_a = parse_js_array(js_text, "TeamA_arr")
        team_b = parse_js_array(js_text, "TeamB_arr")
        score_a = parse_js_array(js_text, "TeamA_score_out_arr")
        score_b = parse_js_array(js_text, "TeamB_score_out_arr")
        q1a = parse_js_array(js_text, "TeamA_score1_arr")
        q2a = parse_js_array(js_text, "TeamA_score2_arr")
        q3a = parse_js_array(js_text, "TeamA_score3_arr")
        q4a = parse_js_array(js_text, "TeamA_score4_arr")
        q1b = parse_js_array(js_text, "TeamB_score1_arr")
        q2b = parse_js_array(js_text, "TeamB_score2_arr")
        q3b = parse_js_array(js_text, "TeamB_score3_arr")
        q4b = parse_js_array(js_text, "TeamB_score4_arr")
        states = parse_js_array(js_text, "State_arr")

        for i in range(len(start_times)):
            if i >= len(team_a):
                break
            time_str = start_times[i] if isinstance(start_times[i], str) else ""
            parts = [int(x) for x in time_str.split(",")] if time_str else []
            if len(parts) >= 5:
                date_str = f"{parts[0]}-{parts[1]:02d}-{parts[2]:02d}"
                time_s = f"{parts[3]:02d}:{parts[4]:02d}"
            else:
                date_str = ""
                time_s = ""

            home = TEAM_NAME_MAP.get(team_a[i], team_a[i]) if i < len(team_a) else ""
            away = TEAM_NAME_MAP.get(team_b[i], team_b[i]) if i < len(team_b) else ""
            state = states[i] if i < len(states) else 0

            qs = [0, 0, 0, 0]
            qa_list = [q1a, q2a, q3a, q4a]
            qb_list = [q1b, q2b, q3b, q4b]
            for qi in range(4):
                a_val = qa_list[qi][i] if i < len(qa_list[qi]) else 0
                b_val = qb_list[qi][i] if i < len(qb_list[qi]) else 0
                qs[qi] = a_val - b_val if isinstance(a_val, int) and isinstance(b_val, int) else 0

            sa = score_a[i] if i < len(score_a) else 0
            sb = score_b[i] if i < len(score_b) else 0
            if sa == 0 and sb == 0:
                for qi in range(4):
                    a_val = qa_list[qi][i] if i < len(qa_list[qi]) else 0
                    b_val = qb_list[qi][i] if i < len(qb_list[qi]) else 0
                    if isinstance(a_val, int):
                        sa += a_val
                    if isinstance(b_val, int):
                        sb += b_val

            matches.append({
                "date": date_str,
                "time": time_s,
                "home": home,
                "away": away,
                "score_home": sa,
                "score_away": sb,
                "state": state,
                "quarters": qs,
            })

        result = {
            "standings": standings,
            "matches": matches,
        }
        logger.info("积分榜 %d 队 | 赛程 %d 场", len(standings), len(matches))
        return result

    except Exception as e:
        logger.error("数据抓取异常: %s", e)
        return {}


def build_standings_html(teams: list[dict]) -> str:
    rows = []
    for t in teams[:12]:
        diff_str = f"+{t['diff']}" if t["diff"] > 0 else str(t["diff"])
        rows.append(
            '<tr>'
            f'<td style="padding:4px 8px;text-align:center;font-weight:bold;">{t["rank"]}</td>'
            f'<td style="padding:4px 8px;">{_e(t["team"])}</td>'
            f'<td style="padding:4px 8px;text-align:center;">{t["played"]}</td>'
            f'<td style="padding:4px 8px;text-align:center;color:#2e7d32;font-weight:bold;">{t["won"]}</td>'
            f'<td style="padding:4px 8px;text-align:center;color:#c62828;">{t["lost"]}</td>'
            f'<td style="padding:4px 8px;text-align:center;">{diff_str}</td>'
            '</tr>'
        )

    return (
        '<div style="background:#fff3e0;padding:12px 16px;border-radius:8px;margin:0 0 14px;'
        'border-left:4px solid #ff6f00;">'
        '<p style="font-weight:bold;color:#e65100;margin:0 0 10px;font-size:16px;">'
        '<span style="background:#ff6f00;color:#fff;padding:2px 8px;border-radius:4px;'
        'font-size:11px;margin-right:6px;">🏀 IBL</span>'
        'Klasemen IBL Gopay 2026</p>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#ffe0b2;">'
        '<th style="padding:4px;">#</th><th style="padding:4px;text-align:left;">Tim</th>'
        '<th style="padding:4px;">GP</th><th style="padding:4px;">W</th>'
        '<th style="padding:4px;">L</th><th style="padding:4px;">+/-</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows)
        + '</tbody></table>'
        '<p style="margin-top:8px;font-size:11px;color:#888;">Sumber: FIBA LiveStats</p>'
        '</div>'
    )


def build_results_html(matches: list[dict]) -> str:
    played = [m for m in matches if m["state"] == 9]
    played.sort(key=lambda m: m["date"] + m["time"], reverse=True)
    recent = played[:15]

    rows = []
    for m in recent:
        cells = []
        cells.append('<td style="padding:4px 6px;font-size:11px;white-space:nowrap;">'
                     f'{m["date"]}<br/>{m["time"]}</td>')
        cells.append('<td style="padding:4px 8px;text-align:right;font-weight:bold;">'
                     f'{_e(m["home"])}</td>')

        qs = m.get("quarters", [0, 0, 0, 0])
        q_str = "-".join(str(abs(q)) for q in qs)
        cells.append(
            '<td style="padding:4px 6px;text-align:center;font-size:15px;font-weight:bold;">'
            f'{m["score_home"]}-{m["score_away"]}</td>'
        )
        cells.append(f'<td style="padding:4px 8px;">{_e(m["away"])}</td>')
        cells.append(f'<td style="padding:4px 6px;font-size:11px;color:#888;">({q_str})</td>')

        rows.append(f'<tr>{"".join(cells)}</tr>')

    upcoming = [m for m in matches if m["state"] in (11, 0) and m["date"]]
    upcoming = sorted(upcoming, key=lambda m: m["date"] + m["time"])[:5]

    upcoming_rows = []
    for m in upcoming:
        upcoming_rows.append(
            '<tr>'
            f'<td style="padding:4px 6px;font-size:11px;">{m["date"]}<br/>{m["time"]}</td>'
            f'<td style="padding:4px 8px;text-align:right;">{_e(m["home"])}</td>'
            '<td style="padding:4px 6px;text-align:center;color:#888;">vs</td>'
            f'<td style="padding:4px 8px;">{_e(m["away"])}</td>'
            '</tr>'
        )

    parts = [
        '<div style="background:#e8eaf6;padding:12px 16px;border-radius:8px;margin:14px 0;'
        'border-left:4px solid #283593;">'
        '<p style="font-weight:bold;color:#1a237e;margin:0 0 10px;font-size:16px;">'
        '<span style="background:#283593;color:#fff;padding:2px 8px;border-radius:4px;'
        'font-size:11px;margin-right:6px;">📊</span>'
        'Hasil Pertandingan IBL Gopay 2026</p>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#c5cae9;">'
        '<th style="padding:4px;">Tanggal</th><th style="padding:4px;text-align:right;">Kandang</th>'
        '<th style="padding:4px;">Skor</th><th style="padding:4px;">Tandang</th>'
        '<th style="padding:4px;">Kuartal</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows)
        + '</tbody></table>'
        '<p style="margin-top:8px;font-size:11px;color:#888;">Sumber: FIBA LiveStats</p>'
        '</div>',
    ]

    if upcoming:
        parts.append(
            '<div style="background:#e0f2f1;padding:12px 16px;border-radius:8px;margin:14px 0;'
            'border-left:4px solid #00695c;">'
            '<p style="font-weight:bold;color:#004d40;margin:0 0 10px;font-size:16px;">'
            '<span style="background:#00695c;color:#fff;padding:2px 8px;border-radius:4px;'
            'font-size:11px;margin-right:6px;">📅</span>'
            'Jadwal Mendatang</p>'
            '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
            + "\n".join(upcoming_rows)
            + '</table></div>'
        )

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


def run(save: bool = False, max_items: int = 3):
    logger.info("=== IBL Gopay 2026 赛事数据 ===")

    data = fetch_data()
    standings = data.get("standings", [])
    matches = data.get("matches", [])

    if not standings and not matches:
        logger.warning("无数据")
        return

    print("\n" + "=" * 60)
    print("  IBL Gopay 2026 赛事数据")
    print("=" * 60)

    if standings:
        print("\n[积分榜]")
        for t in standings[:11]:
            diff_str = f"+{t['diff']}" if t["diff"] > 0 else str(t["diff"])
            print(f"  {t['rank']:>2}. {t['team'][:25]:<25}  {t['won']:>2}-{t['lost']:<2}  Diff:{diff_str}")

    if matches:
        played = [m for m in matches if m["state"] == 9]
        print(f"\n[赛果] 已完赛 {len(played)} 场，最近 5 场:")
        played_by_date = sorted(played, key=lambda m: m["date"] + m["time"], reverse=True)
        for m in played_by_date[:5]:
            print(f"  {m['date']} {m['time']}  {m['home'][:20]} {m['score_home']}-{m['score_away']} {m['away'][:20]}")

    if save:
        author_id = get_author_id()
        category_id = get_category_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        if standings:
            content = build_standings_html(standings)
            tags = TAGS_DEFAULT + ["Klasemen", "Standings"]
            try:
                result = insert_one("posts", {
                    "title": "🏀 Klasemen IBL Gopay 2026",
                    "content": content,
                    "author_id": author_id,
                    "category_id": category_id,
                    "post_type": "info",
                    "status": "pending_review",
                    "created_at": now,
                    "updated_at": now,
                }, returning="id")
                sync_tags(result["id"], tags)
                saved += 1
                logger.info("  [入库] Klasemen IBL Gopay 2026 | tags=%s", tags[:5])
            except Exception as e:
                logger.error("  入库失败: %s", e)

        if matches:
            content = build_results_html(matches)
            tags = TAGS_DEFAULT + ["Pertandingan", "Skor", "Jadwal"]
            try:
                result = insert_one("posts", {
                    "title": "📊 Hasil & Jadwal IBL Gopay 2026",
                    "content": content,
                    "author_id": author_id,
                    "category_id": category_id,
                    "post_type": "info",
                    "status": "pending_review",
                    "created_at": now,
                    "updated_at": now,
                }, returning="id")
                sync_tags(result["id"], tags)
                saved += 1
                logger.info("  [入库] Hasil & Jadwal IBL 2026 | tags=%s", tags[:5])
            except Exception as e:
                logger.error("  入库失败: %s", e)

        logger.info("[入库] %d 条", saved)

    logger.info("=== 完成 ===")


def main():
    parser = argparse.ArgumentParser(description="IBL Gopay 2026 赛事数据抓取")
    parser.add_argument("--save", action="store_true", help="写入数据库")
    parser.add_argument("--max", type=int, default=3, help="最大入库条目数")
    args = parser.parse_args()
    run(save=args.save, max_items=args.max)


if __name__ == "__main__":
    main()
