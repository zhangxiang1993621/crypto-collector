"""美股数据采集

功能：通过 CloakBrowser 反检测浏览器访问 Yahoo Finance chart API，
      采集美股主要指数和重要个股最近一个交易日的 1 分钟 OHLCV 数据并入库 Supabase，
      支持检测并补全缺失的历史交易日数据。

用法：
    python us_stock_scraper/us_stock_scraper.py                     # 仅采集打印
    python us_stock_scraper/us_stock_scraper.py --upload            # 采集并上传 Supabase
    python us_stock_scraper/us_stock_scraper.py --upload --backfill 5  # 上传 + 补最近 5 天缺失数据
"""

import os
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 子进程执行时需要项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from dotenv import load_dotenv
# 直连数据库（绕过 REST API 作业限制）
from db_direct import batch_upsert, execute_sql
from cloakbrowser import launch

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ────────────────────── 指数列表 ──────────────────────
INDICES = [
    {"symbol": "^GSPC", "name": "S&P 500", "color": "#1f77b4"},
    {"symbol": "^IXIC", "name": "NASDAQ 综合指数", "color": "#ff7f0e"},
    {"symbol": "^DJI", "name": "道琼斯工业指数", "color": "#2ca02c"},
    {"symbol": "^RUT", "name": "罗素2000 小盘股", "color": "#d62728"},
    {"symbol": "^VIX", "name": "VIX 恐慌指数", "color": "#9467bd"},
]

# ────────────────────── 重要个股列表 ──────────────────────
STOCKS = [
    # 科技七巨头
    {"symbol": "AAPL", "name": "Apple 苹果", "color": "#555555"},
    {"symbol": "MSFT", "name": "Microsoft 微软", "color": "#00a4ef"},
    {"symbol": "GOOGL", "name": "Alphabet (Google)", "color": "#4285f4"},
    {"symbol": "AMZN", "name": "Amazon 亚马逊", "color": "#ff9900"},
    {"symbol": "NVDA", "name": "NVIDIA 英伟达", "color": "#76b900"},
    {"symbol": "TSLA", "name": "Tesla 特斯拉", "color": "#cc0000"},
    {"symbol": "META", "name": "Meta 元宇宙", "color": "#0668e1"},
    # 金融 / 消费
    {"symbol": "BRK-B", "name": "Berkshire Hathaway", "color": "#4b2e83"},
    {"symbol": "JPM", "name": "摩根大通", "color": "#0f1a3e"},
    {"symbol": "V", "name": "Visa", "color": "#1a1f71"},
    {"symbol": "WMT", "name": "沃尔玛", "color": "#0071dc"},
    {"symbol": "JNJ", "name": "强生", "color": "#eb1700"},
    {"symbol": "XOM", "name": "埃克森美孚", "color": "#1c3d6e"},
    {"symbol": "UNH", "name": "联合健康", "color": "#005eab"},
    {"symbol": "COST", "name": "Costco 好市多", "color": "#e31837"},
    # 热门行业代表
    {"symbol": "AMD", "name": "AMD 超威半导体", "color": "#ed1c24"},
    {"symbol": "NFLX", "name": "Netflix 奈飞", "color": "#e50914"},
    {"symbol": "BA", "name": "波音 Boeing", "color": "#003399"},
    {"symbol": "DIS", "name": "迪士尼 Disney", "color": "#113ccf"},
    {"symbol": "NKE", "name": "耐克 Nike", "color": "#f5a623"},
]

# Yahoo Finance chart API
YF_CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# ────────────────────── 数据获取 ──────────────────────

def fetch_single(browser_page, symbol: str) -> dict:
    """通过 CloakBrowser 从 Yahoo Finance chart API 获取最近1天的1分钟OHLCV"""
    end_ts = int(time.time())
    # 取最近 2 天数据确保覆盖整个前一交易日（含盘前盘后）
    start_ts = end_ts - 2 * 24 * 3600

    url = YF_CHART_API.format(symbol=symbol)
    url += f"?period1={start_ts}&period2={end_ts}&interval=1m"

    try:
        browser_page.goto(url, timeout=20000)
        # 使用 evaluate + fetch 获取原始 JSON，绕过浏览器 JSON viewer 的 <pre> 渲染问题
        raw = browser_page.evaluate(f"""
            async () => {{
                const resp = await fetch('{url}');
                if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
                return await resp.json();
            }}
        """)

        result_list = raw.get("chart", {}).get("result", [])
        if not result_list:
            return {"symbol": symbol, "data": None, "error": "API 返回空数据"}

        ohlcv = result_list[0]
        timestamps = ohlcv.get("timestamp", [])
        quotes = ohlcv.get("indicators", {}).get("quote", [{}])[0]

        opens = quotes.get("open", [])
        highs = quotes.get("high", [])
        lows = quotes.get("low", [])
        closes = quotes.get("close", [])
        volumes = quotes.get("volume", [])

        if not timestamps or not closes:
            return {"symbol": symbol, "data": None, "error": "无 OHLCV 数据"}

        df = pd.DataFrame({
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        }, index=pd.to_datetime(timestamps, unit="s", utc=True))

        df = df.dropna(subset=["Close"])
        if df.empty:
            return {"symbol": symbol, "data": None, "error": "有效数据为空"}

        # 仅保留最近一个交易日的数据（约 390 分钟 = 6.5h 常规交易时段）
        latest_date = df.index[-1].date()
        df = df[df.index.date == latest_date]

        # 如果当天数据太少（盘前），取前一交易日
        if len(df) < 30:
            prev_date = df.index[-1].date()
            df_all = df  # 保留引用
            # 回退到原始未过滤数据，取最后有数据的那个交易日
            df_full = pd.DataFrame({
                "Open": opens, "High": highs, "Low": lows,
                "Close": closes, "Volume": volumes,
            }, index=pd.to_datetime(timestamps, unit="s", utc=True)).dropna(subset=["Close"])
            if not df_full.empty:
                last_trade_date = df_full.index[-1].date()
                df = df_full[df_full.index.date == last_trade_date]

        close = df["Close"]
        return {
            "symbol": symbol,
            "data": df,
            "latest_close": float(close.iloc[-1]),
            "latest_time": df.index[-1].strftime("%Y-%m-%d %H:%M"),
            "change_pct": float((close.iloc[-1] / close.iloc[0] - 1) * 100) if len(df) >= 2 else 0,
        }
    except Exception as e:
        return {"symbol": symbol, "data": None, "error": str(e)}


def collect_symbols(browser_page, items: list[dict]) -> list[dict]:
    """逐个获取一组标的的分钟级数据"""
    results = []
    total = len(items)
    for i, item in enumerate(items):
        sym = item["symbol"]
        logger.info(f"  获取 {sym}  {item.get('name', '')}  ({i + 1}/{total})")
        result = fetch_single(browser_page, sym)
        result["name"] = item["name"]
        result["color"] = item["color"]
        results.append(result)

        if i < total - 1:
            time.sleep(0.8)

    return results


# ────────────────────── 数据库入库（直连 PostgreSQL）──────────────────────

def upload_bars(data_list: list[dict]) -> int:
    """批量 upsert 分钟 K 线到 us_stock_bars 表"""
    all_rows = []
    for d in data_list:
        df = d.get("data")
        if df is None or df.empty:
            continue

        symbol = d["symbol"]
        name = d.get("name", "")
        item_type = "index" if symbol.startswith("^") else "stock"

        for idx, row in df.iterrows():
            all_rows.append({
                "symbol": symbol,
                "name": name,
                "type": item_type,
                "bar_time": idx.isoformat(),
                "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                "high": float(row["High"]) if pd.notna(row["High"]) else None,
                "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
            })

    total = len(all_rows)
    if total == 0:
        logger.info("  无 K 线数据需要入库")
        return 0

    try:
        batch_upsert("us_stock_bars", all_rows, "symbol,bar_time")
        logger.info(f"  us_stock_bars: upsert {total} 条分钟K线")
        return total
    except Exception as e:
        logger.error(f"  bars 入库失败: {e}")
        return 0


def upload_trends(data_list: list[dict]) -> int:
    """upsert 每日趋势汇总到 us_stock_trends 表"""
    rows = []
    for d in data_list:
        df = d.get("data")
        if df is None or df.empty:
            continue

        close = df["Close"]
        rows.append({
            "symbol": d["symbol"],
            "name": d.get("name", ""),
            "type": "index" if d["symbol"].startswith("^") else "stock",
            "color": d.get("color", ""),
            "latest_close": round(float(close.iloc[-1]), 2),
            "latest_time": df.index[-1].isoformat(),
            "intraday_open": round(float(df["Open"].iloc[0]), 2),
            "intraday_high": round(float(df["High"].max()), 2),
            "intraday_low": round(float(df["Low"].min()), 2),
            "change_pct": round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2) if len(df) >= 2 else 0,
            "volume_total": int(df["Volume"].sum()),
            "bars_count": len(df),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    total = len(rows)
    if total == 0:
        return 0

    try:
        batch_upsert("us_stock_trends", rows, "symbol")
        logger.info(f"  us_stock_trends: upsert {total} 条趋势汇总")
        return total
    except Exception as e:
        logger.error(f"  trends 入库失败: {e}")
        return 0


def seed_symbols() -> int:
    """种子数据：将 INDICES + STOCKS 元信息写入 stock_symbols 表（幂等 upsert）"""
    rows = []
    for item in INDICES:
        rows.append({
            "symbol": item["symbol"],
            "name": item["name"],
            "market": "US",
            "category": "index",
            "color": item.get("color", ""),
            "enabled": True,
        })
    for item in STOCKS:
        rows.append({
            "symbol": item["symbol"],
            "name": item["name"],
            "market": "US",
            "category": "stock",
            "color": item.get("color", ""),
            "enabled": True,
        })

    try:
        batch_upsert("stock_symbols", rows, "symbol")
        logger.info(f"  stock_symbols: upsert {len(rows)} 条标的元信息")
        return len(rows)
    except Exception as e:
        logger.error(f"  stock_symbols 入库失败: {e}")
        return 0


def upload_to_supabase(data_list: list[dict]) -> dict:
    """将所有数据上传到 Supabase，返回上传统计"""
    # db_direct 自动通过 DATABASE_URL 或 SUPABASE_URL + SUPABASE_DB_PASSWORD 连接
    if not os.environ.get("DATABASE_URL") and not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_DB_PASSWORD")):
        logger.warning("缺少 DATABASE_URL 或 SUPABASE_URL + SUPABASE_DB_PASSWORD 环境变量，跳过入库")
        return {"symbols": 0, "bars": 0, "trends": 0}

    logger.info("上传数据到 Supabase...")
    symbols_count = seed_symbols()
    bars_count = upload_bars(data_list)
    trends_count = upload_trends(data_list)
    return {"symbols": symbols_count, "bars": bars_count, "trends": trends_count}


# ────────────────────── 数据补缺 ──────────────────────


def _get_trading_days(days_back: int) -> list[str]:
    """生成过去 N 个美股交易日列表（跳过周末）

    Args:
        days_back: 往回看的交易日数量（如 5 表示最近 5 个交易日）

    Returns:
        ["2026-06-12", "2026-06-11", ...] 按日期倒序
    """
    trading_days: list[str] = []
    today = datetime.now(timezone.utc).date()
    cursor = today
    while len(trading_days) < days_back:
        if cursor.weekday() < 5:  # 周一到周五
            trading_days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return trading_days


def detect_gaps(lookback_days: int) -> dict[str, list[str]]:
    """检测每个标的在最近 N 个交易日中的缺失日期"""
    trading_days = _get_trading_days(lookback_days)
    if not trading_days:
        return {}

    earliest = trading_days[-1]
    logger.info(f"检测 {lookback_days} 个交易日 ({earliest} ~ {trading_days[0]}) 的数据缺失情况...")

    all_symbols = [s["symbol"] for s in INDICES] + [s["symbol"] for s in STOCKS]
    gaps: dict[str, list[str]] = {}

    for sym in all_symbols:
        gaps[sym] = _detect_gaps_for_symbol(sym, trading_days)

    return gaps


def _detect_gaps_for_symbol(symbol: str, trading_days: list[str]) -> list[str]:
    """逐日查询 us_stock_bars 是否有数据（直连 PostgreSQL）"""
    missing: list[str] = []
    for day in trading_days:
        sql = '''
            SELECT COUNT(*) as cnt FROM us_stock_bars 
            WHERE symbol = %s AND bar_time >= %s AND bar_time < %s
        '''
        day_start = f"{day}T00:00:00+00:00"
        day_end = f"{day}T23:59:59+00:00"
        rows = execute_sql(sql, (symbol, day_start, day_end))
        if not rows or rows[0].get("cnt", 0) == 0:
            missing.append(day)
    return missing


def _fetch_for_date(browser_page, symbol: str, target_date: str) -> dict:
    """从 Yahoo Finance 拉取指定交易日的美股 1 分钟 K 线

    Args:
        browser_page: CloakBrowser / Playwright 页面对象
        symbol: 标的代码
        target_date: "2026-06-12" 格式

    Returns:
        与 fetch_single 相同结构的 dict
    """
    try:
        dt_target = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return {"symbol": symbol, "data": None, "error": f"日期格式错误: {target_date}"}

    start_ts = int(dt_target.timestamp())
    end_ts = int((dt_target + timedelta(hours=30)).timestamp())  # 多给 6 小时 buffer

    url = YF_CHART_API.format(symbol=symbol)
    url += f"?period1={start_ts}&period2={end_ts}&interval=1m"

    try:
        browser_page.goto(url, timeout=20000)
        raw = browser_page.evaluate(f"""
            async () => {{
                const resp = await fetch('{url}');
                if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
                return await resp.json();
            }}
        """)

        result_list = raw.get("chart", {}).get("result", [])
        if not result_list:
            return {"symbol": symbol, "data": None, "error": "API 返回空数据"}

        ohlcv = result_list[0]
        timestamps = ohlcv.get("timestamp", [])
        quotes = ohlcv.get("indicators", {}).get("quote", [{}])[0]

        opens = quotes.get("open", [])
        highs = quotes.get("high", [])
        lows = quotes.get("low", [])
        closes = quotes.get("close", [])
        volumes = quotes.get("volume", [])

        if not timestamps or not closes:
            return {"symbol": symbol, "data": None, "error": "无 OHLCV 数据"}

        df = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows,
            "Close": closes, "Volume": volumes,
        }, index=pd.to_datetime(timestamps, unit="s", utc=True))

        df = df.dropna(subset=["Close"])
        if df.empty:
            return {"symbol": symbol, "data": None, "error": "有效数据为空"}

        # 仅保留目标交易日的数据
        df = df[df.index.date == dt_target.date()]
        if df.empty:
            return {"symbol": symbol, "data": None, "error": f"{target_date} 无交易数据"}

        close = df["Close"]
        return {
            "symbol": symbol,
            "data": df,
            "latest_close": float(close.iloc[-1]),
            "latest_time": df.index[-1].strftime("%Y-%m-%d %H:%M"),
            "change_pct": float((close.iloc[-1] / close.iloc[0] - 1) * 100) if len(df) >= 2 else 0,
        }
    except Exception as e:
        return {"symbol": symbol, "data": None, "error": str(e)}


def fill_gaps(browser_page, lookback_days: int = 5) -> dict:
    """补缺主流程：检测缺失 → 拉取历史数据 → 入库"""
    logger.info(f"\n=== 数据补缺检查 (最近 {lookback_days} 个交易日) ===")

    # Step 1: 检测缺失
    gaps = detect_gaps(lookback_days)

    total_missing = sum(len(days) for days in gaps.values())
    if total_missing == 0:
        logger.info("所有标的数据完整，无需补缺 ✓")
        return {"filled_days": 0, "filled_bars": 0, "symbols_fixed": 0, "missing": 0}

    logger.info(f"发现 {len(gaps)} 个标的存在数据缺失，共 {total_missing} 个交易日需要补全")

    # Step 2: 拉取并入库
    all_items = INDICES + STOCKS
    symbol_name = {s["symbol"]: s["name"] for s in all_items}
    symbol_type = {s["symbol"]: "index" if s["symbol"].startswith("^") else "stock" for s in all_items}

    filled_bars = 0
    filled_days = 0
    symbols_fixed = 0
    skipped = 0

    for sym, missing_dates in gaps.items():
        name = symbol_name.get(sym, sym)
        item_type = symbol_type.get(sym, "stock")

        for day in missing_dates:
            logger.info(f"  补缺 {sym} ({name}) → {day}")
            result = _fetch_for_date(browser_page, sym, day)

            df = result.get("data")
            if df is None or df.empty:
                logger.warning(f"    {sym} {day}: 无数据可补 ({result.get('error', '')})")
                skipped += 1
                continue

            # 组装 rows 并 upsert
            rows = []
            for idx, row in df.iterrows():
                rows.append({
                    "symbol": sym,
                    "name": name,
                    "type": item_type,
                    "bar_time": idx.isoformat(),
                    "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                    "high": float(row["High"]) if pd.notna(row["High"]) else None,
                    "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                    "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                    "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
                })

            # 直接 upsert 全部行
            try:
                upserted = batch_upsert("us_stock_bars", rows, "symbol,bar_time")
            except Exception as e:
                logger.error(f"    补缺入库失败 [{sym} {day}]: {e}")
                skipped += 1
                continue

            filled_bars += upserted
            filled_days += 1
            symbols_fixed += 1
            logger.info(f"    {sym} {day}: 补入 {upserted} 条 K 线 ✓")

            time.sleep(1.2)  # 控制请求频率

    # Step 3: 汇总
    logger.info(
        f"\n补缺完成: 修复 {symbols_fixed} 个标的, "
        f"填补 {filled_days} 个交易日, "
        f"入库 {filled_bars} 条 K 线, "
        f"跳过 {skipped} 天（无数据）"
    )
    return {
        "filled_days": filled_days,
        "filled_bars": filled_bars,
        "symbols_fixed": symbols_fixed,
        "missing": skipped,
    }


# ────────────────────── 控制台输出 ──────────────────────

def print_summary(data_list: list[dict], category: str) -> None:
    """控制台打印汇总"""
    print(f"\n{'=' * 80}")
    print(f"  {category}   (1分钟K线)")
    print(f"{'=' * 80}")
    for d in data_list:
        info = d.get("data")
        if info is None or info.empty:
            symbol = d.get("symbol", "")
            print(f"  [{symbol}] {d.get('name', '')}  - 无数据 ({d.get('error', '')})")
            continue
        change = d.get("change_pct", 0)
        arrow = "+" if change > 0 else ("-" if change < 0 else "=")
        n_bars = len(info)
        print(
            f"  {arrow} {d['symbol']:<8s} {d.get('name', ''):<25s}  "
            f"收盘 {d.get('latest_close', 0):>10.2f}  "
            f"日内 {change:>+7.2f}%  "
            f"K线 {n_bars}根  "
            f"截止 {d.get('latest_time', '')}"
        )


# ────────────────────── 主流程 ──────────────────────

def run(upload: bool = False, backfill_days: int = 0) -> None:
    logger.info("=== 美股分钟级数据采集 ===")

    all_data: list[dict] = []

    logger.info("启动 CloakBrowser 反检测浏览器...")
    browser = launch(headless=True)
    page = browser.new_page()

    try:
        # ── 第一部份：采集主要指数 ──
        logger.info("--- 采集主要指数 (5 个，1分钟K线) ---")
        index_data = collect_symbols(page, INDICES)
        print_summary(index_data, "主要指数")
        all_data.extend(index_data)

        # ── 第二部份：采集重要个股 ──
        logger.info("\n--- 采集重要个股 (20 个，1分钟K线) ---")
        stock_data = collect_symbols(page, STOCKS)
        print_summary(stock_data, "重要个股")
        all_data.extend(stock_data)

        # ── 第三部份：上传 Supabase ──
        if upload:
            result = upload_to_supabase(all_data)
            logger.info(f"  入库完成: symbols={result['symbols']}, bars={result['bars']}, trends={result['trends']}")

    finally:
        browser.close()

    # ── 第四部份：数据补缺（检测缺失的交易日并回填）──
    if backfill_days > 0 and upload:
        # 独立打开浏览器做补缺请求
        bf_browser = launch(headless=True)
        bf_page = bf_browser.new_page()
        try:
            fill_gaps(bf_page, lookback_days=backfill_days)
        finally:
            bf_browser.close()

    total_ok = sum(1 for d in all_data if d.get("data") is not None)
    logger.info(f"\n=== 完成 === 有效数据 {total_ok}/{len(all_data)} 条")


def main():
    parser = argparse.ArgumentParser(description="美股分钟级数据采集")
    parser.add_argument("--upload", action="store_true", help="上传数据到 Supabase (us_stock_bars + us_stock_trends)")
    parser.add_argument(
        "--backfill", type=int, default=0, metavar="N",
        help="检测并补全最近 N 个交易日缺失的 K 线数据（需配合 --upload）",
    )
    args = parser.parse_args()
    run(upload=args.upload, backfill_days=args.backfill)


if __name__ == "__main__":
    main()
