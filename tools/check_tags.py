"""检查数据库 tags 当前状态"""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from db_direct import execute_sql

# 查所有 tags
rows = execute_sql("SELECT id, name FROM tags") or []
tags = rows
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
for clean_name, group in name_groups.items():
    if len(group) > 1:
        print(f"重名: {clean_name} -> {len(group)} 条")
        for t2 in group:
            print(f"  id={t2['id']} name={t2['name']}")
