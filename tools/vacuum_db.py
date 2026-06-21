"""VACUUM 执行脚本 — 通过直连 PostgreSQL 回收死元组空间

前提条件：
    1. 安装 psycopg2: pip install psycopg2-binary
    2. 在 Supabase Dashboard → Project Settings → Database 中
       找到 Connection string 里的密码 (或重置密码)

用法：
    python tools/vacuum_db.py              # 诊断 + VACUUM us_stock_bars 和 posts
    python tools/vacuum_db.py --table us_stock_bars   # 只 VACUUM 指定表
    python tools/vacuum_db.py --full       # VACUUM FULL（锁表，归还磁盘）
    python tools/vacuum_db.py --dry-run    # 只诊断，不执行
"""

import os
import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 从 SUPABASE_URL 提取项目引用 ──
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
# https://uqlxbcoowpmxmvwkuomc.supabase.co → uqlxbcoowpmxmvwkuomc
PROJECT_REF = SUPABASE_URL.replace("https://", "").replace(".supabase.co", "").strip("/") if SUPABASE_URL else ""


def get_db_password() -> str:
    """获取数据库密码，优先环境变量，否则提示用户输入"""
    pwd = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if pwd:
        return pwd
    print("\n请从 Supabase Dashboard 获取数据库密码：")
    print("  Project Settings → Database → Connection Info → Password")
    print(f"  主机: db.{PROJECT_REF}.supabase.co")
    print(f"  端口: 5432 (session mode)")
    print()
    return input("请输入数据库密码: ").strip()


def get_connection(password: str):
    """建立直连 PostgreSQL 连接（session 模式，非 transaction 模式）"""
    import psycopg2
    conn = psycopg2.connect(
        host=f"db.{PROJECT_REF}.supabase.co",
        port=5432,              # session mode — VACUUM 必须用这个端口
        dbname="postgres",
        user="postgres",
        password=password,
        sslmode="require",      # Supabase 强制要求 SSL
        connect_timeout=15,
    )
    conn.autocommit = True      # 关键！关闭事务模式
    return conn


def diagnose(conn) -> None:
    """打印当前各个表的膨胀情况"""
    sql = """
    SELECT
      relname AS table_name,
      pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
      n_live_tup AS live_rows,
      n_dead_tup AS dead_rows,
      round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct
    FROM pg_stat_user_tables
    WHERE n_dead_tup > 0 OR pg_total_relation_size(relid) > 8192
    ORDER BY pg_total_relation_size(relid) DESC;
    """
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()

    if not rows:
        print("  所有表都很干净，没有死元组。")
        return

    print(f"  {'表名':<25} {'总大小':>10} {'活行':>8} {'死行':>8} {'死元组%':>8}")
    print("  " + "-" * 63)
    for r in rows:
        print(f"  {r[0]:<25} {r[1]:>10} {r[2]:>8} {r[3]:>8} {r[4] or 0:>7.1f}%")
    print()


def run_vacuum(conn, table_name: str, full: bool = False) -> None:
    """对指定表执行 VACUUM 或 VACUUM FULL"""
    mode = "VACUUM FULL" if full else "VACUUM"
    sql = f"{mode} (VERBOSE, ANALYZE) {table_name};"

    logger.info(f"执行: {mode} {table_name} ...")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        # VACUUM VERBOSE 会返回通知消息
        for notice in conn.notices:
            logger.info(f"  {notice.strip()}")
        conn.notices.clear()
        logger.info(f"  {mode} {table_name} 完成 ✓")
    except Exception as e:
        logger.error(f"  {mode} {table_name} 失败: {e}")
    finally:
        cur.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="直连 PostgreSQL 执行 VACUUM")
    parser.add_argument("--table", type=str, default=None,
                        help="指定表名（默认: us_stock_bars, posts）")
    parser.add_argument("--full", action="store_true",
                        help="执行 VACUUM FULL（锁表，归还磁盘空间给 OS）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只诊断，不执行 VACUUM")
    args = parser.parse_args()

    if not PROJECT_REF:
        logger.error("无法从 SUPABASE_URL 提取项目引用，请检查 .env 文件")
        sys.exit(1)

    print("=" * 65)
    print("  Supabase PostgreSQL VACUUM 工具")
    print(f"  项目: {PROJECT_REF}")
    print("=" * 65)
    print()

    password = get_db_password()
    if not password:
        logger.error("数据库密码不能为空")
        sys.exit(1)

    logger.info("连接数据库 (session mode, port 5432)...")
    try:
        conn = get_connection(password)
    except Exception as e:
        logger.error(f"连接失败: {e}")
        logger.error("请确认：")
        logger.error(f"  1. 主机名正确: db.{PROJECT_REF}.supabase.co")
        logger.error("  2. 密码正确（Supabase Dashboard → Project Settings → Database）")
        logger.error("  3. 网络可访问（部分网络需要代理）")
        sys.exit(1)

    try:
        # ── 第 1 步：诊断 ──
        print("\n📊 当前表膨胀情况：")
        diagnose(conn)

        if args.dry_run:
            print("  (--dry-run 模式，跳过 VACUUM)")
            return

        # ── 第 2 步：VACUUM ──
        tables = [args.table] if args.table else ["us_stock_bars", "posts"]
        for t in tables:
            run_vacuum(conn, t, full=args.full)

        # ── 第 3 步：执行后再次诊断 ──
        print("\n📊 VACUUM 后表状态：")
        diagnose(conn)

        print(f"\n✅ 完成！配额应该已经下降。如果用的是 VACUUM FULL，磁盘空间已归还 OS。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
