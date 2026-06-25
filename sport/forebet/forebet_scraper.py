"""Forebet 印尼甲级联赛 - 赛果+赛程抓取 (CloakBrowser)

功能：从 Forebet 抓取印尼 Liga 1 的比赛结果和赛程，存入 Supabase posts 表。
用法：
    python sport/forebet/forebet_scraper.py                  # 仅抓取打印
    python sport/forebet/forebet_scraper.py --save           # 抓取并直接入库
"""

import os
import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, update_one

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

TARGET_URL = "https://www.forebet.com/en/football-tips-and-predictions-for-indonesia/liga-1"
LEAGUE_NAME = "Liga 1 Indonesia"
POST_TITLE = f"Hasil & Jadwal {LEAGUE_NAME} - Forebet"
STANDINGS_TITLE = f"Klasemen {LEAGUE_NAME} - Forebet"
TAG_NAME = LEAGUE_NAME


def lookup_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error(f"未找到作者: {username}")
        sys.exit(1)
    logger.info(f"作者: {row['username']} (id={row['id']})")
    return row["id"]


def lookup_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME") or "Sports Talk"
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error(f"未找到分类: {name}")
        sys.exit(1)
    logger.info(f"分类: {row['name']} (id={row['id']})")
    return row["id"]


def sync_tag(post_id: str, tag_name: str) -> None:
    row = select_one("tags", {"name": tag_name}, columns="id,name")
    if row:
        tag_id = row["id"]
    else:
        result = insert_one("tags", {"name": tag_name}, returning="id")
        tag_id = result["id"]
        logger.info(f"[Tag] 创建新标签: {tag_name}")

    rel = select_one("post_tags", {"post_id": post_id, "tag_id": tag_id}, columns="post_id")
    if not rel:
        insert_one("post_tags", {"post_id": post_id, "tag_id": tag_id})

    rows = select_all("post_tags", {"tag_id": tag_id}, columns="*")
    count = len(rows) if rows else 1
    update_one("tags", {"posts_count": count}, {"id": tag_id})


def upsert_post(title: str, content: str, author_id: str,
                category_id: str, tag_name: str) -> str:
    row = select_one("posts", {"title": title}, columns="id")
    now = datetime.now(timezone.utc).isoformat()

    if row:
        post_id = row["id"]
        update_one("posts", {"content": content, "updated_at": now}, {"id": post_id})
        logger.info(f"[更新] 帖子已存在，内容已更新: {title}")
    else:
        result = insert_one("posts", {
            "title": title,
            "content": content,
            "author_id": author_id,
            "category_id": category_id,
            "post_type": "info",
            "status": "pending_review",
            "created_at": now,
            "updated_at": now,
        }, returning="id")
        post_id = result["id"]
        logger.info(f"[新建] 帖子已创建: {title}")

    sync_tag(post_id, tag_name)
    return post_id


def fetch_data() -> dict:
    """使用 CloakBrowser 抓取 Forebet 赛果和积分榜"""
    logger.info(f"访问页面: {TARGET_URL}")

    from cloakbrowser import launch

    result = {"matches": [], "standings": []}
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)

        data = page.evaluate("""() => {
            const result = { matches: [], standings: [], round: '' };

            // 获取当前轮次
            const roundEl = document.querySelector('.schema .schema__title, .schema h2');
            if (roundEl) {
                result.round = roundEl.textContent.trim();
            }

            // 抓取比赛数据 (schema 容器内 rcnt 行)
            const matchRows = document.querySelectorAll('.rcnt');
            matchRows.forEach(row => {
                const text = row.textContent.trim();
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                if (lines.length < 3) return;

                // Forebet 格式: "IdXX HomeTeam AwayTeam date time ... FT score (half) extra"
                // 第一行是编号，第二行是主队，第三行是客队
                const matchId = lines[0] || '';
                const homeTeam = lines[1] || '';
                const awayTeam = lines[2] || '';

                // 查找日期（格式: DD/MM/YYYY HH:MM）
                let date = '';
                let score = '';
                let status = '';

                for (const line of lines) {
                    const dateMatch = line.match(/(\\d{2}\\/\\d{2}\\/\\d{4})\\s+(\\d{2}:\\d{2})/);
                    if (dateMatch) {
                        date = dateMatch[0];
                    }
                    if (line.includes('FT') || line.includes('OT') || line.includes('AP')) {
                        status = line.match(/(FT|OT|AP|Postp\\.|Cancl\\.)/);
                        status = status ? status[1] : '';
                    }
                    const scoreMatch = line.match(/(\\d+)\\s*[-:]\\s*(\\d+)/);
                    if (scoreMatch && !dateMatch) {
                        score = scoreMatch[0];
                    }
                }

                if (homeTeam && awayTeam && homeTeam.length > 1 && awayTeam.length > 1) {
                    result.matches.push({
                        id: matchId,
                        home_team: homeTeam,
                        away_team: awayTeam,
                        date: date,
                        score: score || '',
                        status: status
                    });
                }
            });

            // 抓取积分榜
            const standingsTable = document.querySelector('.standings');
            if (standingsTable) {
                const rows = standingsTable.querySelectorAll('tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 8) return;
                    const vals = Array.from(cells).map(c => c.textContent.trim());
                    const pos = vals[0] || '';
                    if (!pos || isNaN(Number(pos))) return;

                    let team = vals[1] || '';
                    let teamIdx = 1;
                    // 跳过空的/数字的单元格找到真正的队名
                    for (let i = 1; i < vals.length && i < 4; i++) {
                        if (vals[i] && isNaN(Number(vals[i])) && vals[i].length > 2) {
                            team = vals[i];
                            teamIdx = i;
                            break;
                        }
                    }

                    const pts = vals[teamIdx + 1] || '';
                    const gp = vals[teamIdx + 2] || '';
                    const w = vals[teamIdx + 3] || '';
                    const d = vals[teamIdx + 4] || '';
                    const l = vals[teamIdx + 5] || '';

                    result.standings.push({
                        pos, team, pts, gp, w, d, l
                    });
                });
            }

            return result;
        }""")

        result.update(data)

    except Exception as e:
        logger.error(f"抓取失败: {e}")
    finally:
        browser.close()

    logger.info(f"抓取到 {len(result.get('matches', []))} 场比赛, {len(result.get('standings', []))} 条积分榜")
    return result


def build_matches_html(matches: list[dict], round_info: str = "") -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f'<h2>Hasil & Jadwal {LEAGUE_NAME}</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Forebet</p>',
    ]
    if round_info:
        parts.append(f'<p style="font-weight:bold;color:#333">{round_info}</p>')
    parts.extend([
        '<hr>',
        '<table style="width:100%;border-collapse:collapse;font-size:14px">',
        '<tr style="background:#f0f0f0">'
        '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tanggal</th>'
        '<th style="padding:8px;text-align:right;border:1px solid #ddd">Tim Tuan Rumah</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #ddd">Skor</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tim Tamu</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #ddd">Status</th>'
        '</tr>',
    ])

    for m in matches:
        status = m.get("status", "")
        status_color = "#e74c3c" if status == "LIVE" else "#27ae60" if status == "FT" else "#666"
        parts.append(
            '<tr>'
            f'<td style="padding:6px 8px;border:1px solid #eee;font-size:12px">{m.get("date","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:right;font-weight:bold">{m.get("home_team","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:center;font-size:16px;font-weight:bold">{m.get("score","vs")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:left;font-weight:bold">{m.get("away_team","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:center;font-size:12px;color:{status_color}">{status}</td>'
            '</tr>'
        )

    parts.append('</table>')
    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{TARGET_URL}" target="_blank">Forebet - {LEAGUE_NAME}</a></p>')
    return '\n'.join(parts)


def build_standings_html(standings: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f'<h2>Klasemen {LEAGUE_NAME}</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Forebet</p>',
        '<hr>',
        '<table style="width:100%;border-collapse:collapse;font-size:13px">',
        '<tr style="background:#1a1a2e;color:#fff">'
        '<th style="padding:8px;text-align:center;border:1px solid #333">#</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #333">Tim</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">Pts</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">GP</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">W</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">D</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">L</th>'
        '</tr>',
    ]

    for s in standings:
        parts.append(
            '<tr>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("pos","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;font-weight:bold">{s.get("team","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee;font-weight:bold">{s.get("pts","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("gp","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("w","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("d","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("l","")}</td>'
            '</tr>'
        )

    parts.append('</table>')
    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{TARGET_URL}" target="_blank">Forebet - {LEAGUE_NAME}</a></p>')
    return '\n'.join(parts)


def run(save_to_db: bool = False) -> dict:
    logger.info(f"=== Forebet {LEAGUE_NAME} 抓取 ===")

    data = fetch_data()
    if not data or (not data.get("matches") and not data.get("standings")):
        logger.error("未获取到有效数据")
        return {}

    matches = data.get("matches", [])
    standings = data.get("standings", [])
    round_info = data.get("round", "")

    logger.info(f"轮次: {round_info}")
    logger.info(f"比赛: {len(matches)} 场")
    for m in matches[:20]:
        logger.info(f"  {m.get('date','')} | {m.get('home_team','')} {m.get('score','-')} {m.get('away_team','')} [{m.get('status','')}]")

    logger.info(f"积分榜: {len(standings)} 条")
    for s in standings[:5]:
        logger.info(f"  #{s.get('pos','')} {s.get('team','')} Pts:{s.get('pts','')} GP:{s.get('gp','')}")

    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()

        if matches:
            html = build_matches_html(matches, round_info)
            upsert_post(POST_TITLE, html, author_id, category_id, TAG_NAME)

        if standings:
            html = build_standings_html(standings)
            upsert_post(STANDINGS_TITLE, html, author_id, category_id, TAG_NAME)

        logger.info("入库完成")

    logger.info("=== Forebet 抓取完成 ===")
    return data


def main():
    parser = argparse.ArgumentParser(description=f"Forebet {LEAGUE_NAME} 赛果抓取")
    parser.add_argument("--save", action="store_true", help="入库到数据库")
    args = parser.parse_args()
    run(save_to_db=args.save)


if __name__ == "__main__":
    main()
