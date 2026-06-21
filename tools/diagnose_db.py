"""数据库诊断工具 — 分析各表行数、大小，定位配额占用瓶颈

用法：
    python tools/diagnose_db.py          # 基础诊断：行数 + 死元组
    python tools/diagnose_db.py --full   # 完整诊断：含表大小（需 RPC）
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from db_direct import execute_sql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── 所有已知表名 ──
ALL_TABLES = [
    "tokens",
    "stock_symbols",
    "us_stock_bars",
    "us_stock_trends",
    "posts",
    "post_tags",
    "tags",
    "profiles",
    "categories",
    "comments",
    "likes",
    "follows",
    "bookmarks",
]

# ── 各表的大致单行估算（字节），用于粗略估算总大小 ──
ROW_SIZE_ESTIMATE: dict[str, int] = {
    "tokens": 200,
    "stock_symbols": 150,
    "us_stock_bars": 100,
    "us_stock_trends": 80,
    "posts": 2000,
    "post_tags": 60,
    "tags": 80,
    "profiles": 500,
    "categories": 100,
    "comments": 300,
    "likes": 40,
    "follows": 40,
    "bookmarks": 40,
}


def format_size(bytes_val: int) -> str:
    """将字节数格式化为人类可读"""
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


def count_table(table_name: str) -> int:
    """精确计数表行数"""
    try:
        rows = execute_sql(f'SELECT COUNT(*) as cnt FROM "{table_name}"')
        if rows:
            return rows[0].get("cnt", 0)
        return 0
    except Exception as e:
        logger.warning(f"  {table_name}: 无法计数 — {e}")
        return -1


def get_table_stats() -> list[dict[str, Any]]:
    """获取所有表的基础统计"""
    results: list[dict[str, Any]] = []
    for table_name in ALL_TABLES:
        count = count_table(table_name)
        if count < 0:
            continue
        est_bytes = count * ROW_SIZE_ESTIMATE.get(table_name, 100)
        results.append({
            "table": table_name,
            "rows": count,
            "est_size": est_bytes,
        })
    return results


def run_diagnose() -> None:
    """主诊断流程"""
    print("=" * 70)
    print("  Supabase 数据库诊断报告")
    print("=" * 70)
    print()

    stats = get_table_stats()

    # ── 按估算大小排序 ──
    stats.sort(key=lambda x: x["est_size"], reverse=True)
    total_est = sum(s["est_size"] for s in stats)

    print(f"{'表名':<20} {'行数':>12} {'估算大小':>12} {'占比':>8}")
    print("-" * 55)
    for s in stats:
        pct = s["est_size"] / total_est * 100 if total_est > 0 else 0
        bar = "█" * int(pct / 2)
        print(f"{s['table']:<20} {s['rows']:>12,} {format_size(s['est_size']):>12} {pct:>6.1f}% {bar}")

    print("-" * 55)
    print(f"{'合计':<20} {sum(s['rows'] for s in stats):>12,} {format_size(total_est):>12}")
    print()
    logger.info(f"诊断完成，共 {len(stats)} 张表")


if __name__ == "__main__":
    run_diagnose()
