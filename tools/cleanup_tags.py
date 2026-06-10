"""清理数据库 tags：去掉 # 前缀，合并去重"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SUPABASE_URL"] = "https://uqlxbcoowpmxmvwkuomc.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVxbHhiY29vd3BteG12d2t1b21jIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTIyMzc0MSwiZXhwIjoyMDk0Nzk5NzQxfQ.RKo_Do6063K17BWyjn80g4u41mB6HjT44MArwRRLvGk"
from supabase import create_client
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

print("=" * 60)
print("  Tags 清理：去 # 前缀 + 合并去重")
print("=" * 60)

# 1. 获取所有 tags
r = client.table("tags").select("id,name").execute()
all_tags = r.data
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
        # 先查引用 hash tag 的 post_tags
        pt_r = client.table("post_tags").select("post_id,tag_id").eq("tag_id", ht["id"]).execute()
        if pt_r.data:
            for pt in pt_r.data:
                # 检查目标关联是否已存在
                exist = client.table("post_tags").select("post_id").eq("post_id", pt["post_id"]).eq("tag_id", target["id"]).execute()
                if not exist.data:
                    # 更新引用
                    client.table("post_tags").update({"tag_id": target["id"]}).eq("post_id", pt["post_id"]).eq("tag_id", ht["id"]).execute()
                    print(f"    迁移 post_tags: post={pt['post_id'][:8]}...")
                else:
                    # 已存在则删除重复记录
                    client.table("post_tags").delete().eq("post_id", pt["post_id"]).eq("tag_id", ht["id"]).execute()
                    print(f"    删除重复 post_tags: post={pt['post_id'][:8]}...")

        # 3b. 删除 hash tag
        client.table("tags").delete().eq("id", ht["id"]).execute()
        print(f"    已删除: {ht['name']}")

        # 更新 clean_tags 中的引用（因为可能后续还有同名 hash tag）
        merged += 1
    else:
        # 不存在同名 → 直接改名
        print(f"  改名: {ht['name']} -> {clean_name}")
        client.table("tags").update({"name": clean_name}).eq("id", ht["id"]).execute()
        clean_tags[key] = {"id": ht["id"], "name": clean_name}
        renamed += 1

print(f"\n[3/4] 合并: {merged} 个, 改名: {renamed} 个")

# 4. 更新 post_tags 中重复的关联（同一个 post 对同一个 tag 多条记录）
print("\n[4/4] 清理 post_tags 重复关联...")
pt_all = client.table("post_tags").select("post_id,tag_id").execute()
from collections import defaultdict
pairs = defaultdict(list)
for pt in pt_all.data:
    # post_tags 表用复合键 (post_id, tag_id) 去重
    # 没有独立 id 列，需要逐条删除多余的
    pairs[(pt["post_id"], pt["tag_id"])].append(True)

dup_deleted = 0
for (pid, tid), flags in pairs.items():
    if len(flags) > 1:
        # 先删光，再插入一条
        client.table("post_tags").delete().eq("post_id", pid).eq("tag_id", tid).execute()
        client.table("post_tags").insert({"post_id": pid, "tag_id": tid}).execute()
        dup_deleted += len(flags) - 1
if dup_deleted:
    print(f"  修复 {dup_deleted} 条重复 post_tags 记录")

# 5. 验证
r2 = client.table("tags").select("id,name").execute()
final_tags = r2.data
with_hash_final = [t for t in final_tags if t["name"].startswith("#")]
print(f"\n=== 清理完成 ===")
print(f"最终标签数: {len(final_tags)}")
print(f"仍带 # 的: {len(with_hash_final)}")
if with_hash_final:
    print("  ⚠ 警告：仍有带 # 的标签！")
    for t in with_hash_final:
        print(f"    {t['name']}")

# 最终列表
print("\n最终标签列表:")
for t in sorted(final_tags, key=lambda x: x["name"].lower()):
    print(f"  {t['name']}")
