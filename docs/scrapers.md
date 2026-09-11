# 爬虫清单

按业务域列出全部采集脚本。**除特别说明外，不加 `--save` 时只抓取并打印预览，不会写库。**

- 命令均为在项目根目录执行。
- "分类变量"指脚本解析发帖分类所用的环境变量，未配置时使用脚本内默认值（多为 `Hot Tokens` / `news` / `Sports Talk`）。
- "调度"列中的命令与 `.github/workflows/scheduler.yml` 保持一致。

## 1. 加密市场（`crypto/`）

| 脚本 | 数据源 | 抓取方式 | 分类变量 | 调度命令 |
|---|---|---|---|---|
| `crypto/price_collector.py` | CoinCap v3 `/assets` | httpx | —（写 `tokens` 表） | `python crypto/price_collector.py` |
| `crypto/airdrop_scraper.py` | Binance / Bybit / OKX / Gate / Kaskus 公告 | httpx | `HOT_TOKENS_CATEGORY_NAME` | `python crypto/airdrop_scraper.py --save --max 20` |
| `crypto/tokocrypto/tokocrypto_scraper.py` | Tokocrypto 活动 API + 活动页 | httpx | `TOKOCRYPTO_CATEGORY_NAME` | `python crypto/tokocrypto/tokocrypto_scraper.py --save --max 10` |
| `crypto/indodax/indodax_scraper.py` | `blog.indodax.com` WordPress REST API | httpx | `INDODAX_CATEGORY_NAME` | `python crypto/indodax/indodax_scraper.py --save --max 15` |
| `crypto/pintu/pintu_scraper.py` | `blog.pintu.co.id` WordPress REST API | httpx | `PINTU_CATEGORY_NAME` | `python crypto/pintu/pintu_scraper.py --save --max 15` |
| `crypto/mobee/mobee_scraper.py` | `mobee.com` market-update（Webflow） | httpx | `MOBEE_CATEGORY_NAME` | `python crypto/mobee/mobee_scraper.py --save --max 10` |
| `crypto/osl/osl_scraper.py` | `osl.com/en-id/announcement`（Nuxt SPA） | Playwright | `OSL_CATEGORY_NAME` | `python crypto/osl/osl_scraper.py --save --max 10` |
| `crypto/bitget/bitget_scraper.py` | `bitget.com/id/news`（SPA） | Playwright | `BITGET_CATEGORY_NAME` | `python crypto/bitget/bitget_scraper.py --save --max 10` |
| `crypto/okx/okx_scraper.py` | `okx.com/id/help/announcements`（SPA） | Playwright | `OKX_CATEGORY_NAME` | `python crypto/okx/okx_scraper.py --save --max 10` |

> `price_collector.py` 无论是否带参数都会写库；缺少 `COINCAP_API_KEY` 或 `DATABASE_URL` 时记录日志并 `sys.exit(0)`（不视为失败）。

## 2. 新闻与体育资讯（`news_scrapers/`）

| 脚本 | 数据源 | 抓取方式 | 分类变量 | 调度命令 |
|---|---|---|---|---|
| `binance/news_scraper.py` | 币安广场新闻页 | Playwright | `POSTS_CATEGORY_NAME` | `python news_scrapers/binance/news_scraper.py --scroll 5 --max 50 --save` |
| `indo_news/indo_news_scraper.py` | Google News RSS + trends24.in + x.com | httpx | `INDO_CATEGORY_NAME` | `python news_scrapers/indo_news/indo_news_scraper.py --save --max 10` |
| `sport_detik/detik_scraper.py` | `sport.detik.com` RSS | httpx | `FIFA_CATEGORY_NAME` | `python news_scrapers/sport_detik/detik_scraper.py --save --max 15` |
| `tribunbola/tribunbola_scraper.py` | `tribunbola.co.id` | httpx | `FIFA_CATEGORY_NAME` | `python news_scrapers/tribunbola/tribunbola_scraper.py --save --max 10` |
| `bolasport/bolasport_scraper.py` | `bolasport.com` | Playwright | `FIFA_CATEGORY_NAME` | `python news_scrapers/bolasport/bolasport_scraper.py --save --max 15` |
| `dailysports/dailysports_scraper.py` | `dailysports.id` | httpx | `FIFA_CATEGORY_NAME` | `python news_scrapers/dailysports/dailysports_scraper.py --save --max 10` |
| `mainbasket/mainbasket_scraper.py` | `mainbasket.com`（IBL 篮球） | httpx | `FIFA_CATEGORY_NAME` | `python news_scrapers/mainbasket/mainbasket_scraper.py --save --max 15` |
| `ibl_data/ibl_data_scraper.py` | FIBA LiveStats（IBL Gopay 2026） | httpx | `FIFA_CATEGORY_NAME` | `python news_scrapers/ibl_data/ibl_data_scraper.py --save` |

> `binance/assemble_posts.py` 已废弃：运行时会提示改用 `news_scraper.py --save`。保留仅作历史参考。

## 3. 足球 / 世界杯 / 羽毛球（`sport/`）

| 脚本 | 数据源 | 抓取方式 | 分类变量 | 调度命令 |
|---|---|---|---|---|
| `schedule/fifa_scraper.py` | FIFA 2026 赛程 API | Playwright + httpx | `FIFA_CATEGORY_NAME` | `python sport/schedule/fifa_scraper.py --save` |
| `blog/fifa_blog_scraper.py` | FIFA 官方 Blog | Playwright + httpx | `FIFA_CATEGORY_NAME` | `python sport/blog/fifa_blog_scraper.py --save --max 30` |
| `goal/goal_scraper.py` | Goal.com 世界杯比分 | Playwright + httpx | `FIFA_CATEGORY_NAME` | `python sport/goal/goal_scraper.py --save --live` |
| `forebet/forebet_scraper.py` | Forebet 印尼 Liga 1 | CloakBrowser | `FIFA_CATEGORY_NAME` | `python sport/forebet/forebet_scraper.py --save` |
| `fastscore/fastscore_scraper.py` | Fastscore 印尼 Liga 1 | CloakBrowser | `FIFA_CATEGORY_NAME` | `python sport/fastscore/fastscore_scraper.py --save` |
| `footballant/footballant_scraper.py` | Footballant 印尼超级联赛 | CloakBrowser | `FIFA_CATEGORY_NAME` | `python sport/footballant/footballant_scraper.py --save` |
| `badminton/bwf_indonesia_scraper.py` | BWF World Tour 印尼公开赛/大师赛 | CloakBrowser | `FIFA_CATEGORY_NAME` | `python sport/badminton/bwf_indonesia_scraper.py --save` |
| `badminton/bwf_calendar_scraper.py` | olympics.com + BWF 全年赛历 | CloakBrowser | `FIFA_CATEGORY_NAME` | `python sport/badminton/bwf_calendar_scraper.py --save` |

> `goal_scraper.py` 的 `--live` 用于抓取进行中的比赛；已完赛与进行中都会更新到同一条帖子。

## 4. 电竞（`esports/`）

| 脚本 | 数据源 | 抓取方式 | 分类变量 | 调度命令 |
|---|---|---|---|---|
| `esports/esports_scraper.py` | `duniagames.co.id` | Playwright + httpx | `ESPORTS_CATEGORY_NAME` | `python esports/esports_scraper.py --save --max 10` |

## 5. AI 生成（`ai_digest/`）

| 脚本 | 数据源 | 抓取方式 | 环境变量 | 命令 |
|---|---|---|---|---|
| `ai_digest/ai_digest.py` | DeepSeek（`deepseek-chat`） | httpx | `DEEPSEEK_API_KEY`、`HOT_TOKENS_CATEGORY_NAME`、`POSTS_AUTHOR_USERNAME` | `python ai_digest/ai_digest.py --save --max 10` |
| `ai_digest/bot_posts.py` | DeepSeek + 现有新闻 | httpx | `DEEPSEEK_API_KEY`、`HOT_TOKENS_CATEGORY_NAME` | `python ai_digest/bot_posts.py --save --max 10 --bots 5` |

- `ai_digest.py`：读取 `news` 分类最新帖子 → 生成中文日报（标题/摘要/分析/展望/标签）→ 由 `indoAdmin` 发布到 `Hot Tokens`。
- `bot_posts.py`：随机选取机器人账号，以各自身份对最新加密新闻发表观点。
- ⚠️ 这两个脚本**当前未包含在 GitHub Actions 调度中**（`scheduler_engine.py` 的 "AI 生成" 分类为空），需通过本地 GUI 或手动运行。

## 6. 美股（`us_stock_scraper/`）

| 脚本 | 数据源 | 抓取方式 | 写入表 | 命令 |
|---|---|---|---|---|
| `us_stock_scraper/us_stock_scraper.py` | Yahoo Finance chart API（1 分钟 OHLCV） | CloakBrowser | `us_stock_bars`、`us_stock_trends`、`stock_symbols` | `python us_stock_scraper/us_stock_scraper.py --upload --backfill 4` |

- `--upload`：写库；不加则只抓取（不落库）。
- `--backfill N`：检测并补全最近 N 个交易日缺失的 K 线（需配合 `--upload`）。

## 7. 独立子系统：Telegram 群汇总（`tg_summary_bot/`）

| 脚本 | 说明 | 命令 |
|---|---|---|
| `tg_summary_bot/main.py` | 主入口：Telethon 用户账号监听群消息 + APScheduler 定时生成报告 | `python tg_summary_bot/main.py` |

- 依赖独立的 `tg_summary_bot/requirements.txt` 与 `tg_summary_bot/.env`（`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` / `REPORT_CHANNEL_ID` 等）。
- 首次运行需手机号验证登录，session 保存为 `tg_summary_bot/tg_session*`。
- 数据存本地 SQLite，不写 Supabase。

## 8. 手动管理任务

| 任务 | 脚本 | 说明 |
|---|---|---|
| 批量创建机器人 | `task_manager/create_bots.py` | 在 `profiles` 批量创建机器人账号 |
| 清空全部帖子/评论/标签 | `tools/clean_all.py` | ⚠️ 危险操作，需输入 YES 确认 |
| 清理 indo_news 帖子 | `task_manager/_cleanup_indo_news.py` | 一次性清理脚本 |

前两者在本地 GUI 中作为内置"手动触发"任务注册（不参与定时调度）。
