"""检查数据库 tags 当前状态"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SUPABASE_URL"] = "https://uqlxbcoowpmxmvwkuomc.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVxbHhiY29vd3BteG12d2t1b21jIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTIyMzc0MSwiZXhwIjoyMDk0Nzk5NzQxfQ.RKo_Do6063K17BWyjn80g4u41mB6HjT44MArwRRLvGk"
from supabase import create_client
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

# 查所有 tags
r = client.table("tags").select("id,name").execute()
tags = r.data
print(f"=== 总共 {len(tags)} 个标签 ===")
with_hash = [t for t in tags if t["name"].startswith("#")]
without_hash = [t for t in tags if not t["name"].startswith("#")]
print(f"带 # 前缀: {len(with_hash)} 个")
for t in with_hash:
    print(f"  id={t['id']}  name={t['name']}")
print(f"不带 # 前缀: {len(without_hash)} 个")
for t in without_hash:
    print(f"  id={t['id']}  name={t['name']}")

# 检查是否有重名（不考虑#前缀）
print()
print("=== 重名检查 ===")
name_groups = {}
for t in tags:
    clean = t["name"].lstrip("#")
    name_groups.setdefault(clean, []).append(t)

dup_count = 0
for clean, group in name_groups.items():
    if len(group) > 1:
        dup_count += 1
        print(f"  重名: \"{clean}\" -> {[(g['name'], g['id'][:8]) for g in group]}")

if dup_count == 0:
    print("  无重名")
