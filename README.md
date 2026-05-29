# Crypto Price Collector

定时从 [CoinCap API 3.0](https://docs.coincap.io/) 采集全部加密货币价格，upsert 到 Supabase `public.tokens` 表。

## 架构

```
CoinCap API  ──fetch──>  GitHub Actions (cron 定时)  ──upsert──>  Supabase tokens 表
```

## 字段映射

| CoinCap API 字段 | tokens 表字段 | 说明 |
|---|---|---|
| `id` | `name` / `coincap_id` | 唯一标识，如 `bitcoin` |
| `name` | `full_name` | 全名，如 `Bitcoin` |
| `symbol` | `symbol` | 代币符号，如 `BTC` |
| `priceUsd` | `price` | 美元价格 |
| `changePercent24Hr` | `change_24h` | 24h 涨跌幅 |
| `marketCapUsd` | `market_cap` | 市值 |
| `volumeUsd24Hr` | `volume_24h` | 24h 交易量 |

> `posts_count`、`emoji` 等非价格字段在 upsert 时不会被覆盖，保留原有值。

## 快速部署

### 1. 获取 CoinCap API Key（免费）

访问 [coincap.io/api-key](https://coincap.io/api-key) → 点击 **Request API Key** → 复制密钥。

免费版限额：500 次/分钟，足够每 30 分钟采集一次。

### 2. 获取 Supabase 密钥

Supabase 控制台 → **Settings** → **API**：

- `Project URL` → `SUPABASE_URL`
- `service_role` key → `SUPABASE_SERVICE_ROLE_KEY`

### 3. GitHub 配置

将项目推送到 GitHub，在仓库 **Settings** → **Secrets and variables** → **Actions** 添加：

| Secret 名 | 值 |
|---|---|
| `COINCAP_API_KEY` | `your-coincap-api-key` |
| `SUPABASE_URL` | `https://xxxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | `your-service-role-key` |

### 4. 触发方式

- **定时自动**: 每 30 分钟（修改 `scheduler.yml` 中 cron 可调整）
- **手动触发**: GitHub → **Actions** → **Crypto Price Collector** → **Run workflow**

## 本地运行

```bash
pip install -r requirements.txt
set COINCAP_API_KEY=your-coincap-api-key
set SUPABASE_URL=https://xxxxx.supabase.co
set SUPABASE_SERVICE_ROLE_KEY=your-key
python main.py
```