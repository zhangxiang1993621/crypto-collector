# 运维手册

## 1. 环境变量全量清单

项目根 `.env`（由 `.env.example` 复制而来，**不要提交真实值**）。

### 1.1 数据库

| 变量 | 必填 | 说明 |
|---|---|---|
| `DATABASE_URL` | 推荐 | 完整直连串 `postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres` |
| `SUPABASE_URL` | 备选 | `https://<ref>.supabase.co`，与 `SUPABASE_DB_PASSWORD` 配合 |
| `SUPABASE_DB_PASSWORD` | 备选 | 数据库密码 |
| `SUPABASE_SERVICE_ROLE_KEY` | 部分脚本 | `task_manager/create_bots.py` 等使用 Supabase SDK 时必需 |

> `DATABASE_URL` 与 `SUPABASE_URL`+`SUPABASE_DB_PASSWORD` 二选一；前者优先。密码含特殊字符时会自动 URL-decode。

### 1.2 外部 API

| 变量 | 用途 |
|---|---|
| `COINCAP_API_KEY` | CoinCap 价格采集（`crypto/price_collector.py`） |
| `DEEPSEEK_API_KEY` | AI 日报与机器人观点（`ai_digest/`） |

### 1.3 发帖与分类

| 变量 | 默认值 | 用于 |
|---|---|---|
| `POSTS_AUTHOR_USERNAME` | `indoAdmin` | 默认发帖账号 |
| `POSTS_CATEGORY_NAME` | `news` | 币安新闻、FIFA 赛程 |
| `FIFA_CATEGORY_NAME` | `Sports Talk` | 体育类爬虫 |
| `INDO_CATEGORY_NAME` | `Indo Street` | 印尼热点 |
| `PINTU_CATEGORY_NAME` | `Hot Tokens` | Pintu |
| `TOKOCRYPTO_CATEGORY_NAME` | `Hot Tokens` | Tokocrypto |
| `INDODAX_CATEGORY_NAME` | `Hot Tokens` | Indodax |
| `OSL_CATEGORY_NAME` | `Hot Tokens` | OSL |
| `MOBEE_CATEGORY_NAME` | `Hot Tokens` | Mobee |
| `BITGET_CATEGORY_NAME` | `Hot Tokens` | Bitget |
| `OKX_CATEGORY_NAME` | `Hot Tokens` | OKX |
| `ESPORTS_CATEGORY_NAME` | `E-Sports` | 电竞 |
| `HOT_TOKENS_CATEGORY_NAME` | `Hot Tokens` | AI 日报、空投 |

> 上表"默认值"指脚本内 `.or(...)` 兜底值。`.env.example` 中把多家交易所分类的**示例值**写成了 `news`；实际取值以 `.env` 为准，且必须与 Supabase `categories` 表中已存在的分类名一致。

### 1.4 Telegram 群汇总（`tg_summary_bot/.env`，独立配置）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TELEGRAM_API_ID` | — | my.telegram.org 申请 |
| `TELEGRAM_API_HASH` | — | 同上 |
| `TELEGRAM_PHONE` | — | 登录手机号（首次需验证码） |
| `REPORT_CHANNEL_ID` | — | 报告发送目标频道 |
| `SUMMARY_INTERVAL_HOURS` | `6` | 报告生成间隔 |
| `MESSAGE_RETENTION_DAYS` | `30` | 消息保留天数 |
| `MIN_MESSAGE_COUNT` | `5` | 生成报告的最小消息数 |
| `TOP_TALKERS_COUNT` | `10` | 话痨榜人数 |
| `TOP_WORDS_COUNT` | `10` | 热词榜数量 |

## 2. 首次配置步骤

1. `pip install -r requirements.txt`
2. `playwright install chromium`
3. 按需安装 CloakBrowser（`us_stock_scraper`、`sport/forebet|fastscore|footballant|badminton` 使用）。
4. `cp .env.example .env`，填入真实数据库串与 API Key。
5. 确认分类与账号在 Supabase `categories` / `profiles` 中存在（缺少时脚本会报错退出）。
6. 本地验证：`python run_task_manager.py`，点"立即执行"试跑；或直接命令行跑单个脚本（不带 `--save` 先预览）。

## 3. 运维脚本（`tools/`）

| 脚本 | 用途 | 典型命令 |
|---|---|---|
| `tools/diagnose_db.py` | 分析各表行数、死元组，定位配额瓶颈 | `python tools/diagnose_db.py` |
| `tools/vacuum_db.py` | 直连 PostgreSQL 回收死元组空间 | `python tools/vacuum_db.py --table us_stock_bars` |
| `tools/clean_all.py` | ⚠️ 清空 posts/comments/post_tags/likes/bookmarks 及**全部 tags**（测试用） | `python tools/clean_all.py --dry-run` |
| `tools/cleanup_tags.py` | 清理 tags：去掉 `#` 前缀并合并重复 | `python tools/cleanup_tags.py` |
| `tools/check_tags.py` | 查看当前 tags 状态（含 `#` 前缀统计） | `python tools/check_tags.py` |
| `tools/read_invoice.py` | 读取 Supabase Invoice PDF 内容 | `python tools/read_invoice.py` |
| `task_manager/_cleanup_indo_news.py` | 一次性清理 indo_news 已发布帖子 | `python task_manager/_cleanup_indo_news.py` |

**支持 `--dry-run` 的脚本**：`clean_all.py`、`vacuum_db.py`。执行破坏性操作前先用 dry-run 确认。

## 4. 依赖

`requirements.txt`（主项目）：

| 包 | 用途 |
|---|---|
| `httpx` | HTTP 客户端 |
| `supabase` | Supabase SDK（部分脚本） |
| `psycopg2-binary` | PostgreSQL 直连（`db_direct.py`） |
| `playwright` | 浏览器自动化（首选） |
| `cloakbrowser` | 反检测浏览器（备选） |
| `python-dotenv` | `.env` 加载 |
| `apscheduler` | 本地调度 |
| `pyyaml` | YAML 解析/生成 |
| `yfinance` | 美股行情数据 |

`tg_summary_bot/requirements.txt` 为独立子系统依赖（含 `telethon` 等）。

## 5. 故障排查

| 现象 | 可能原因 | 排查/处理 |
|---|---|---|
| 脚本启动即 `sys.exit` 并提示缺少环境变量 | `.env` 未配置或未被加载 | 检查项目根 `.env`；确认变量名与本文一致 |
| `缺少 DATABASE_URL 或 SUPABASE_DB_PASSWORD` | 直连配置缺失 | 配置 `DATABASE_URL` 或 `SUPABASE_URL`+`SUPABASE_DB_PASSWORD` |
| 发帖脚本报"分类不存在"/"账号不存在" | Supabase `categories`/`profiles` 中无对应记录 | 先运行 `create_bots.py`，或补建分类 |
| Playwright 报浏览器缺失 | 未执行 `playwright install chromium` | 安装浏览器后重试 |
| 抓取被拦截（403 / 超时 / 空内容） | 目标站反爬 | 按 [`conventions.md`](conventions.md) 从 Playwright 降级 CloakBrowser |
| `fastscore` 日志出现 `Cloudflare blocked` | Cloudflare 拦截 | 属已知情况，脚本会安全退出 |
| `footballant` 提示检查 `LEAGUE_ID` | 联赛 ID 变更 | 更新脚本中的 `LEAGUE_ID` |
| 帖子重复 | 去重键未命中（标题被截断/改写） | 检查脚本的去重逻辑与 `title[:200]` 截断 |
| Supabase 存储/配额告警 | 死元组堆积 | `python tools/diagnose_db.py` → `python tools/vacuum_db.py` |
| 本地 GUI 改了 cron 但云端未生效 | `task_config.json` 与 YAML 未同步 | 在面板点"同步到 YAML"并提交推送 |

## 6. 安全注意事项

1. **不要提交 `.env`**（已在 `.gitignore` 中）。
2. `.env.example` 只允许占位符。历史提交中一旦出现过真实密钥，**必须轮换**对应的 Supabase Service Role Key、DeepSeek Key、CoinCap Key，仅删除文件不足以恢复安全。
3. GitHub Actions 使用 Secrets/Variables 注入，不要把密钥写进 workflow 文件。
4. `SUPABASE_SERVICE_ROLE_KEY` 权限极高（绕过 RLS），只授予确实需要的脚本。
