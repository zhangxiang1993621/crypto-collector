"""BWF 印尼羽毛球赛事 - Indonesia Open & Masters 结果抓取

功能：从 BWF World Tour 抓取印尼羽毛球公开赛和大师赛的比赛结果和冠军信息，存入 Supabase posts 表。
数据源：
  - POLYTRON Indonesia Open 2026 (Super 1000): bwfworldtour.bwfbadminton.com/tournament/5528/
  - DAIHATSU Indonesia Masters 2026 (Super 500): bwfworldtour.bwfbadminton.com/tournament/5529/
用法：
    python sport/badminton/bwf_indonesia_scraper.py                # 仅抓取打印
    python sport/badminton/bwf_indonesia_scraper.py --save        # 抓取并直接入库
"""

import os
import sys
import logging
import argparse
import re
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

# BWF World Tour 印尼赛事 URL
TOURNAMENTS = [
    {
        "id": "5528",
        "name": "POLYTRON Indonesia Open 2026",
        "slug": "polytron-indonesia-open-2026",
        "level": "HSBC BWF WORLD TOUR SUPER 1000",
        "url": "https://bwfworldtour.bwfbadminton.com/tournament/5528/polytron-indonesia-open-2026/results/",
        "tag": "Indonesia Open 2026",
        "month": "June",
    },
    {
        "id": "5529",
        "name": "DAIHATSU Indonesia Masters 2026",
        "slug": "daihatsu-indonesia-masters-2026",
        "level": "HSBC BWF WORLD TOUR SUPER 500",
        "url": "https://bwfworldtour.bwfbadminton.com/tournament/5529/daihatsu-indonesia-masters-2026/results/",
        "tag": "Indonesia Masters 2026",
        "month": "January",
    },
]

TAG_NAME = "Bulu Tangkis Indonesia"


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


def fetch_tournament_results(url: str) -> dict:
    """使用 CloakBrowser 抓取 BWF 赛事结果"""
    logger.info(f"访问: {url}")

    from cloakbrowser import launch

    result = {
        "title": "", "date": "", "venue": "", "prize": "",
        "level": "", "categories": []
    }

    browser = launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        title = page.title()
        if "404" in title or "not found" in title.lower():
            logger.warning(f"页面不存在: {url}")
            return result

        result = page.evaluate("""() => {
            const result = {
                title: '', date: '', venue: '', prize: '',
                level: '', categories: [], raw_text: ''
            };

            // 提取可见文本
            const text = document.body.innerText;
            result.raw_text = text;

            // 找标题（赛事名称）
            const heading = document.querySelector('h1, h2.title, .tournament-title, [class*="title"]');
            if (heading) {
                result.title = heading.textContent.trim();
            }

            // 解析文本提取结构化数据
            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);

            // 找日期、场馆、奖金信息
            for (let i = 0; i < Math.min(30, lines.length); i++) {
                const line = lines[i];
                // 日期格式: DD - DD MONTH / DD MONTH - DD MONTH
                if (line.match(/\\d+\\s*-\\s*\\d+\\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)/i) || line.match(/(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)/i)) {
                    result.date = line;
                }
                // Istora / Jakarta / 奖金
                if (/ISTORA/i.test(line) || /SENAYAN/i.test(line) || /JAKARTA/i.test(line)) {
                    result.venue = line;
                }
                if (/USD\\s*\\d/i.test(line)) {
                    result.prize = line;
                }
                if (/SUPER\\s*(1000|750|500|300|100)/i.test(line)) {
                    result.level = line;
                }
            }

            // 提取各项目冠军
            const categories = [];
            const categoryNames = ['MEN\\'S SINGLES', 'WOMEN\\'S SINGLES', 'MEN\\'S DOUBLES', 'WOMEN\\'S DOUBLES', 'MIXED DOUBLES'];
            const lines2 = text.split('\\n').map(l => l.trim());

            for (const catName of categoryNames) {
                let catIdx = -1;
                for (let i = 0; i < lines2.length; i++) {
                    if (lines2[i].includes(catName)) { catIdx = i; break; }
                }
                if (catIdx < 0) continue;

                const category = { name: catName, entries: [] };
                // 找下一个 category 的位置
                let nextCatIdx = lines2.length;
                for (const nextCat of categoryNames) {
                    if (nextCat === catName) continue;
                    for (let i = catIdx + 1; i < lines2.length; i++) {
                        if (lines2[i].includes(nextCat)) {
                            if (i < nextCatIdx) nextCatIdx = i;
                            break;
                        }
                    }
                }

                // 提取该类别内的条目（排名 1, 2, 3/4）
                for (let i = catIdx; i < nextCatIdx && category.entries.length < 4; i++) {
                    const line = lines2[i];
                    if (line === '1' || line === '2' || line === '3/4') {
                        const rank = line;
                        // 名字在接下来几行
                        const nameLines = [];
                        for (let j = i + 1; j < Math.min(i + 10, nextCatIdx); j++) {
                            const nxt = lines2[j];
                            if (nxt.match(/^\\d+$/) || nxt.match(/(RANKED|PRIZE|POINTS)/i) || nxt.length < 2) break;
                            if (nxt.match(/^[A-Z][a-z]+$/)) {
                                nameLines.push(nxt);
                            } else if (nxt.match(/^[A-Z]{2,}/)) {
                                // 缩写名如 "GOH", "IZZUDDIN"
                                nameLines.push(nxt);
                            } else {
                                break;
                            }
                        }
                        if (nameLines.length >= 1) {
                            const name = nameLines.join(' ');
                            // 找排名、奖金、积分
                            let rank_num = '', prize_money = '', points = '', ranking = '';
                            for (let j = i + 1; j < Math.min(i + 15, nextCatIdx); j++) {
                                const l2 = lines2[j];
                                if (l2.match(/^\\d+$/) && !rank_num) rank_num = l2;
                                if (/RANKED/i.test(l2)) ranking = l2.replace('RANKED', '').trim();
                                if (/PRIZE/i.test(l2)) prize_money = l2.trim();
                                if (/POINTS/i.test(l2)) points = l2.trim();
                            }
                            category.entries.push({
                                rank, name, ranking, prize_money, points,
                                name_lines: nameLines.join('|')
                            });
                        }
                    }
                }

                if (category.entries.length > 0) {
                    categories.push(category);
                }
            }

            result.categories = categories;
            return result;
        }""")

    except Exception as e:
        logger.error(f"抓取失败: {e}")
    finally:
        browser.close()

    logger.info(f"抓取到 {len(result.get('categories', []))} 个项目")
    return result


def build_tournament_html(tdata: dict, tournament: dict) -> str:
    """构建赛事结果 HTML"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    date = tdata.get("date", tournament.get("month", ""))
    venue = tdata.get("venue", "")
    prize = tdata.get("prize", "")
    level = tdata.get("level", tournament.get("level", ""))

    parts = [
        f'<h2>{tournament["name"]}</h2>',
        f'<p style="color:#666;font-size:14px">'
        f'<strong>{date}</strong> | {venue} | {level}<br>'
        f'Prize Money: {prize} | Diperbarui: {now_str} (UTC)'
        f'</p>',
        '<hr>',
    ]

    categories = tdata.get("categories", [])
    if categories:
        parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
        parts.append(
            '<tr style="background:#1a1a2e;color:#fff">'
            '<th style="padding:8px;text-align:center;border:1px solid #333;width:40px">#</th>'
            '<th style="padding:8px;text-align:left;border:1px solid #333">Pemain / Pasangan</th>'
            '<th style="padding:8px;text-align:center;border:1px solid #333">Peringkat</th>'
            '<th style="padding:8px;text-align:right;border:1px solid #333">Hadia h</th>'
            '<th style="padding:8px;text-align:right;border:1px solid #333">Poin</th>'
            '</tr>'
        )

        # Indonesia 颜色高亮
        for cat in categories:
            parts.append(
                f'<tr><td colspan="5" style="padding:8px 8px 4px;background:#f5f5f5;font-weight:bold;color:#333">'
                f'{cat["name"]}</td></tr>'
            )
            for entry in cat.get("entries", []):
                rank = entry.get("rank", "")
                name = entry.get("name", "")
                ranking = entry.get("ranking", "")
                prize_money = entry.get("prize_money", "").replace("PRIZE MONEY - ", "")
                points = entry.get("points", "").replace("POINTS GAINED - ", "")

                # 判断是否是印尼选手（用于高亮）
                is_indo = any(w in name.upper() for w in
                              ["GINTING", "CHRISTIE", "JONATAN", "FEBRI", "GUTAMA", "ISFAHANI",
                               "RAHAYU", "APR YANI", "Syah", "Prasetyo", "ANGGINA", " Indonesia"])

                row_style = ""
                if rank == "1":
                    row_style = 'background:#fff8e1;font-weight:bold'
                elif rank == "2":
                    row_style = 'background:#fafafa'

                parts.append(
                    f'<tr style="{row_style}">'
                    f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee">{rank}</td>'
                    f'<td style="padding:6px 8px;border:1px solid #eee;font-weight:bold">{name}</td>'
                    f'<td style="padding:6px 8px;text-align:center;border:1px solid #eee;font-size:12px">{ranking}</td>'
                    f'<td style="padding:6px 8px;text-align:right;border:1px solid #eee;font-size:12px">{prize_money}</td>'
                    f'<td style="padding:6px 8px;text-align:right;border:1px solid #eee;font-size:12px">{points}</td>'
                    '</tr>'
                )
        parts.append('</table>')
    else:
        # 无结构化数据时直接输出原始文本
        raw = tdata.get("raw_text", "")
        # 只保留 PODIUM 相关部分
        podium_idx = raw.upper().find("PODIUM")
        if podium_idx < 0:
            podium_idx = raw.upper().find("TOURNAMENT WINNER")
        if podium_idx >= 0:
            raw = raw[podium_idx:]
        parts.append(f'<pre style="font-size:13px;white-space:pre-wrap">{raw[:3000]}</pre>')

    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="{tdata.get("url", tournament["url"])}" target="_blank">BWF World Tour</a></p>')
    return '\n'.join(parts)


def run(save_to_db: bool = False) -> list[dict]:
    logger.info("=== BWF 印尼羽毛球赛事抓取 ===")

    results = []
    for t in TOURNAMENTS:
        logger.info(f"\n--- {t['name']} ---")
        tdata = fetch_tournament_results(t["url"])
        if not tdata or not tdata.get("categories"):
            logger.warning(f"  未获取到 {t['name']} 数据，跳过")
            continue

        # 打印摘要
        for cat in tdata.get("categories", []):
            logger.info(f"  {cat['name']}:")
            for entry in cat.get("entries", []):
                logger.info(f"    {entry.get('rank','')}. {entry.get('name','')} ({entry.get('ranking','')})")

        results.append({"tournament": t, "data": tdata})

    if save_to_db and results:
        author_id = lookup_author_id()
        category_id = lookup_category_id()

        for item in results:
            t = item["tournament"]
            tdata = item["data"]
            title = f"Hasil {t['name']} - BWF"
            html = build_tournament_html(tdata, t)
            upsert_post(title, html, author_id, category_id, t["tag"])

        logger.info("入库完成")

    logger.info("=== BWF 印尼羽毛球赛事抓取完成 ===")
    return results


def main():
    parser = argparse.ArgumentParser(description="BWF 印尼羽毛球赛事结果抓取")
    parser.add_argument("--save", action="store_true", help="入库到数据库")
    args = parser.parse_args()
    run(save_to_db=args.save)


if __name__ == "__main__":
    main()
