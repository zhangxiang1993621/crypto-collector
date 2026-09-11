# 编码规范

本文件约定采集脚本的统一写法，新增或修改爬虫时必须遵守，以保证脚本可被调度、可去重、可维护。

## 1. 浏览器自动化工具选择

**规则**：需要浏览器时**优先 Playwright**；若 Playwright 无法通过目标站反爬检测，再降级 **CloakBrowser**。

```
开始
  │
  ▼
用 Playwright 访问目标网站
  │
  ├── 成功（HTTP 200 + 有效内容） ──▶ 用 Playwright 编写
  │
  └── 失败（超时 / 403 / 429 / 反爬拦截 / 无有效内容） ──▶ 用 CloakBrowser 编写
```

两个库 API 兼容，主要差别在 import 与启动方式：

```python
# Playwright
from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
page = browser.new_page()

# CloakBrowser（后续 page.xxx() API 一致）
from cloakbrowser import launch
browser = launch(headless=True)
page = browser.new_page()
```

**项目实例**：`us_stock_scraper/us_stock_scraper.py` 访问 Yahoo Finance 时 Playwright 超时，改用 CloakBrowser 成功。

**当前使用 CloakBrowser 的脚本**：`us_stock_scraper`、`sport/forebet`、`sport/fastscore`、`sport/footballant`、`sport/badminton/*`。

## 2. 新增爬虫步骤

### 2.1 目录与文件

```
<域>/<source>/
├── __init__.py            # 空文件
└── <source>_scraper.py    # 主脚本
```

### 2.2 脚本骨架

```python
"""<来源> 抓取 + 发帖

功能：从 <URL> 抓取 <内容>，写入 Supabase posts 表
用法：
    python <source>_scraper.py                  # 抓取并打印预览
    python <source>_scraper.py --save           # 抓取并入库
    python <source>_scraper.py --save --max 10  # 最多 10 条
"""

import os
import re
import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

# 1) 让脚本能 import 根目录的 db_direct（层级按目录深度调整）
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv
from db_direct import select_one, select_all, insert_one, execute_sql

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CATEGORY_ENV = "<XXX>_CATEGORY_NAME"
DEFAULT_CATEGORY = "news"
TAGS_DEFAULT = [...]


def get_author_id():      # profiles 按 POSTS_AUTHOR_USERNAME 解析
def get_category_id():    # categories 按 CATEGORY_ENV 解析，失败 sys.exit(1)
def fetch_items():        # 抓取
def build_post_html(item):  # 生成 HTML
def sync_tags(post_id, tags):  # tags + post_tags
def deduplicate(items):   # 标题去重
def run(save=False, max_items=10): ...
def main():               # argparse: --save / --max
```

### 2.3 必须遵守的点

1. **`--save` 控制写库**：默认只抓取并打印预览，绝不静默写库。
2. **`--max` 限制条数**：默认值适中（多为 10），便于调度时控制。
3. **环境变量缺失要"安全退出"**：缺少关键变量时记录日志并 `sys.exit(0)`（或明确的错误码），不要抛出长堆栈。
4. **路径**：统一用 `pathlib.Path` 与基于 `__file__` 的相对定位，不写死绝对路径。
5. **日志**：使用 `logging`，便于调度器捕获子进程输出。
6. **编码**：注意 UTF-8；HTML 正文中的用户数据必须用 `_e()` 转义。

## 3. 发帖规范

| 项 | 规范 |
|---|---|
| 目标表 | `posts` |
| `post_type` | `"info"` |
| `status` | `"pending_review"` |
| `title` | 截断到 200 字符（`title[:200]`） |
| `content` | HTML 富文本，`build_post_html()` 生成 |
| `created_at` / `updated_at` | UTC ISO 字符串 |
| 语言 | 面向印尼受众的内容使用**印尼语** |
| 更新类内容 | 比分/赛事用"按标题找到 → UPDATE content"，不要重复插入 |

## 4. 标签规范

- 标签名不带 `#` 前缀（`tools/cleanup_tags.py` 用于清理历史遗留的 `#`）。
- 通过 `sync_tags()` 写 `tags` + `post_tags`；先查后建，避免重复。
- 适当维护 `tags.posts_count`（部分脚本会更新）。

## 5. 接入调度

新增爬虫后需要同步以下位置，否则不会被调度/显示：

1. **`.github/workflows/scheduler.yml`**：新增 job（`runs-on`、`continue-on-error: true`、四步流程、`env`、`run`）。
2. **`task_manager/scheduler_engine.py`**：
   - `TASK_CATEGORIES` 中加入任务名（本地 GUI 分类显示）。
   - `_job_label()` 中补充中文描述。
3. **`.env.example`**：新增所需环境变量并写注释。
4. **文档**：更新 [`scrapers.md`](scrapers.md)、[`scheduling.md`](scheduling.md) 的对照表。

> 若爬虫需要额外的 Secrets/Variables，记得同步在 GitHub 仓库 Settings 中配置。

## 6. 通用

- Python 版本：3.11+；类型注解按现有风格（`str | None`）。
- 不要提交 `.env`、`task_config.json`、`output/`、`*.session`、`*.db`（见 `.gitignore`）。
- 破坏性运维操作必须提供 `--dry-run` 或二次确认。

## 7. 开发协作（DSH Superpowers）

本项目使用 DSH（DeepSeek Harness）的 Superpowers 工作流套件：

- 规则层：`AGENTS.md` 的 `superpowers-dsh` 标记段。
- 工作流技能：`.dsh/skills/`（每个新会话的第一步是加载 `using-superpowers`）。
- Subagent 模板：`.dsh/agents/`。
- 安装校验：在独立 PowerShell 7 终端执行 `pwsh -NoProfile -File .dsh/scripts/validate-install.ps1`。

提交前请确保文档与代码一致（见 [`README.md`](README.md) 的维护约定）。
