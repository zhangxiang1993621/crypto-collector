"""清空全部帖子和评论脚本（测试用）

⚠️  危险操作！会删除 posts、comments、post_tags、likes、bookmarks
   以及未被引用的孤儿 tags。

用法：
    python tools/clean_all.py                    # 交互模式，确认后执行
    python tools/clean_all.py --yes              # 跳过确认直接执行
    python tools/clean_all.py --dry-run          # 只统计，不删除
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db_direct import execute_sql

DELETE_ORDER = [
    # 子表优先（有外键依赖）
    ("post_tags",     "post_tags",           "帖子-标签关联"),
    ("likes",         "likes",               "点赞"),
    ("bookmarks",     "bookmarks",           "收藏"),
    ("comments",      "comments",            "评论"),
    ("posts",         "posts",               "帖子"),
    # 清理孤儿 tags
    ("__orphan_tags__", None,                "孤儿标签"),
]


def table_exists(table_name: str) -> bool:
    """检查表是否存在"""
    try:
        execute_sql(f'SELECT 1 FROM "{table_name}" LIMIT 0', fetch=False)
        return True
    except Exception:
        return False


def count_table(table_name: str) -> int:
    rows = execute_sql(f'SELECT COUNT(*) AS cnt FROM "{table_name}"')
    return rows[0]["cnt"] if rows else 0


def delete_table(table_name: str) -> int:
    """清空指定表，返回删除行数。表不存在返回 0。"""
    if not table_exists(table_name):
        return 0
    count = count_table(table_name)
    if count == 0:
        return 0
    execute_sql(f'DELETE FROM "{table_name}"', fetch=False)
    return count


def delete_orphan_tags() -> int:
    """删除未被任何 post_tags 引用的孤儿标签"""
    if not table_exists("tags") or not table_exists("post_tags"):
        return 0
    try:
        sql = """
        DELETE FROM tags
        WHERE id NOT IN (SELECT DISTINCT tag_id FROM post_tags WHERE tag_id IS NOT NULL)
        """
        result = execute_sql(sql, fetch=False)
        return result.rowcount if hasattr(result, 'rowcount') else 0
    except Exception:
        return 0


def run(dry_run: bool = False, skip_confirm: bool = False) -> None:
    print("=" * 60)
    print("  清空全部帖子和评论（测试用）")
    print("=" * 60)

    # ── 第 1 步：统计当前数据量 ──
    print("\n📊 当前数据量：")
    total_rows = 0
    for name, table, label in DELETE_ORDER:
        count = 0
        if name == "__orphan_tags__":
            try:
                orphan_sql = """
                SELECT COUNT(*) AS cnt FROM tags
                WHERE id NOT IN (SELECT DISTINCT tag_id FROM post_tags WHERE tag_id IS NOT NULL)
                """
                rows = execute_sql(orphan_sql)
                count = rows[0]["cnt"] if rows else 0
            except Exception:
                pass
        else:
            if table_exists(table):
                count = count_table(table)
            else:
                count = 0
        print(f"  {label:<20} {count:>8,} 行")
        total_rows += count

    if total_rows == 0:
        print("\n✅ 所有表均为空，无需清理。")
        return

    print(f"  {'─' * 36}")
    print(f"  {'合计':<20} {total_rows:>8,} 行")

    # ── 第 2 步：确认 ──
    if dry_run:
        print(f"\n🔍 --dry-run 模式，未执行删除。")
        return

    if not skip_confirm:
        print(f"\n⚠️  即将删除以上 {total_rows:,} 行数据！")
        answer = input("确认执行？(输入 YES 继续): ").strip()
        if answer != "YES":
            print("已取消。")
            return

    # ── 第 3 步：执行删除 ──
    print("\n🗑️  开始删除...")
    total_deleted = 0
    for name, table, label in DELETE_ORDER:
        if name == "__orphan_tags__":
            deleted = delete_orphan_tags()
        else:
            deleted = delete_table(table)
        total_deleted += deleted
        print(f"  {label:<20} 已删除 {deleted:>8,} 行")

    print(f"  {'─' * 36}")
    print(f"  {'合计':<20} 已删除 {total_deleted:>8,} 行")
    print("\n✅ 清空完成。")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="清空全部帖子和评论（测试用）")
    parser.add_argument("--yes", action="store_true", help="跳过确认直接执行")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    args = parser.parse_args()

    run(dry_run=args.dry_run, skip_confirm=args.yes)
