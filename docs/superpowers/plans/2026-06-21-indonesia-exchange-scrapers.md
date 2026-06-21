# 印尼加密交易所新闻抓取 实施计划

> **For agentic workers:** 使用此计划逐步实施。步骤使用 checkbox (`- [ ]`) 语法追踪。

**Goal:** 为 6 个印尼加密交易所新增新闻抓取脚本，接入现有任务调度系统。

**Architecture:** 每个交易所独立一个脚本目录，复用 `db_direct`、`sync_tags` 等公共模式。API 优先（httpx），API 不可用或需要 JS 渲染时用 Playwright。

**Tech Stack:** Python 3.11, httpx, Playwright, psycopg2 (db_direct), 现有项目架构

---

## 数据源汇总

| # | 交易所 | 数据源 | 方式 | 频率 |
|---|--------|--------|------|------|
| 1 | **Tokocrypto** | `v1/activity-menus` API + `/en/campaign/{id}` HTML | httpx | 2×/day |
| 2 | **Indodax** | `blog.indodax.com/wp-json/wp/v2/posts` | httpx (WP API) | 2×/day |
| 3 | **OSL Indo** | `osl.com/en-id/announcement` 列表页 + 详情页 | httpx | 1×/day |
| 4 | **Mobee** | `mobee.com/en/mobee-academy/market-update` | httpx (HTML scrape) | 1×/day |
| 5 | **Bitget ID** | `bitget.com/id/news` 列表页 | Playwright (SPA) | 1×/day |
| 6 | **OKX ID** | `okx.com/id/help/announcements` | Playwright (SPA) | 1×/day |
| - | **Pintu** | 已完成 | - | - |

## 公共模式

所有脚本共享以下模块函数（与 pintu_scraper 一致）：
- `get_cat_id()` - 从环境变量 `{NAME}_CATEGORY_NAME` 获取分类，默认 "news"
- `get_random_bot()` - 随机选取机器人作者
- `sync_tags(post_id, tags)` - 标签入库
- `filter_new_only(items, cat_id)` - 按标题去重
- `strip_html()`, `_e()`, `truncate()` - HTML 工具

---

## 任务清单

### Task 1: Tokocrypto Scraper (API 驱动，最优先)

**Files:**
- Create: `tokocrypto_scraper/__init__.py`
- Create: `tokocrypto_scraper/tokocrypto_scraper.py`

**数据流:**
1. GET `v1/activity-menus?sourceSeat=1` → 获取活动列表 (JSON)
2. 对每个活动 GET `/en/campaign/{id}` → 抓取详情 HTML
3. 解析 HTML 中的活动标题、描述、时间

**API 响应格式 (已知):**
```json
{"code":0, "data":{"list":[{"id":229,"name":"...","description":"...","redirectAddress":"/en/campaign/160",...}]}}
```

- [ ] 创建 `tokocrypto_scraper/tokocrypto_scraper.py`，包含：
  - `fetch_activity_list()` - 调用 API 获取活动列表
  - `fetch_campaign_detail(campaign_id)` - 抓取详情页内容
  - `build_post_html(item)` - 构建 HTML 帖子（印尼风格卡片）
  - `run(save, max_items)` - 主流程

- [ ] 本地测试：`python tokocrypto_scraper/tokocrypto_scraper.py --max 5`

- [ ] 添加到 `scheduler.yml`：新增 `tokocrypto` job，env 含 `DATABASE_URL`

### Task 2: Indodax Scraper (WordPress API)

**Files:**
- Create: `indodax_scraper/__init__.py`
- Create: `indodax_scraper/indodax_scraper.py`

**数据流:**
1. GET `blog.indodax.com/wp-json/wp/v2/categories?per_page=100` → 分类映射
2. GET `blog.indodax.com/wp-json/wp/v2/posts?per_page=30&page=1&_embed=true` → 文章列表

**模式与 pintu_scraper 几乎完全相同**（均使用 WP REST API）。

- [ ] 创建 `indodax_scraper/indodax_scraper.py`
  - WP_API_POSTS = `https://blog.indodax.com/wp-json/wp/v2/posts`
  - 其余逻辑与 pintu_scraper 完全一致（复用相同函数签名）

- [ ] 本地测试

- [ ] 添加到 `scheduler.yml`

### Task 3: OSL Indo Scraper (HTML Parse)

**Files:**
- Create: `osl_scraper/__init__.py`
- Create: `osl_scraper/osl_scraper.py`

**数据流:**
1. GET `osl.com/en-id/announcement` → 解析列表页 HTML（提取链接和标题）
2. 对每个公告 GET `/en-id/announcement/{slug}` → 解析详情页

**OSL 使用 Contentful CMS** (CDN: `images.ctfassets.net`)，详情页为服务端渲染 HTML。

- [ ] 创建 `osl_scraper/osl_scraper.py`
  - `fetch_announcement_list()` - 正则提取 `<a href="/en-id/announcement/...">` 和日期
  - `fetch_detail(url)` - 抓取详情页，提取标题和正文

- [ ] 添加到 `scheduler.yml`

### Task 4: Mobee Scraper (Webflow HTML)

**Files:**
- Create: `mobee_scraper/__init__.py`
- Create: `mobee_scraper/mobee_scraper.py`

**数据流:**
1. GET `mobee.com/en/mobee-academy/market-update` → Webflow 渲染页面
2. 解析新闻列表（标题、链接、日期、摘要）

**Mobee 使用 Webflow**，页面包含结构化 HTML。

- [ ] 创建 `mobee_scraper/mobee_scraper.py`
  - 解析 `.market-update` 区域中的文章卡片
  - Webflow 文章 URL 格式：`/en/mobee-academy/market-update/{slug}`

- [ ] 添加到 `scheduler.yml`

### Task 5: Bitget ID Scraper (Playwright SPA)

**Files:**
- Create: `bitget_scraper/__init__.py`
- Create: `bitget_scraper/bitget_scraper.py`

**数据流:**
1. Playwright 打开 `bitget.com/id/news`，等待 JS 渲染
2. 解析新闻列表（标题、链接、日期、摘要）
3. 抓取详情页获取完整内容

**使用 Playwright**（Bitget 为 SPA，httpx 无法直接获取动态内容）。

- [ ] 创建 `bitget_scraper/bitget_scraper.py`
  - 使用 Playwright 加载页面，等待内容渲染
  - 解析文章标题和链接
  - 对每篇新闻抓取详情页

- [ ] 添加到 `scheduler.yml`（需要 `playwright install chromium`）

### Task 6: OKX ID Scraper (Playwright SPA)

**Files:**
- Create: `okx_scraper/__init__.py`
- Create: `okx_scraper/okx_scraper.py`

**数据流:**
1. Playwright 打开 `okx.com/id/help/announcements`，等待 JS 渲染
2. 解析公告列表
3. 抓取详情页

**使用 Playwright**（OKX 为 SPA）。

- [ ] 创建 `okx_scraper/okx_scraper.py`

- [ ] 添加到 `scheduler.yml`

### Task 7: 更新调度器和环境配置

- [ ] `scheduler.yml` - 为所有 6 个新 scraper 添加 job
- [ ] `.env.example` - 添加环境变量注释
- [ ] 提交推送
