"""BWF 全年赛历 - 从 olympics.com badminton 页面抓取全年羽毛球赛事

功能：从 olympics.com badminton 页面和 BWF 赛历页面抓取全年羽毛球赛事信息，存入 Supabase posts 表。
数据源：
  - olympics.com/en/sport/badminton/  (全年羽毛球资讯和赛历)
  - bwfbadminton.com/calendar/        (BWF 官方赛历)
用法：
    python sport/badminton/bwf_calendar_scraper.py              # 仅抓取打印
    python sport/badminton/bwf_calendar_scraper.py --save       # 抓取并直接入库
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

TARGET_URLS = [
    "https://bwfbadminton.com/calendar/",
    "https://olympics.com/en/sports/badminton/",
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


def fetch_bwf_calendar() -> dict:
    """使用 CloakBrowser 抓取 BWF 官方赛历"""
    logger.info("访问 BWF Calendar...")

    from cloakbrowser import launch

    result = {"tournaments": [], "raw": ""}
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        page.goto("https://bwfbadminton.com/calendar/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        if "not found" in page.title().lower() or "404" in page.title():
            logger.warning("BWF Calendar 页面不可用")
            return result

        data = page.evaluate("""() => {
            const result = { tournaments: [] };
            const text = document.body.innerText;
            result.raw = text;

            // 从可见文本解析赛历
            const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
            
            let currentMonth = '';
            const monthPattern = /^(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)/i;
            
            const tournaments = [];
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                
                // 月份标题
                if (monthPattern.test(line)) {
                    currentMonth = line;
                    continue;
                }
                
                // 日期范围格式: "DD - DD MONTH" 或 "DD MONTH - DD MONTH"
                const dateMatch = line.match(/^(\\d{2})\\s*-\\s*(\\d{2})\\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)/i);
                if (dateMatch) {
                    // 找接下来几行的赛事信息
                    const dateRange = line;
                    let name = '';
                    let location = '';
                    let level = '';
                    let prize = '';
                    
                    for (let j = i + 1; j < Math.min(i + 6, lines.length); j++) {
                        const nxt = lines[j];
                        // 跳过 LIVE SCORES
                        if (/LIVE SCORES/i.test(nxt)) continue;
                        // 找城市/国家
                        if (!location && nxt.match(/[A-Z][a-z]+,\\s+[A-Z]/)) {
                            location = nxt;
                        }
                        // 找赛事级别
                        if (!level && /SUPER\\s*(1000|750|500|300|100)|THOMAS|Uber|SUDIRMAN|WORLD CHAMP/i.test(nxt)) {
                            level = nxt;
                        }
                        // 找奖金
                        if (!prize && /USD\\s*\\d/i.test(nxt)) {
                            prize = nxt;
                        }
                        // 找赛事名称
                        if (!name && nxt.length > 3 && !nxt.match(/^\\d+$/) && !nxt.match(/Prize|Ticket|Watch|Live/i)) {
                            name = nxt;
                        }
                    }
                    
                    if (name || level) {
                        tournaments.push({
                            date: dateRange,
                            month: currentMonth,
                            name: name,
                            location: location,
                            level: level,
                            prize: prize
                        });
                    }
                }
            }
            
            result.tournaments = tournaments;
            return result;
        }""")

        result.update(data)

    except Exception as e:
        logger.error(f"BWF Calendar 抓取失败: {e}")
    finally:
        browser.close()

    logger.info(f"抓取到 {len(result.get('tournaments', []))} 场赛事")
    return result


def fetch_olympics_badminton() -> dict:
    """使用 CloakBrowser 抓取 Olympics badminton 赛历"""
    logger.info("访问 Olympics badminton...")

    from cloakbrowser import launch

    result = {"events": [], "news": []}
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        page.goto("https://olympics.com/en/sports/badminton/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        if "404" in page.title():
            logger.warning("Olympics badminton 页面不可用")
            return result

        data = page.evaluate("""() => {
            const result = { events: [], news: [], text: '' };
            const text = document.body.innerText;
            result.text = text;

            // 找赛事相关条目
            const eventCards = document.querySelectorAll('[class*="event"], [class*="schedule"], [class*="upcoming"], article, [class*="card"]');
            eventCards.forEach(card => {
                const text = card.textContent.trim();
                if (text.length > 20 && text.length < 500) {
                    result.events.push(text.slice(0, 200));
                }
            });

            // 找印尼相关
            const all = document.querySelectorAll('*');
            const indoEvents = [];
            all.forEach(el => {
                if (/indonesia/i.test(el.textContent) && el.textContent.length < 200) {
                    indoEvents.push(el.textContent.trim().slice(0, 150));
                }
            });
            result.indo_events = [...new Set(indoEvents)].slice(0, 10);

            return result;
        }""")

        result.update(data)

    except Exception as e:
        logger.error(f"Olympics badminton 抓取失败: {e}")
    finally:
        browser.close()

    return result


def build_calendar_html(tournaments: list[dict]) -> str:
    """构建 BWF 赛历 HTML"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        '<h2>Kalender Turnamen Bulu Tangkis Dunia 2026</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: BWF Badminton</p>',
        '<hr>',
        '<table style="width:100%;border-collapse:collapse;font-size:13px">',
        '<tr style="background:#1a1a2e;color:#fff">'
        '<th style="padding:8px;text-align:left;border:1px solid #333;width:120px">Tanggal</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #333">Turnamen</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #333">Lokasi</th>'
        '<th style="padding:8px;text-align:left;border:1px solid #333">Level</th>'
        '<th style="padding:8px;text-align:right;border:1px solid #333">Hadiah</th>'
        '</tr>',
    ]

    current_month = ""
    for t in tournaments:
        month = t.get("month", "")
        month_row = ""
        if month != current_month:
            current_month = month
            month_row = f'<tr><td colspan="5" style="background:#2d2d44;color:#fff;padding:6px 8px;font-weight:bold">{month}</td></tr>'
            parts.append(month_row)

        name = t.get("name", "")
        date = t.get("date", "")
        location = t.get("location", "")
        level = t.get("level", "")
        prize = t.get("prize", "").replace("US $", "USD")

        # Indonesia 高亮
        is_indo = "Indonesia" in location or "Indonesia" in name
        row_bg = '#fff8e1' if is_indo else ''

        parts.append(
            f'<tr style="background:{row_bg}">'
            f'<td style="padding:6px 8px;border:1px solid #eee;font-size:12px">{date}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;font-weight:bold">{name}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee">{location}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;font-size:11px">{level}</td>'
            f'<td style="padding:6px 8px;border:1px solid #eee;text-align:right;font-size:12px">{prize}</td>'
            '</tr>'
        )

    parts.append('</table>')
    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="https://bwfbadminton.com/calendar/" target="_blank">BWF Calendar 2026</a></p>')
    return '\n'.join(parts)


def build_olympics_html(data: dict) -> str:
    """构建 Olympics badminton HTML"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        '<h2>Berita & Event Bulu Tangkis - Olympic Games</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: Olympics.com</p>',
        '<hr>',
    ]

    text = data.get("text", "")
    # 提取关键段落
    if text:
        # 取 badminton 相关内容段落
        lines = text.split('\n')
        relevant = []
        skip = False
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            # 跳过页眉
            if any(h in line for h in ['IOC', 'LA28', 'Museum', 'Shop', 'Airbnb', 'Olympic Channel']):
                continue
            relevant.append(line)
        parts.append('<div style="font-size:14px;line-height:1.7">')
        for line in relevant[:80]:
            parts.append(f'<p>{line}</p>')
        parts.append('</div>')

    parts.append('<hr>')
    parts.append(f'<p style="font-size:12px;color:#999">Sumber: <a href="https://olympics.com/en/sports/badminton/" target="_blank">Olympics.com Badminton</a></p>')
    return '\n'.join(parts)


def run(save_to_db: bool = False) -> dict:
    logger.info("=== BWF 羽毛球赛历抓取 ===")

    # 1. BWF 官方赛历
    bwf_data = fetch_bwf_calendar()
    tournaments = bwf_data.get("tournaments", [])
    logger.info(f"\nBWF 赛历: {len(tournaments)} 场")
    for t in tournaments:
        logger.info(f"  {t.get('date','')} | {t.get('name','')} | {t.get('location','')}")

    # 2. Olympics badminton
    olympics_data = fetch_olympics_badminton()
    logger.info(f"\nOlympics badminton 内容长度: {len(olympics_data.get('text', ''))} 字符")

    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()

        if tournaments:
            html = build_calendar_html(tournaments)
            upsert_post("Kalender Turnamen Bulu Tangkis 2026 - BWF", html, author_id, category_id, TAG_NAME)

        if olympics_data.get("text"):
            html = build_olympics_html(olympics_data)
            upsert_post("Berita Bulu Tangkis - Olympics.com", html, author_id, category_id, TAG_NAME)

        logger.info("入库完成")

    logger.info("=== BWF 羽毛球赛历抓取完成 ===")
    return {"bwf": bwf_data, "olympics": olympics_data}


def main():
    parser = argparse.ArgumentParser(description="BWF 羽毛球赛历抓取")
    parser.add_argument("--save", action="store_true", help="入库到数据库")
    args = parser.parse_args()
    run(save_to_db=args.save)


if __name__ == "__main__":
    main()
