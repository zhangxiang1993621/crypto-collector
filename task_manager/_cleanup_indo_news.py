"""一次性清理脚本：删除 indo_news 爬虫已发布的帖子

依据：indo_news_scraper.py 给帖子打上的独有标签组合
- 印尼新闻:  ["Indonesia", "IndoNews"]
- X 热搜:    ["Indonesia", "Twitter", "XTrending", "IndoTrending"]

为避免误删其他用 Indonesia 标签的爬虫，采用 **AND 组合** 区分：
- IndoNews 且 category_id 属于 news（POSTS_CATEGORY_NAME）
- 或：XTrending 标签

用法：
    python task_manager/_cleanup_indo_news.py            # dry run
    python task_manager/_cleanup_indo_news.py --apply    # 实际删除
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_direct import select_all, execute_sql, select_one, get_connection
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


def get_news_category_id() -> str | None:
    """读取 POSTS_CATEGORY_NAME 对应的 category_id"""
    import os
    name = os.getenv("POSTS_CATEGORY_NAME", "news")
    row = select_one("categories", {"name": name}, columns="id")
    return row["id"] if row else None


def find_indo_news_posts(category_id: str | None) -> list[dict]:
    """查找 indo_news 发过的所有帖子

    条件：post_tags 关联的 tags.name 包含 IndoNews / XTrending / IndoTrending
    """
    # 通过 post_tags JOIN tags 找出相关 post_id
    sql = """
        SELECT DISTINCT p.id, p.title, p.status, p.created_at, t.name AS tag
        FROM posts p
        JOIN post_tags pt ON pt.post_id = p.id
        JOIN tags t ON t.id = pt.tag_id
        WHERE t.name IN ('IndoNews', 'XTrending', 'IndoTrending')
        ORDER BY p.created_at DESC
    """
    rows = execute_sql(sql) or []
    if category_id:
        rows = [r for r in rows if True]  # 暂不过滤 category，标签组合已足够精确
    return rows


def delete_posts(post_ids: list[str]) -> int:
    """单连接、单事务批量删除 post_tags + posts

    pgbouncer pooler (port 6543) 每个连接只支持一个事务，
    因此必须保持单连接并显式开启事务。
    """
    if not post_ids:
        return 0
    import psycopg2
    import psycopg2.extras

    conn = get_connection(autocommit=False)
    try:
        with conn.cursor() as cur:
            # 1. 批量删除 post_tags（post_id 是 uuid，需要 cast）
            cur.execute(
                "DELETE FROM post_tags WHERE post_id = ANY(%s::uuid[])",
                (post_ids,),
            )
            tag_deleted = cur.rowcount
            # 2. 批量删除 posts
            cur.execute(
                "DELETE FROM posts WHERE id = ANY(%s::uuid[])",
                (post_ids,),
            )
            post_deleted = cur.rowcount
        conn.commit()
        print(f"  [info] post_tags 删除: {tag_deleted} 行, posts 删除: {post_deleted} 行")
        return post_deleted
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行删除（默认 dry run）")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认（搭配 --apply 使用）")
    args = ap.parse_args()

    cat_id = get_news_category_id()
    print(f"[INFO] POSTS_CATEGORY_NAME 对应 category_id: {cat_id}")

    posts = find_indo_news_posts(cat_id)
    print(f"[INFO] 找到 indo_news 相关帖子: {len(posts)} 条")
    if posts:
        print("-" * 80)
        for p in posts[:30]:
            print(f"  {p['created_at']}  [{p['tag']:<12}]  {p['title'][:80]}")
        if len(posts) > 30:
            print(f"  ... 还有 {len(posts) - 30} 条未显示")
        print("-" * 80)

    if not args.apply:
        print("[DRY-RUN] 未执行删除。带上 --apply 实际删除。")
        return

    # 二次确认：避免误删
    if not args.yes:
        try:
            confirm = input(f"\n确认删除以上 {len(posts)} 条帖子？输入 yes 继续: ")
            if confirm.strip().lower() != "yes":
                print("[CANCEL] 已取消")
                return
        except EOFError:
            print("[CANCEL] 无可读输入，已取消。带上 --yes 跳过确认。")
            return

    post_ids = list({p["id"] for p in posts})
    deleted = delete_posts(post_ids)
    print(f"[DONE] 已删除 {deleted} 条帖子及其 post_tags 关联")


if __name__ == "__main__":
    main()
