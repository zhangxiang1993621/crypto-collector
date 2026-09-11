# 调度

项目有两条独立的调度通道，二者互不干扰：

| 通道 | 位置 | 触发方式 | 适用场景 |
|---|---|---|---|
| GitHub Actions | `.github/workflows/scheduler.yml` | 云端定时 + 手动 `workflow_dispatch` | 生产环境无人值守 |
| 本地 GUI | `task_manager/`（入口 `run_task_manager.py`） | 本地 APScheduler 定时 + 手动点击 | 开发调试、临时补跑 |

## 1. GitHub Actions

### 1.1 工作流结构

```yaml
name: Crypto Collector
on:
  schedule:
  - cron: '0 0 * * *'   # 当前所有条目相同（见"已知问题"）
  ...
  workflow_dispatch: null

jobs:
  price_collector:
    runs-on: ubuntu-latest
    continue-on-error: true      # 单个 job 失败不影响其他 job
    steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: {python-version: '3.11'}
    - run: pip install -r requirements.txt
    - name: Run
      env:
        DATABASE_URL: ${{ secrets.DATABASE_URL }}
        COINCAP_API_KEY: ${{ secrets.COINCAP_API_KEY }}
      run: python crypto/price_collector.py || echo "[FAIL]"
```

- 每个 job 都是相同的四步：checkout → setup-python 3.11 → `pip install -r requirements.txt` → 执行脚本。
- 所有 job 都设置了 `continue-on-error: true`，失败在 Actions 页面显示为黄色警告而非红色失败。
- 执行命令末尾普遍带 `|| echo "[FAIL]"`，用于在日志里留下可检索的失败标记。
- 运行环境时区为 UTC，cron 按 UTC 解释。

### 1.2 Job 与命令对照

| job | 命令 | 该 job 传入的环境变量 |
|---|---|---|
| `price_collector` | `python crypto/price_collector.py` | `DATABASE_URL`、`COINCAP_API_KEY` |
| `airdrop` | `python crypto/airdrop_scraper.py --save --max 20` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`HOT_TOKENS_CATEGORY_NAME` |
| `indo_news` | `python news_scrapers/indo_news/indo_news_scraper.py --save --max 10` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`INDO_CATEGORY_NAME` |
| `tokocrypto` | `python crypto/tokocrypto/tokocrypto_scraper.py --save --max 10` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME` |
| `indodax` | `python crypto/indodax/indodax_scraper.py --save --max 15` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME` |
| `pintu` | `python crypto/pintu/pintu_scraper.py --save --max 15` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`PINTU_CATEGORY_NAME` |
| `mobee` | `python crypto/mobee/mobee_scraper.py --save --max 10` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME` |
| `detik_sport` | `python news_scrapers/sport_detik/detik_scraper.py --save --max 15` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`FIFA_CATEGORY_NAME` |
| `tribunbola` | `python news_scrapers/tribunbola/tribunbola_scraper.py --save --max 10` | 同上 |
| `dailysports` | `python news_scrapers/dailysports/dailysports_scraper.py --save --max 10` | 同上 |
| `binance_news` | `python news_scrapers/binance/news_scraper.py --scroll 5 --max 50 --save` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`POSTS_CATEGORY_NAME` |
| `fifa_schedule` | `python sport/schedule/fifa_scraper.py --save` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`POSTS_CATEGORY_NAME`、`FIFA_CATEGORY_NAME` |
| `fifa_blog` | `python sport/blog/fifa_blog_scraper.py --save --max 30` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`FIFA_CATEGORY_NAME` |
| `indonesia_esports` | `python esports/esports_scraper.py --save --max 10` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`ESPORTS_CATEGORY_NAME` |
| `goal` | `python sport/goal/goal_scraper.py --save --live` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`FIFA_CATEGORY_NAME` |
| `osl` | `python crypto/osl/osl_scraper.py --save --max 10` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME` |
| `bitget` | `python crypto/bitget/bitget_scraper.py --save --max 10` | 同上 |
| `okx` | `python crypto/okx/okx_scraper.py --save --max 10` | 同上 |
| `bolasport` | `python news_scrapers/bolasport/bolasport_scraper.py --save --max 15` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`FIFA_CATEGORY_NAME` |
| `forebet` | `python sport/forebet/forebet_scraper.py --save` | 同上 |
| `fastscore` | `python sport/fastscore/fastscore_scraper.py --save` | 同上 |
| `footballant` | `python sport/footballant/footballant_scraper.py --save` | 同上 |
| `bwf_indonesia` | `python sport/badminton/bwf_indonesia_scraper.py --save` | 同上 |
| `bwf_calendar` | `python sport/badminton/bwf_calendar_scraper.py --save` | 同上 |
| `us_stock` | `python us_stock_scraper/us_stock_scraper.py --upload --backfill 4` | `DATABASE_URL` |
| `mainbasket` | `python news_scrapers/mainbasket/mainbasket_scraper.py --save --max 15` | `DATABASE_URL`、`POSTS_AUTHOR_USERNAME`、`FIFA_CATEGORY_NAME` |
| `ibl_data` | `python news_scrapers/ibl_data/ibl_data_scraper.py --save` | 同上 |

> `ai_digest` 与 `bot_posts` 目前**没有**对应的 job。

### 1.3 Secrets / Variables

在仓库 Settings → Secrets and variables → Actions 中配置：

**Secrets**

| 名称 | 用途 |
|---|---|
| `DATABASE_URL` | Supabase PostgreSQL 直连串（几乎所有 job 必需） |
| `COINCAP_API_KEY` | CoinCap 价格采集 |

**Variables**

| 名称 | 对应分类 |
|---|---|
| `POSTS_AUTHOR_USERNAME` | 默认发帖账号（`indoAdmin`） |
| `POSTS_CATEGORY_NAME` | 币安新闻 / FIFA 赛程默认分类 |
| `FIFA_CATEGORY_NAME` | 体育类分类 |
| `INDO_CATEGORY_NAME` | 印尼热点分类 |
| `PINTU_CATEGORY_NAME` | Pintu 分类 |
| `HOT_TOKENS_CATEGORY_NAME` | AI 摘要 / 空投分类 |
| `ESPORTS_CATEGORY_NAME` | 电竞分类 |

未在 workflow 中传入 `*_CATEGORY_NAME` 的 job（`tokocrypto`、`indodax`、`mobee`、`osl`、`bitget`、`okx`）会使用脚本内默认分类 **`Hot Tokens`**（见 [operations.md](operations.md) 默认值列），而不是 `.env.example` 里标注的 `news`。

### 1.4 已知问题

1. **schedule 条目与 job 数量不匹配**：`on.schedule` 当前有 23 条 cron（全部为 `0 0 * * *`），而 `jobs` 有 27 个且**均无 `if` 条件**。按 GitHub Actions 语义，每条 schedule 都会触发整个工作流、运行其中所有 job。因此存在"同一天多次触发、每次跑全部 job"的风险（GitHub 对完全相同的 cron 条目是否有去重，建议在 Actions 运行记录中确认）。**建议**：把 `on.schedule` 合并为一条 `- cron: '0 0 * * *'`；若确实需要按 job 区分频率，应为每个 job 增加 `if: github.event.schedule == '<cron>'` 之类的守卫。
2. **cron 已统一为每天一次**：提交 `9a7436d` 将所有爬虫 Cron 统一为 `0 0 * * *`（UTC，即印尼时间 07:00）。根 README 旧版的"每 30 分钟/每 2 小时"已不再适用。

## 2. 本地 GUI 面板

### 2.1 启动与组成

```bash
python run_task_manager.py
```

- 入口：`run_task_manager.py` → `task_manager/gui.py`（tkinter）。
- 调度引擎：`task_manager/scheduler_engine.py` 的 `TaskScheduler`（APScheduler `BackgroundScheduler`，`timezone="UTC"`）。
- 配置文件：`task_config.json`（项目根，**已 gitignore**）。
- 从 YAML 导入：`task_config.json` 不存在时，读取 `scheduler.yml`（若存在 `scheduler.yml.disabled` 则读它）解析 job 生成任务列表。

### 2.2 面板功能

| 功能 | 说明 |
|---|---|
| 任务列表 | 按分类分组（`TASK_CATEGORIES`），显示描述、cron、状态、上次运行、结果、下次运行 |
| 启动/停止调度 | 启停 APScheduler |
| 启用/停用任务 | 修改 `enabled` 并保存到 `task_config.json`；停用会从调度器移除 job |
| 编辑 Cron | 弹窗编辑（含快捷预设），校验失败则拒绝 |
| 立即执行 | 在线程中单次执行任务 |
| 执行分类全部 | 按顺序串行执行某分类下所有任务 |
| 同步到 YAML | `sync_to_yaml()` 把所有**带 cron 的任务**（不区分启用/停用）写回 `scheduler.yml` 的 `on.schedule` |
| 运行日志 | 实时显示子进程输出 |

### 2.3 任务执行细节

- 任务命令在**子进程**中执行（`subprocess.Popen`，`shell=True`，`cwd=项目根`）。
- 命令中的 `python` 会被替换为当前解释器 `sys.executable`，保证子进程与面板使用同一 conda 环境。
- 子进程继承面板环境变量，并叠加任务自身的 `env_vars`；`${{ ... }}` 形式的占位符会被解析为同名环境变量，取空则不下传（让脚本使用自身默认值）。
- 单条命令超时 10 分钟（`process.wait(timeout=600)`），超时判定为失败。
- 无 `cron` 的任务（`trigger: manual`）只记录状态，不加入调度器。

### 2.4 内置手动任务

`scheduler_engine._merge_builtin_tasks()` 会确保以下任务存在（不参与定时）：

| 任务名 | 命令 |
|---|---|
| `create_bots` | `python task_manager/create_bots.py` |
| `clean_all` | `python tools/clean_all.py`（终端需输入 YES；面板会二次确认，分类批量执行时自动跳过） |

### 2.5 配置同步机制

- `load_config()`：优先读 `task_config.json`；不存在则从 YAML 导入；最后合并内置手动任务。
- `save_config()`：把任务列表写入 `task_config.json`。
- `sync_to_yaml()`：把**有 cron 的任务**写回 YAML 的 `on.schedule` 列表，并修正 PyYAML 把 `on` 解析为布尔值的问题（`^true:` → `on:`）。

> ⚠️ 注意：`import_from_yaml()` 按"第 i 个 job 对应第 i 条 cron"解析。当 `schedule` 条目数少于 job 数时，靠后的 job 会拿到默认 `0 0 * * *`。修改 YAML 时需保证两者数量与顺序一致。

### 2.6 分类映射

新增任务若要出现在面板的正确分类下，需同时修改 `scheduler_engine.py` 中的：

- `TASK_CATEGORIES`：把任务名加入对应分类列表。
- `_job_label()`：补充任务的中文描述。

分类顺序由 `CATEGORY_ORDER` 控制（新闻爬虫 / AI 生成 / 电子竞技 / 体育 / 加密市场 / 美股数据 / 管理工具）。当前 "AI 生成" 分类为空。
