"""Footballant 印尼超级联赛 - 赛程+统计抓取 (CloakBrowser)

功能：从 Footballant 抓取印尼 Super League 的赛程和统计数据，存入 Supabase posts 表。
状态：⚠️ 需要确认正确的 League ID。网站使用 /football-data/league/{id} 格式，
      用户提供的原始 URL (footballant.com/league/indonesia-super-league) 已失效。
      请在 Footballant 首页搜索 "BRI Liga 1" 找到正确的联赛 ID 后更新 LEAGUE_ID。
用法：
    python sport/footballant/footballant_scraper.py                  # 仅抓取打印
    python sport/footballant/footballant_scraper.py --save           # 抓取并直接入库
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

# TODO: 在 Footballant 首页搜索 "BRI Liga 1" 找到正确 ID 后替换此处
LEAGUE_ID = 9999  # 待确认：印尼 Liga 1 在 Footballant 的 League ID
TARGET_URL = f"https://www.footballant.com/football-data/league/{LEAGUE_ID}"
LEAGUE_NAME = "Liga 1 Indonesia"
SCHEDULE_TITLE = f"Jadwal {LEAGUE_NAME} - Footballant"
STATS_TITLE = f"Statistik {LEAGUE_NAME} - Footballant"
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
    """使用 CloakBrowser 抓取 Footballant 赛程和统计数据"""
    logger.info(f"访问页面: {TARGET_URL} (League ID: {LEAGUE_ID})")

    from cloakbrowser import launch

    result = {"matches": [], "stats": [], "standings": []}
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        title = page.title()
        logger.info(f"页面标题: {title}")

        if "404" in title or "Not Found" in title:
            logger.error(f"League {LEAGUE_ID} 不存在。请在 Footballant 首页搜索 'BRI Liga 1' 找到正确的 ID 后更新 LEAGUE_ID")
            return result

        result = page.evaluate("""() => {
            const data = { matches: [], stats: [], standings: [] };

            // 赛程抓取
            const allTables = document.querySelectorAll('table');
            allTables.forEach(table => {
                const rows = table.querySelectorAll('tbody tr, tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 3) return;
                    const texts = Array.from(cells).map(c => c.textContent.trim()).filter(Boolean);
                    if (texts.length >= 3) {
                        let homeIdx = texts.findIndex(t => t.length > 2 && !/^\\d{1,2}[:\/-]\\d{1,2}$/.test(t) && !/^\\d+$/.test(t));
                        if (homeIdx >= 0 && homeIdx + 2 < texts.length) {
                            const nextText = texts[homeIdx + 1] || '';
                            const isScore = /^\\d{1,2}\\s*[-:]\\s*\\d{1,2}$/.test(nextText);
                            data.matches.push({
                                date: texts[0] || '',
                                home_team: texts[homeIdx],
                                away_team: texts[homeIdx + (isScore ? 2 : 1)],
                                score: isScore ? nextText : ''
                            });
                        }
                    }
                });
            });

            // 积分榜
            const standingsTable = document.querySelector('table[class*="standing"], table.standings');
            if (standingsTable) {
                const rows = standingsTable.querySelectorAll('tbody tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 8) {
                        const vals = Array.from(cells).map(c => c.textContent.trim());
                        data.standings.push({
                            pos: vals[0], team: vals[1], mp: vals[2],
                            w: vals[3], d: vals[4], l: vals[5],
                            gf: vals[6], ga: vals[7], pts: vals[8] || ''
                        });
                    }
                });
            }

            // 统计数据
            const statContainers = document.querySelectorAll('[class*="stats"], [class*="top-scorer"], [class*="top-players"]');
            statContainers.forEach(container => {
                const rows = container.querySelectorAll('tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 3) {
                        const name = cells[1] ? cells[1].textContent.trim() : '';
                        const value = cells[2] ? cells[2].textContent.trim() : '';
                        if (name && value) {
                            data.stats.push({ name, value });
                        }
                    }
                });
            });

            return data;
        }""")

    except Exception as e:
        logger.error(f"抓取失败: {e}")
    finally:
        browser.close()

    logger.info(f"抓取到 {len(result.get('matches', []))} 场比赛, "
                f"{len(result.get('standings', []))} 条积分榜, "
                f"{len(result.get('stats', []))} 条统计")
    return result


def build_schedule_html(matches: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f'<h2>Jadwal {LEAGUE_NAME}</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Footballant</p>',
        '<hr>',
        '<table style="width:100%;border-collapse:collapse;font-size:14px">',
        '<tr style="background:#f0f0f0">'
        '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tanggal</th>'
        '<th style="padding:8px;text-align:right;border:1px solid #ddd">Tim Tuan Rumah</th>'
        '<th style="padding:8px;text-align:center;border:1px solid #ddd">Skor</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tim Tamu</th>'
        '</tr>',
    ]

    for m in matches:
        parts.append(
            '<tr>'
            f'<td style="padding:6px 8px;border:1px solid #eee">{m.get("date","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:right;font-weight:bold">{m.get("home_team","")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:center">{m.get("score","vs")}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:left;font-weight:bold">{m.get("away_team","")}</td>'
            '</tr>'
        )

    parts.append('</table>')
    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{TARGET_URL}" target="_blank">Footballant - {LEAGUE_NAME}</a></p>')
    return '\n'.join(parts)


def build_stats_html(standings: list[dict], stats: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f'<h2>Statistik {LEAGUE_NAME}</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Footballant</p>',
        '<hr>',
    ]

    if standings:
        parts.append('<h3>Klasemen</h3>')
        parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px">')
        parts.append(
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
            '</tr>'
        )
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
        parts.append('<br>')

    if stats:
        parts.append('<h3>Statistik Pemain</h3>')
        parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
        parts.append(
            '<tr style="background:#f0f0f0">'
            '<th style="padding:8px;text-align:left;border:1px solid #ddd">Pemain</th>'
            '<th style="padding:8px;text-align:center;border:1px solid #ddd">Statistik</th>'
            '</tr>'
        )
        for s in stats:
            parts.append(
                '<tr>'
                f'<td style="padding:6px 8px;border:1px solid #eee">{s["name"]}</td>'
                f'<td style="padding:6px 8px;border:1px solid #eee;text-align:center">{s["value"]}</td>'
                '</tr>'
            )
        parts.append('</table>')

    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{TARGET_URL}" target="_blank">Footballant</a></p>')
    return '\n'.join(parts)


def run(save_to_db: bool = False) -> dict:
    logger.info(f"=== Footballant {LEAGUE_NAME} 抓取 ===")

    data = fetch_page_data()
    if not data:
        logger.error("未获取到有效数据")
        return {}

    matches = data.get("matches", [])
    standings = data.get("standings", [])
    stats = data.get("stats", [])

    logger.info(f"赛程 {len(matches)} 场")
    for m in matches:
        logger.info(f"  {m.get('date','')} | {m.get('home_team','')} {m.get('score','vs')} {m.get('away_team','')}")

    logger.info(f"积分榜 {len(standings)} 条")
    for s in standings:
        logger.info(f"  #{s.get('pos','')} {s.get('team','')} Pts:{s.get('pts','')}")

    logger.info(f"统计 {len(stats)} 条")
    for s in stats:
        logger.info(f"  {s.get('name','')}: {s.get('value','')}")

    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()

        if matches:
            html = build_schedule_html(matches)
            upsert_post(SCHEDULE_TITLE, html, author_id, category_id, TAG_NAME)

        if standings or stats:
            html = build_stats_html(standings, stats)
            upsert_post(STATS_TITLE, html, author_id, category_id, TAG_NAME)

        logger.info("入库完成")

    logger.info("=== Footballant 抓取完成 ===")
    return data


def main():
    parser = argparse.ArgumentParser(description=f"Footballant {LEAGUE_NAME} 抓取")
    parser.add_argument("--save", action="store_true", help="入库到数据库")
    args = parser.parse_args()
    run(save_to_db=args.save)


if __name__ == "__main__":
    main()
