"""Fastscore 印尼甲级联赛 - 实时比分+积分榜抓取 (CloakBrowser)

功能：从 Fastscore 抓取印尼 Liga 1 的实时比分和积分榜，存入 Supabase posts 表。
状态：⚠️ Fastscore 使用极严格的 Cloudflare 防护，Playwright 和 CloakBrowser 均被拦截。
      如果 Cloudflare 策略调整，本爬虫可恢复使用。
用法：
    python sport/fastscore/fastscore_scraper.py                # 仅抓取打印
    python sport/fastscore/fastscore_scraper.py --save         # 抓取并直接入库
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

TARGET_URL = "https://www.fastscore.com/football/indonesia/liga-1"
LEAGUE_NAME = "Liga 1 Indonesia"
STANDINGS_TITLE = f"Klasemen {LEAGUE_NAME} - Fastscore"
FIXTURES_TITLE = f"Skor & Jadwal {LEAGUE_NAME} - Fastscore"
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


def fetch_page_data() -> dict:
    """使用 CloakBrowser 抓取 Fastscore 比分和积分榜数据"""
    logger.info(f"访问页面: {TARGET_URL}")

    from cloakbrowser import launch

    result = {"standings": [], "matches": []}
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        title = page.title()
        logger.info(f"页面标题: {title}")

        if "Attention Required" in title or "Just a moment" in title or "blocked" in title.lower():
            logger.warning("Fastscore 被 Cloudflare 拦截，当前无法爬取")
            return result

        result = page.evaluate("""() => {
            const data = { standings: [], matches: [] };

            const standingsTable = document.querySelector('table.standings, table[class*="standings"], table[class*="table-standings"]');
            if (standingsTable) {
                const rows = standingsTable.querySelectorAll('tbody tr, tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 8) return;
                    const pos = cells[0] ? cells[0].textContent.trim() : '';
                    const team = cells[1] ? cells[1].textContent.trim() : '';
                    const mp = cells[2] ? cells[2].textContent.trim() : '';
                    const w = cells[3] ? cells[3].textContent.trim() : '';
                    const d = cells[4] ? cells[4].textContent.trim() : '';
                    const l = cells[5] ? cells[5].textContent.trim() : '';
                    const gf = cells[6] ? cells[6].textContent.trim() : '';
                    const ga = cells[7] ? cells[7].textContent.trim() : '';
                    const pts = cells[8] ? cells[8].textContent.trim() : '';
                    if (team && pos) {
                        data.standings.push({pos, team, mp, w, d, l, gf, ga, pts});
                    }
                });
            }

            const matchRows = document.querySelectorAll('tr[data-match-id], .match-row, .fixture, .live-match');
            if (matchRows.length > 0) {
                matchRows.forEach(row => {
                    const homeEl = row.querySelector('.home-team, .team-a');
                    const scoreEl = row.querySelector('.score, .match-score');
                    const awayEl = row.querySelector('.away-team, .team-b');
                    const timeEl = row.querySelector('.match-time, .kickoff');
                    if (homeEl && awayEl) {
                        data.matches.push({
                            time: timeEl ? timeEl.textContent.trim() : '',
                            home_team: homeEl.textContent.trim(),
                            away_team: awayEl.textContent.trim(),
                            score: scoreEl ? scoreEl.textContent.trim() : ''
                        });
                    }
                });
            } else {
                const allTables = document.querySelectorAll('table');
                allTables.forEach(table => {
                    const rows = table.querySelectorAll('tbody tr');
                    rows.forEach(row => {
                        const homeEl = row.querySelector('.home-team, .team-a, td:nth-child(3)');
                        const scoreEl = row.querySelector('.score, .match-score, td:nth-child(4)');
                        const awayEl = row.querySelector('.away-team, .team-b, td:nth-child(5)');
                        const timeEl = row.querySelector('.match-time, .kickoff, td:nth-child(1)');
                        if (homeEl && awayEl) {
                            const home = homeEl.textContent.trim();
                            const away = awayEl.textContent.trim();
                            if (home && away && home.length > 1 && away.length > 1) {
                                data.matches.push({
                                    time: timeEl ? timeEl.textContent.trim() : '',
                                    home_team: home,
                                    away_team: away,
                                    score: scoreEl ? scoreEl.textContent.trim() : ''
                                });
                            }
                        }
                    });
                });
            }

            return data;
        }""")

    except Exception as e:
        logger.error(f"抓取失败: {e}")
    finally:
        browser.close()

    logger.info(f"抓取到 {len(result.get('standings', []))} 条积分榜, {len(result.get('matches', []))} 场比赛")
    return result


def build_standings_html(standings: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f'<h2>Klasemen {LEAGUE_NAME}</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Fastscore</p>',
        '<hr>',
        '<table style="width:100%;border-collapse:collapse;font-size:13px">',
        '<tr style="background:#1a1a2e;color:#fff">'
        '<th style="padding:8px;text-align:center;border:1px solid #333">#</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #333">Tim</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">MP</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">W</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">D</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">L</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">GF</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">GA</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #333">Pts</th>'
        '</tr>',
    ]

    for s in standings:
        parts.append(
            '<tr>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("pos","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;font-weight:bold">{s.get("team","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("mp","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("w","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("d","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("l","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("gf","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{s.get("ga","")}</td>'
            f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee;font-weight:bold">{s.get("pts","")}</td>'
            '</tr>'
        )

    parts.append('</table>')
    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{TARGET_URL}" target="_blank">Fastscore - {LEAGUE_NAME}</a></p>')
    return '\n'.join(parts)


def build_matches_html(matches: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f'<h2>Skor & Jadwal {LEAGUE_NAME}</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Fastscore</p>',
        '<hr>',
        '<table style="width:100%;border-collapse:collapse;font-size:14px">',
        '<tr style="background:#f0f0f0">'
        '<th style="padding:8px;text-align:left;border:1px solid #ddd">Waktu</th>'
        '<th style="padding:8px;text-align:right;border:1px solid #ddd">Tim Tuan Rumah</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #ddd">Skor</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tim Tamu</th>'
        '</tr>',
    ]

    for m in matches:
        parts.append(
            '<tr>'
            f'<td style="padding:6px 8px;border:1px solid #eee">{m.get("time","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:right;font-weight:bold">{m.get("home_team","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:center">{m.get("score","vs")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:left;font-weight:bold">{m.get("away_team","")}</td>'
            '</tr>'
        )

    parts.append('</table>')
    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{TARGET_URL}" target="_blank">Fastscore - {LEAGUE_NAME}</a></p>')
    return '\n'.join(parts)


def run(save_to_db: bool = False) -> dict:
    logger.info(f"=== Fastscore {LEAGUE_NAME} 抓取 ===")

    data = fetch_page_data()
    if not data or (not data.get("standings") and not data.get("matches")):
        logger.error("未获取到有效数据")
        return {}

    standings = data.get("standings", [])
    matches = data.get("matches", [])

    logger.info(f"积分榜 {len(standings)} 条记录")
    for s in standings:
        logger.info(f"  #{s.get('pos','')} {s.get('team','')} | MP:{s.get('mp','')} Pts:{s.get('pts','')}")

    logger.info(f"比赛 {len(matches)} 场")
    for m in matches:
        logger.info(f"  {m.get('time','')} | {m.get('home_team','')} {m.get('score','')} {m.get('away_team','')}")

    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()

        if standings:
            html = build_standings_html(standings)
            upsert_post(STANDINGS_TITLE, html, author_id, category_id, TAG_NAME)

        if matches:
            html = build_matches_html(matches)
            upsert_post(FIXTURES_TITLE, html, author_id, category_id, TAG_NAME)

        logger.info("入库完成")

    logger.info("=== Fastscore 抓取完成 ===")
    return data


def main():
    parser = argparse.ArgumentParser(description=f"Fastscore {LEAGUE_NAME} 抓取")
    parser.add_argument("--save", action="store_true", help="入库到数据库")
    args = parser.parse_args()
    run(save_to_db=args.save)


if __name__ == "__main__":
    main()
