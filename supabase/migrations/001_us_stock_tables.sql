-- ============================================================
-- 股票数据采集表
-- 用途：存储美股/A股指数和个股的分钟级 K 线数据 + 每日趋势汇总 + 标的元信息
-- ============================================================

-- 1. 标的元信息表（股票/指数的公共信息）
CREATE TABLE IF NOT EXISTS stock_symbols (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT        NOT NULL UNIQUE,    -- 代码 (AAPL, ^GSPC, 600519.SS...)
    name        TEXT,                           -- 名称 (Apple 苹果, S&P 500...)
    market      TEXT        NOT NULL,           -- 市场: 'US' 美股 | 'CN' A股
    category    TEXT        NOT NULL,           -- 类别: 'stock' 个股 | 'index' 指数
    color       TEXT,                           -- 图表配色 (hex)
    enabled     BOOLEAN     NOT NULL DEFAULT true,  -- 是否启用采集
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_symbols_market   ON stock_symbols (market);
CREATE INDEX IF NOT EXISTS idx_symbols_category ON stock_symbols (category);

-- 2. 分钟级 K 线原始数据表
CREATE TABLE IF NOT EXISTS us_stock_bars (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT        NOT NULL,          -- 股票/指数代码 (AAPL, ^GSPC...)
    name        TEXT,                          -- 名称 (Apple 苹果, S&P 500...)
    type        TEXT        NOT NULL,          -- 'index' | 'stock'
    bar_time    TIMESTAMPTZ NOT NULL,          -- 该根 K 线的时间点 (含时区)
    open        DOUBLE PRECISION,              -- 开盘价
    high        DOUBLE PRECISION,              -- 最高价
    low         DOUBLE PRECISION,              -- 最低价
    close       DOUBLE PRECISION,              -- 收盘价
    volume      BIGINT,                        -- 成交量
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 采集入库时间

    -- 同一 symbol + bar_time 唯一，用于 upsert（同一天多次采集幂等）
    UNIQUE (symbol, bar_time)
);

-- 索引加速查询
CREATE INDEX IF NOT EXISTS idx_bars_symbol ON us_stock_bars (symbol);
CREATE INDEX IF NOT EXISTS idx_bars_time   ON us_stock_bars (bar_time DESC);
CREATE INDEX IF NOT EXISTS idx_bars_type   ON us_stock_bars (type);

-- ============================================================

-- 3. 每日趋势汇总表
CREATE TABLE IF NOT EXISTS us_stock_trends (
    id            BIGSERIAL PRIMARY KEY,
    symbol        TEXT        NOT NULL UNIQUE,  -- 股票/指数代码 (唯一)
    name          TEXT,                         -- 名称
    type          TEXT        NOT NULL,         -- 'index' | 'stock'
    color         TEXT,                         -- 图表配色
    latest_close  DOUBLE PRECISION,             -- 最新收盘价
    latest_time   TIMESTAMPTZ,                  -- 最后一条 K 线时间
    intraday_open DOUBLE PRECISION,             -- 当日开盘价
    intraday_high DOUBLE PRECISION,             -- 当日最高价
    intraday_low  DOUBLE PRECISION,             -- 当日最低价
    change_pct    DOUBLE PRECISION,             -- 日内涨跌幅 (%)
    volume_total  BIGINT,                       -- 当日总成交量
    bars_count    INT,                          -- 采集的 K 线数量
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()  -- 最后更新时间
);

CREATE INDEX IF NOT EXISTS idx_trends_type ON us_stock_trends (type);

-- ============================================================
-- RLS: 允许 service_role 全权限（项目所有脚本使用 service_role key）
-- ============================================================
ALTER TABLE stock_symbols   ENABLE ROW LEVEL SECURITY;
ALTER TABLE us_stock_bars   ENABLE ROW LEVEL SECURITY;
ALTER TABLE us_stock_trends ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_full_access" ON stock_symbols   FOR ALL USING (true);
CREATE POLICY "service_role_full_access" ON us_stock_bars   FOR ALL USING (true);
CREATE POLICY "service_role_full_access" ON us_stock_trends FOR ALL USING (true);
