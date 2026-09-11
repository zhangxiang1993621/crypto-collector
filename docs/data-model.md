# 数据模型

本项目所有业务数据存放在 **Supabase PostgreSQL**，通过 `db_direct.py` 直连读写（详见 [`architecture.md`](architecture.md) 第 5.1 节）。本文列出代码中实际使用的表、字段与写入语义。

> 说明：字段清单依据代码中的 `insert_one` / `upsert_one` 调用推导，数据库侧可能还存在主键、外键、默认值等约束（以 Supabase 实际 schema 为准）。

## 1. 连接配置（`db_direct.py`）

优先级从高到低：

1. `DATABASE_URL`：完整连接串 `postgresql://user:password@host:5432/dbname`（密码中的特殊字符自动 URL-decode）。
2. `SUPABASE_URL` + `SUPABASE_DB_PASSWORD`：自动拼出 `db.<project-ref>.supabase.co:5432/postgres`。

连接参数：`sslmode=require`、`connect_timeout=30`；默认 `autocommit=True`（`batch_upsert` / `insert_many` 使用事务）。

## 2. `db_direct.py` 函数一览

| 函数 | 作用 | 说明 |
|---|---|---|
| `get_connection(autocommit=True)` | 获取 psycopg2 连接 | VACUUM 等需要 autocommit |
| `execute_sql(sql, params, fetch)` | 执行任意 SQL | 返回 dict 行列表或 None |
| `select_one(table, where, columns)` | 单行查询 | 等价 Supabase `.select().eq()` |
| `select_all(table, where, columns, limit)` | 多行查询 | `where=None` 表示无条件 |
| `existing_titles(category_id, titles)` | 查询给定标题中已存在于 `posts` 的集合 | 发帖前跨轮去重 |
| `insert_one(table, data, returning)` | 单行插入 | 返回插入行 |
| `insert_many(table, rows)` | 批量插入（事务） | 返回行数 |
| `update_one(table, data, where)` | 单行更新 | 返回更新行数 |
| `upsert_one(table, data, conflict_key)` | 单条 upsert | `ON CONFLICT ... DO UPDATE` |
| `batch_upsert(table, rows, conflict_key)` | 批量 upsert（事务） | 支持复合键（逗号分隔） |
| `count_table(table)` | 行数统计 | |
| `truncate_table(table)` | 清空表 | `TRUNCATE ... RESTART IDENTITY CASCADE` |
| `vacuum_table(table, full)` | 回收空间 | `VACUUM [FULL] (VERBOSE, ANALYZE)` |

## 3. 表清单

### 3.1 内容与用户

| 表 | 关键字段 | 用途 |
|---|---|---|
| `profiles` | `id`, `username`, `is_bot` | 发帖账号；机器人账号由 `task_manager/create_bots.py` 批量创建（头像用 dicebear 生成） |
| `categories` | `id`, `name` | 帖子分类；脚本按 `*_CATEGORY_NAME` 解析 |
| `posts` | `id`, `title`, `content`, `author_id`, `category_id`, `post_type`, `status`, `created_at`, `updated_at` | 帖子主体（HTML 富文本） |
| `tags` | `id`, `name`, `posts_count` | 标签；`posts_count` 由部分脚本维护 |
| `post_tags` | `post_id`, `tag_id` | 帖子-标签关联 |
| `comments` | — | 评论（由 `tools/clean_all.py` 清理时引用） |
| `likes` | — | 点赞 |
| `bookmarks` | — | 收藏 |

`posts` 写入约定：

- `post_type = "info"`
- `status = "pending_review"`
- `created_at` / `updated_at`：UTC ISO 字符串

### 3.2 行情数据

| 表 | 字段 | 冲突键 | 写入脚本 |
|---|---|---|---|
| `tokens` | `coincap_id`, `name`, `full_name`, `symbol`, `price`, `change_24h`, `market_cap`, `volume_24h` | `coincap_id` | `crypto/price_collector.py` |
| `us_stock_bars` | `symbol`, `name`, `type`, `bar_time`, `open`, `high`, `low`, `close`, `volume` | `symbol,bar_time` | `us_stock_scraper/us_stock_scraper.py` |
| `us_stock_trends` | `symbol`, `name`, `type`, `color`, `latest_close`, `latest_time`, `intraday_open`, `intraday_high`, `intraday_low`, `change_pct`, `volume_total`, `bars_count`, `updated_at` | `symbol` | 同上 |
| `stock_symbols` | `symbol`, `name`, `market`, `category`, `color`, `enabled` | `symbol` | 同上（种子元信息，幂等） |

`us_stock_bars.type` 取值 `index`（`^` 开头）或 `stock`。

## 4. 表关系

```mermaid
erDiagram
  profiles ||--o{ posts : author_id
  categories ||--o{ posts : category_id
  posts ||--o{ post_tags : post_id
  tags ||--o{ post_tags : tag_id
  posts ||--o{ comments : post_id
  posts ||--o{ likes : post_id
  posts ||--o{ bookmarks : post_id
```

## 5. 去重与 upsert 语义

| 场景 | 策略 | 示例脚本 |
|---|---|---|
| 新闻类发帖 | 按 `title` 查重，存在则跳过 | `news_scrapers/*`、`crypto/{pintu,indodax,...}` |
| 比分/赛事类 | 按 `title` 找已有帖子 → `UPDATE content`；无则插入 | `sport/goal`、`sport/fastscore`、`sport/badminton` |
| 价格/行情 | `batch_upsert` 按业务键 | `tokens`、`us_stock_bars`、`us_stock_trends` |
| 标签 | 先按 `name` 查/建，再查 `post_tags` 是否已关联 | 各发帖脚本的 `sync_tags()` |

## 6. 本地存储（非 Supabase）

`tg_summary_bot/` 使用本地 SQLite（`tg_summary_bot/tg_summary.db`），与 Supabase 无关：

| 表 | 用途 |
|---|---|
| `messages` | 群消息原文（chat_id / user / text / message_date） |
| `reports` | 生成的群聊报告（report_text / message_count / 时间段 / posted） |

调度元数据 `task_config.json`（本地，已 gitignore）记录本地任务开关与 cron，见 [`scheduling.md`](scheduling.md)。
