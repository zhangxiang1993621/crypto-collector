"""清空全部帖子和评论脚本（测试用）

⚠️  危险操作！会删除 posts、comments、post_tags、likes、bookmarks
   以及全部 tags（帖子清空后标签已不再被引用）。

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
    # 清空全部 tags（帖子清空后所有标签都已无引用）
    ("__all_tags__",  None,                  "全部标签"),
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


def clear_all_tags() -> int:
    """清空 tags 表并返回真实删除行数。

    说明：本脚本会清空 posts 与 post_tags，清空后所有标签都不再被引用，
    因此这里显式清空全部标签（原实现声称"仅删孤儿标签"，但因执行顺序在
    post_tags 清空之后，实际同样是删除全部标签）。
    """
    if not table_exists("tags"):
        return 0
    try:
        rows = execute_sql("DELETE FROM tags RETURNING id")
        return len(rows) if rows else 0
    except Exception as e:
        print(f"  [错误] 清空 tags 失败: {e}")
        return 0


def run(dry_run: bool = False, skip_confirm: bool = False) -> None:
    print("=" * 60)
    print("  清空全部帖子和评论（测试用）")
    print("=" * 60)

    # ── 第 1 步：统计当前数据量 ──
    print("\n[统计] 当前数据量：")
    total_rows = 0
    for name, table, label in DELETE_ORDER:
        count = 0
        if name == "__all_tags__":
            if table_exists("tags"):
                count = count_table("tags")
        else:
            if table_exists(table):
                count = count_table(table)
            else:
                count = 0
        print(f"  {label:<20} {count:>8,} 行")
        total_rows += count

    if total_rows == 0:
        print("\n[OK] 所有表均为空，无需清理。")
        return

    print(f"  {'─' * 36}")
    print(f"  {'合计':<20} {total_rows:>8,} 行")

    # ── 第 2 步：确认 ──
    if dry_run:
        print(f"\n[预览] --dry-run 模式，未执行删除。")
        return

    if not skip_confirm:
        print(f"\n[警告] 即将删除以上 {total_rows:,} 行数据！")
        answer = input("确认执行？(输入 YES 继续): ").strip()
        if answer != "YES":
            print("已取消。")
            return

    # ── 第 3 步：执行删除 ──
    print("\n[删除] 开始删除...")
    total_deleted = 0
    for name, table, label in DELETE_ORDER:
        if name == "__all_tags__":
            deleted = clear_all_tags()
        else:
            deleted = delete_table(table)
        total_deleted += deleted
        print(f"  {label:<20} 已删除 {deleted:>8,} 行")

    print(f"  {'─' * 36}")
    print(f"  {'合计':<20} 已删除 {total_deleted:>8,} 行")
    print("\n[OK] 清空完成。")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="清空全部帖子和评论（测试用）")
    parser.add_argument("--yes", action="store_true", help="跳过确认直接执行")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    args = parser.parse_args()

    run(dry_run=args.dry_run, skip_confirm=args.yes)
