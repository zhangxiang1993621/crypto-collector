"""FIFA 2026 Piala Dunia - Pengambil Jadwal + Skrip Penyimpanan

Fungsi: Mengambil jadwal Piala Dunia 2026 dari halaman resmi FIFA, menyimpan ke tabel posts Supabase
Penggunaan:
    python sport/schedule/fifa_scraper.py                  # Hanya ambil & cetak
    python sport/schedule/fifa_scraper.py --save           # Ambil & simpan langsung
"""

import os
import sys
import re
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from db_direct import select_one, select_all, insert_one, update_one
from playwright.sync_api import sync_playwright

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

FIFA_API = "https://fifaworldcup26.hospitality.fifa.com/next-api/matches-all?productCode=26FWC&productType=5"
FIFA_PAGE = "https://fifaworldcup26.hospitality.fifa.com/id/id/choose-matches?scheduleView=true"

# 印尼语月份映射
BULAN_ID = {
    "January": "Januari", "February": "Februari", "March": "Maret",
    "April": "April", "May": "Mei", "June": "Juni",
    "July": "Juli", "August": "Agustus", "September": "September",
    "October": "Oktober", "November": "November", "December": "Desember",
}

# 印尼语星期映射
HARI_ID = {
    "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
    "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
}

# 印尼语国家名称映射
NEGARA_ID = {
    "Algeria": "Aljazair", "Argentina": "Argentina", "Australia": "Australia",
    "Austria": "Austria", "Belgium": "Belgia",
    "Bosnia and Herzegovina": "Bosnia dan Herzegovina",
    "Brazil": "Brasil", "Cabo Verde": "Tanjung Verde", "Canada": "Kanada",
    "Curacao": "Curacao", "Czechia": "Ceko", "Ecuador": "Ekuador",
    "Egypt": "Mesir", "France": "Prancis", "Germany": "Jerman",
    "Haiti": "Haiti", "Iran": "Iran", "Iraq": "Irak",
    "Ivory Coast": "Pantai Gading", "Japan": "Jepang",
    "Jordan": "Yordania", "Korea Republic": "Korea Selatan",
    "Mexico": "Meksiko", "Morocco": "Maroko", "Netherlands": "Belanda",
    "New Zealand": "Selandia Baru", "Norway": "Norwegia",
    "Paraguay": "Paraguay", "Qatar": "Qatar",
    "Republic of Korea": "Korea Selatan", "Saudi Arabia": "Arab Saudi",
    "Scotland": "Skotlandia", "Senegal": "Senegal",
    "South Africa": "Afrika Selatan", "Spain": "Spanyol",
    "Sweden": "Swedia", "Switzerland": "Swiss",
    "Tunisia": "Tunisia", "Turkiye": "Turkiye",
    "Uruguay": "Uruguay", "USA": "Amerika Serikat",
}


def _ke_id(teks: str) -> str:
    """将英文球队名转为印尼语"""
    return NEGARA_ID.get(teks, teks)


def _tanggal_id(match_date: str) -> str:
    """将 'June 24' 转为 '24 Juni'"""
    for en, id_ in BULAN_ID.items():
        if en in match_date:
            return match_date.replace(en, id_)
    return match_date


def _waktu_id(match_time: str) -> str:
    """将 'Wednesday, 7pm CT' 转为 'Rabu, 7pm CT'"""
    for en, id_ in HARI_ID.items():
        if en in match_time:
            return match_time.replace(en, id_)
    return match_time


# ────────────────────── Alat Database ──────────────────────

def lookup_author_id() -> str:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    row = select_one("profiles", {"username": username}, columns="id,username")
    if not row:
        logger.error(f"Author tidak ditemukan: {username}")
        sys.exit(1)
    logger.info(f"Author: {row['username']} (id={row['id']})")
    return row["id"]


def lookup_category_id() -> str:
    name = os.environ.get("FIFA_CATEGORY_NAME") or "Sports Talk"
    row = select_one("categories", {"name": name}, columns="id,name")
    if not row:
        logger.error(f"Kategori tidak ditemukan: {name}")
        sys.exit(1)
    logger.info(f"Kategori: {row['name']} (id={row['id']})")
    return row["id"]


def upsert_post(title: str, content: str, author_id: str,
                category_id: str, tag_name: str) -> str:
    row = select_one("posts", {"title": title}, columns="id")
    now = datetime.now(timezone.utc).isoformat()

    if row:
        post_id = row["id"]
        update_one("posts", {"content": content, "updated_at": now}, {"id": post_id})
        logger.info(f"[Update] Postingan sudah ada, konten diperbarui: {title}")
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
        logger.info(f"[Baru] Postingan dibuat: {title}")

    sync_tag(post_id, tag_name)
    return post_id


def sync_tag(post_id: str, tag_name: str) -> None:
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


# ────────────────────── Pengambilan Data ──────────────────────

def fetch_matches_from_api() -> list[dict]:
    logger.info("Mengambil data pertandingan dari FIFA API...")
    r = httpx.get(FIFA_API, timeout=30)
    r.raise_for_status()
    data = r.json()
    logger.info(f"Diperoleh {len(data)} pertandingan")
    return data


def fetch_group_mapping() -> dict[int, str]:
    """从页面抓取 MatchNumber → Grup 映射"""
    logger.info("Mengambil informasi grup dari halaman...")
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
                const groupMatch = text.match(/Group\\s+([A-L])\\b/i) || text.match(/Grup\\s+([A-L])\\b/i);
                const numMatch = text.match(/\\bM(\\d+)/);
                if (groupMatch && numMatch) {
                    mapping[parseInt(numMatch[1])] = groupMatch[1];
                }
            });
            return mapping;
        }""")

        browser.close()
    logger.info(f"Diperoleh {len(mapping)} pemetaan grup")
    return mapping


# ────────────────────── Pembuatan HTML ──────────────────────

def build_html_schedule(matches: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for m in matches:
        g = m.get("group", "Babak Gugur")
        groups.setdefault(g, []).append(m)

    sorted_groups = sorted([g for g in groups if g != "Babak Gugur"]) + (["Babak Gugur"] if "Babak Gugur" in groups else [])

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_parts = [
        '<h2>Jadwal Grup Piala Dunia FIFA 2026</h2>',
        f'<p style="color:#666;font-size:14px">Diperbarui: {now_str} (UTC) | Sumber: FIFA Official</p>',
        '<hr>',
    ]

    for g_name in sorted_groups:
        group_matches = groups[g_name]
        html_parts.append(f'<h3 style="margin-top:24px">Grup {g_name}</h3>')
        html_parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
        html_parts.append('<tr style="background:#f0f0f0">'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tanggal</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">Waktu</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tim Tuan Rumah</th>'
                          '<th style="padding:8px;text-align:center;border:1px solid #ddd">VS</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">Tim Tamu</th>'
                          '<th style="padding:8px;text-align:left;border:1px solid #ddd">Stadion</th>'
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
    html_parts.append('<p style="font-size:12px;color:#999">Jadwal babak gugur akan diperbarui setelah fase grup selesai.</p>')
    return '\n'.join(html_parts)


# ────────────────────── Alur Utama ──────────────────────

def run(save_to_db: bool = False) -> list[dict]:
    api_matches = fetch_matches_from_api()
    group_map = fetch_group_mapping()

    result = []
    for m in api_matches:
        if m["Stage"] != "GROUP STAGE MATCHES":
            continue
        mn = m["MatchNumber"]
        result.append({
            "match_number": mn,
            "host_team": _ke_id(m["HostTeam"]["ExternalName"]),
            "away_team": _ke_id(m["OpposingTeam"]["ExternalName"]),
            "venue": f"{m['Venue']['Name']} ({m['Venue']['Town']}, {m['Venue']['Country']})",
            "match_date": _tanggal_id(m["MatchDate"]),
            "match_time": _waktu_id(m["MatchDayTime"]),
            "group": group_map.get(mn, ""),
        })

    logger.info(f"Total pertandingan grup: {len(result)}")

    # 推导缺失的分组
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
        logger.info(f"Grup dipulihkan untuk {fixed} pertandingan")

    if save_to_db:
        author_id = lookup_author_id()
        category_id = lookup_category_id()
        html = build_html_schedule(result)
        upsert_post("Jadwal Grup Piala Dunia FIFA 2026", html, author_id, category_id, "Piala Dunia FIFA 2026")

    return result


def main():
    parser = argparse.ArgumentParser(description="FIFA 2026 Piala Dunia - Pengambil Jadwal")
    parser.add_argument("--save", action="store_true", help="Simpan ke database")
    args = parser.parse_args()

    logger.info("=== Pengambil Jadwal Piala Dunia FIFA 2026 ===")
    run(save_to_db=args.save)
    logger.info("=== Selesai ===")


if __name__ == "__main__":
    main()
