# Crypto Collector

加密货币、体育赛事、新闻资讯多源数据采集与发布系统。通过爬虫自动抓取数据，存入 Supabase 后端，前端站点消费展示。

## 项目结构

```
crypto-collector/
├── price_collector/       # 加密货币价格采集（CoinCap API）
├── news_scraper/          # 币安广场新闻抓取（Playwright）
├── airdrop_scraper/       # 交易所空投福利公告
├── ai_digest/             # AI 新闻摘要 + 机器人观点发帖
├── fifa_scraper/          # 世界杯赛程抓取
├── fifa_blog_scraper/     # FIFA 官方博客文章
├── worldcup_scraper/      # 2026 世界杯比分实时更新
├── esports_scraper/       # 印尼电子竞技新闻（DuniaGames）
├── indo_news_scraper/     # 印尼热点新闻（Google+Twitter）
├── us_stock_scraper/      # 美股分钟K线采集+图表生成
├── bilibili_scraper/      # B站视频采集
├── tg_summary_bot/        # Telegram 群消息汇总 Bot
├── task_manager/          # 本地 GUI 任务调度面板（APScheduler）
├── supabase_client.py     # Supabase 客户端封装
├── task_config.json        # 任务调度配置
├── run_task_manager.py    # 任务面板启动入口
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量模板
└── .github/workflows/     # GitHub Actions 定时调度
```

## 所有爬虫一览

| 爬虫 | 目录 | 数据源 | 分类 | 发帖人 | 频率 |
|---|---|---|---|---|---|
| 加密价格 | `price_collector/` | CoinCap API | - | - | 每 30 分钟 |
| 币安新闻 | `news_scraper/` | 币安广场 | news | indoAdmin | 每 2 小时 |
| 空投福利 | `airdrop_scraper/` | 各大交易所 | Hot Tokens | indoAdmin | 每 6 小时 |
| AI 摘要 | `ai_digest/` | AI 生成 | Hot Tokens | 系统 | 每 4 小时 |
| 机器人观点 | `ai_digest/` | AI 生成 | Hot Tokens | 随机 Bot | 每天 12:00 |
| 世界杯赛程 | `fifa_scraper/` | FIFA API | Sports Talk | indoAdmin | 每 6 小时 |
| FIFA Blog | `fifa_blog_scraper/` | FIFA 官网 | Sports Talk | indoAdmin | 每 2 小时 |
| 世界杯比分 | `worldcup_scraper/` | 搜狐体育+FIFA | Sports Talk | indoAdmin | 每 30 分钟 |
| 电竞新闻 | `esports_scraper/` | DuniaGames | E-Sports | indoAdmin | 每天 8/20 点 |
| 印尼热点 | `indo_news_scraper/` | Google+Twitter | Indo Street | indoAdmin | 每天 7/19 点 |
| 美股数据 | `us_stock_scraper/` | Yahoo Finance | - | - | 工作日 21:30 |
| B站视频 | `bilibili_scraper/` | 哔哩哔哩 | - | - | 手动 |
| TG 摘要 | `tg_summary_bot/` | Telegram 群聊 | - | - | 手动 |

## 快速开始

### 环境要求

- Python 3.11+
- Windows / Linux / macOS

### 安装

```bash
pip install -r requirements.txt

# Playwright 浏览器（news / fifa / esports / worldcup 需要）
playwright install chromium

# CloakBrower 浏览器（us_stock 需要）
cloakbrowser install
```

### 配置

复制 `.env.example` 为 `.env`，填入 Supabase 和 API 密钥：

```bash
cp .env.example .env
```

必填变量：

| 变量 | 说明 |
|---|---|
| `SUPABASE_URL` | Supabase 项目地址 |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase Service Role Key |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（AI 摘要需要） |
| `COINCAP_API_KEY` | CoinCap API Key（价格采集需要） |

发布配置：

| 变量 | 说明 |
|---|---|
| `POSTS_AUTHOR_USERNAME` | 默认发帖用户名 |
| `POSTS_CATEGORY_NAME` | 新闻分类 |
| `FIFA_CATEGORY_NAME` | FIFA / 世界杯分类 |
| `INDO_CATEGORY_NAME` | 印尼新闻分类 |
| `ESPers_CATEGORY_NAME` | 电竞分类 |
| `HOT_TOKENS_CATEGORY_NAME` | AI 摘要分类 |

### 运行方式

**方式一：本地任务面板（推荐）**

```bash
python run_task_manager.py
```

打开 GUI 面板后可以：启停任务、编辑 cron、查看实时日志、手动触发。

**方式二：GitHub Actions**

推送代码到 `main` 分支后自动按 cron 调度。也可在 [Actions 页面](https://github.com/zhangxiang1993621/crypto-collector/actions) 手动触发。

GitHub 上需要配置的 Secrets / Variables：
- Settings → Secrets and variables → Actions → Secrets
- Settings → Secrets and variables → Actions → Variables

**方式三：命令行单独执行**

```bash
# 世界杯比分
python worldcup_scraper/worldcup_scraper.py --save --today --max 20

# 币安新闻
python news_scraper/news_scraper.py --scroll 5 --max 50 --save

# AI 摘要
python ai_digest/ai_digest.py --save --max 10
```

## 核心设计

### 防重复机制

所有发帖类爬虫使用 **title 去重** 的 upsert 策略：

1. 爬取到数据后，生成唯一标题
2. 查询 Supabase `posts` 表中是否存在同标题帖子
3. 存在 → UPDATE 更新内容（如世界杯比分刷新）
4. 不存在 → INSERT 新建帖子

### 世界杯比分更新

`worldcup_scraper` 的特殊逻辑：
- **首次运行**：为当天 + 未来的比赛各创建一条帖子
- **后续运行**：仅更新已有帖子的内容（比分），不新建重复帖子
- 每场比赛对应一条帖子，标题格式：`⚽ TeamA vs TeamB — 阶段名`

### CI 容错

GitHub Actions 中所有 job 均设置 `continue-on-error: true`，任何单个任务失败不影响其余任务。失败任务在 Actions 页面显示黄色警告图标。

## 项目依赖

```
httpx              HTTP 客户端
supabase           Supabase SDK
playwright         浏览器自动化（首选）
cloakbrowser       反检测浏览器（备选）
python-dotenv      环境变量管理
apscheduler        本地任务调度
pyyaml             CI 配置解析
yfinance           美股行情数据
mplfinance         金融K线图绘制
matplotlib         图表渲染
```

## 许可证

MIT
