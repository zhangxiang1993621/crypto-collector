"""清理数据库 tags：去掉 # 前缀，合并去重"""
import os, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# 直连数据库
from db_direct import execute_sql, select_all, select_one, insert_one, update_one

def delete_where(table: str, where: dict) -> int:
    """按条件删除行，返回删除行数"""
    where_clause = " AND ".join([f'"{k}" = %s' for k in where.keys()])
    sql = f'DELETE FROM "{table}" WHERE {where_clause}'
    result = execute_sql(sql, tuple(where.values()), fetch=False)
    return 0 if result is None else len(result) if isinstance(result, list) else 0


print("=" * 60)
print("  Tags 清理：去 # 前缀 + 合并去重")
print("=" * 60)

# 1. 获取所有 tags
all_rows = execute_sql("SELECT id, name FROM tags")
all_tags = all_rows or []
print(f"\n[1/4] 当前共 {len(all_tags)} 个标签")

# 2. 区分带 # 和不带 # 的
hash_tags = [t for t in all_tags if t["name"].startswith("#")]
clean_tags = {t["name"].lower(): t for t in all_tags if not t["name"].startswith("#")}
print(f"[2/4] 带 # 前缀: {len(hash_tags)} 个, 不带 #: {len(clean_tags)} 个")

# 3. 逐个处理带 # 的 tag
merged = 0
renamed = 0
for ht in hash_tags:
    clean_name = ht["name"].lstrip("#")
    key = clean_name.lower()

    if key in clean_tags:
        # 存在同名 clean tag → 合并
        target = clean_tags[key]
        print(f"  合并: {ht['name']} -> {target['name']} (id={target['id'][:8]}...)")

        # 3a. 将 post_tags 中引用 hash tag 的记录改为引用 clean tag
        pt_rows = execute_sql(
            "SELECT post_id, tag_id FROM post_tags WHERE tag_id = %s", (ht["id"],)
        ) or []
        if pt_rows:
            for pt in pt_rows:
                exist = select_one("post_tags", {"post_id": pt["post_id"], "tag_id": target["id"]}, columns="post_id")
                if not exist:
                    update_one("post_tags", {"tag_id": target["id"]}, {"post_id": pt["post_id"], "tag_id": ht["id"]})
                    print(f"    迁移 post_tags: post={pt['post_id'][:8]}...")
                else:
                    delete_where("post_tags", {"post_id": pt["post_id"], "tag_id": ht["id"]})
                    print(f"    删除重复 post_tags: post={pt['post_id'][:8]}...")

        # 3b. 删除 hash tag
        delete_where("tags", {"id": ht["id"]})
        print(f"    已删除: {ht['name']}")
        merged += 1
    else:
        # 不存在同名 → 直接改名
        print(f"  改名: {ht['name']} -> {clean_name}")
        update_one("tags", {"name": clean_name}, {"id": ht["id"]})
        clean_tags[key] = {"id": ht["id"], "name": clean_name}
        renamed += 1

print(f"\n[3/4] 合并: {merged} 个, 改名: {renamed} 个")

# 4. 更新 post_tags 中重复的关联（同一个 post 对同一个 tag 多条记录）
print("\n[4/4] 清理 post_tags 重复关联...")
pt_all = execute_sql("SELECT post_id, tag_id FROM post_tags") or []
pairs = defaultdict(list)
for pt in pt_all:
    pairs[(pt["post_id"], pt["tag_id"])].append(True)

dup_deleted = 0
for (pid, tid), flags in pairs.items():
    if len(flags) > 1:
        delete_where("post_tags", {"post_id": pid, "tag_id": tid})
        insert_one("post_tags", {"post_id": pid, "tag_id": tid})
        dup_deleted += len(flags) - 1
if dup_deleted:
    print(f"  修复 {dup_deleted} 条重复 post_tags 记录")

# 5. 验证
final_rows = execute_sql("SELECT id, name FROM tags") or []
with_hash_final = [t for t in final_rows if t["name"].startswith("#")]
print(f"\n=== 清理完成 ===")
print(f"最终标签数: {len(final_rows)}")
print(f"仍带 # 的: {len(with_hash_final)}")
if with_hash_final:
    print("  ⚠ 警告：仍有带 # 的标签！")
    for t in with_hash_final:
        print(f"    {t['name']}")

# 最终列表
print("\n最终标签列表:")
for t in sorted(final_rows, key=lambda x: x["name"].lower()):
    print(f"  {t['name']}")
