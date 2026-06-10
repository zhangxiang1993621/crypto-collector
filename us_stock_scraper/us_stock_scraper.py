"""美股数据采集 + 分钟K线图生成

功能：通过 CloakBrowser 反检测浏览器访问 Yahoo Finance chart API，
      采集美股主要指数和重要个股最近一个交易日的 1 分钟 OHLCV 数据，
      生成分钟K线图并保存到 output/ 目录，支持上传到 Supabase。

用法：
    python us_stock_scraper/us_stock_scraper.py                  # 仅采集打印
    python us_stock_scraper/us_stock_scraper.py --save           # 采集并保存图表
    python us_stock_scraper/us_stock_scraper.py --save --upload  # 保存图表并上传 Supabase
"""

import os
import sys
import json
import time
import logging
import argparse
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from supabase import create_client, Client
from cloakbrowser import launch

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
BATCH_SIZE = 500  # Supabase upsert 批处理大小

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"

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

# ────────────────────── 通用 K 线配色（美股：红涨绿跌） ──────────────────────
MC = mpf.make_marketcolors(
    up="red", down="green",
    edge="inherit",
    wick="inherit",
    volume="inherit",
)
STYLE = mpf.make_mpf_style(
    marketcolors=MC,
    gridstyle="--",
    y_on_right=False,
    base_mpf_style="charles",
)


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
        content = browser_page.content()

        json_match = re.search(r"<pre>(.*?)</pre>", content, re.DOTALL)
        if json_match:
            raw = json.loads(json_match.group(1))
        else:
            return {"symbol": symbol, "data": None, "error": "响应非 JSON"}

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


# ────────────────────── 图表生成 ──────────────────────

def generate_chart(info: dict, filepath: str) -> bool:
    """生成单个标的的分钟K线图并保存"""
    df = info.get("data")
    if df is None or df.empty:
        return False

    symbol = info.get("symbol", "")
    raw_name = info.get("name", symbol)
    en_name = re.sub(r"[\u4e00-\u9fff]+", "", raw_name).strip() or symbol
    latest_close = info.get("latest_close", 0)
    latest_time = info.get("latest_time", "")
    change = info.get("change_pct", 0)

    title = f"{en_name} ({symbol})  1-min Candles"
    subtitle = f"{latest_time}  Close {latest_close:.2f}  Intraday Change {change:+.2f}%"
    full_title = f"{title}\n{subtitle}"

    try:
        fig, axes = mpf.plot(
            df,
            type="candle",
            style=STYLE,
            title=full_title,
            ylabel="Price (USD)",
            volume=True,
            figsize=(16, 8),
            savefig=filepath,
            returnfig=True,
            datetime_format="%H:%M",
            xrotation=45,
            tight_layout=True,
        )
        plt.close(fig)
        return True
    except Exception as e:
        logger.error(f"  生成K线图失败 [{symbol}]: {e}")
        return False


def generate_index_comparison(index_data: list[dict], filepath: str) -> bool:
    """生成主要指数归一化对比折线图"""
    valid = [d for d in index_data if d.get("data") is not None and not d["data"].empty]
    if len(valid) < 2:
        return False

    try:
        fig, ax = plt.subplots(figsize=(14, 7))
        for d in valid:
            df = d["data"]
            normalized = df["Close"] / df["Close"].iloc[0] * 100
            raw_name = d.get("name", d["symbol"])
            label = re.sub(r"[\u4e00-\u9fff]+", "", raw_name).strip() or d["symbol"]
            ax.plot(df.index, normalized, label=label,
                    linewidth=2, alpha=0.85)

        ax.set_title("Major US Indices - Normalized Comparison (Base=100)", fontsize=15, fontweight="bold")
        ax.set_ylabel("Normalized Price (Base=100)")
        ax.legend(loc="upper left", fontsize=10)
        ax.grid(True, linestyle="--", alpha=0.5)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(filepath, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return True
    except Exception as e:
        logger.error(f"  生成指数对比图失败: {e}")
        return False


def generate_summary_json(data_list: list[dict], filepath: str) -> None:
    """生成当日分钟级汇总 JSON"""
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "items": [],
    }
    for d in data_list:
        df = d.get("data")
        if df is None or df.empty:
            summary["items"].append({
                "symbol": d["symbol"],
                "name": d.get("name", ""),
                "error": d.get("error", "无数据"),
            })
            continue

        close = df["Close"]
        summary["items"].append({
            "symbol": d["symbol"],
            "name": d.get("name", ""),
            "latest_time": df.index[-1].strftime("%Y-%m-%d %H:%M"),
            "latest_close": round(float(close.iloc[-1]), 2),
            "open": round(float(df["Open"].iloc[0]), 2),
            "change_pct": round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2) if len(df) >= 2 else 0,
            "intraday_high": round(float(df["High"].max()), 2),
            "intraday_low": round(float(df["Low"].min()), 2),
            "volume_total": int(df["Volume"].sum()),
            "bars_count": len(df),
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


# ────────────────────── Supabase 入库 ──────────────────────

def get_supabase_client() -> Client | None:
    """获取 Supabase 客户端，环境变量缺失则返回 None"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 环境变量，跳过入库")
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def upload_bars(client: Client, data_list: list[dict]) -> int:
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

    inserted = 0
    for i in range(0, total, BATCH_SIZE):
        batch = all_rows[i:i + BATCH_SIZE]
        try:
            client.table("us_stock_bars").upsert(
                batch, on_conflict="symbol,bar_time"
            ).execute()
            inserted += len(batch)
        except Exception as e:
            logger.error(f"  bars 入库批次失败 [{i}:{i + len(batch)}]: {e}")

    logger.info(f"  us_stock_bars: upsert {inserted}/{total} 条分钟K线")
    return inserted


def upload_trends(client: Client, data_list: list[dict]) -> int:
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
        client.table("us_stock_trends").upsert(
            rows, on_conflict="symbol"
        ).execute()
        logger.info(f"  us_stock_trends: upsert {total} 条趋势汇总")
        return total
    except Exception as e:
        logger.error(f"  trends 入库失败: {e}")
        return 0


def seed_symbols(client: Client) -> int:
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
        client.table("stock_symbols").upsert(
            rows, on_conflict="symbol"
        ).execute()
        logger.info(f"  stock_symbols: upsert {len(rows)} 条标的元信息")
        return len(rows)
    except Exception as e:
        logger.error(f"  stock_symbols 入库失败: {e}")
        return 0


def upload_to_supabase(data_list: list[dict]) -> dict:
    """将所有数据上传到 Supabase，返回上传统计"""
    client = get_supabase_client()
    if client is None:
        return {"symbols": 0, "bars": 0, "trends": 0}

    logger.info("上传数据到 Supabase...")
    symbols_count = seed_symbols(client)
    bars_count = upload_bars(client, data_list)
    trends_count = upload_trends(client, data_list)
    return {"symbols": symbols_count, "bars": bars_count, "trends": trends_count}


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

def run(save: bool = False, upload: bool = False) -> None:
    logger.info("=== 美股分钟级数据采集 + K线图生成 ===")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_data = []
    charts_ok = 0

    logger.info("启动 CloakBrowser 反检测浏览器...")
    browser = launch(headless=True)
    page = browser.new_page()

    try:
        # ── 第一部份：采集主要指数 ──
        logger.info("--- 采集主要指数 (5 个，1分钟K线) ---")
        index_data = collect_symbols(page, INDICES)
        print_summary(index_data, "主要指数")

        if save and any(d.get("data") is not None for d in index_data):
            comp_path = str(OUTPUT_DIR / f"us_indices_compare_{timestamp}.png")
            if generate_index_comparison(index_data, comp_path):
                logger.info(f"  指数对比图: {comp_path}")
                charts_ok += 1

            for d in index_data:
                if d.get("data") is None:
                    continue
                sym_clean = d["symbol"].lstrip("^")
                filepath = str(OUTPUT_DIR / f"us_index_{sym_clean}_{timestamp}.png")
                if generate_chart(d, filepath):
                    charts_ok += 1

        all_data.extend(index_data)

        # ── 第二部份：采集重要个股 ──
        logger.info("\n--- 采集重要个股 (20 个，1分钟K线) ---")
        stock_data = collect_symbols(page, STOCKS)
        print_summary(stock_data, "重要个股")

        if save and any(d.get("data") is not None for d in stock_data):
            for d in stock_data:
                if d.get("data") is None:
                    continue
                filepath = str(OUTPUT_DIR / f"us_stock_{d['symbol']}_{timestamp}.png")
                if generate_chart(d, filepath):
                    charts_ok += 1

        all_data.extend(stock_data)

        # ── 第三部份：保存汇总 JSON ──
        if save:
            json_path = str(OUTPUT_DIR / f"us_stock_summary_{timestamp}.json")
            generate_summary_json(all_data, json_path)
            logger.info(f"  汇总数据: {json_path}")

        # ── 第四部份：上传 Supabase ──
        if upload:
            result = upload_to_supabase(all_data)
            logger.info(f"  入库完成: symbols={result['symbols']}, bars={result['bars']}, trends={result['trends']}")

    finally:
        browser.close()

    total_ok = sum(1 for d in all_data if d.get("data") is not None)
    logger.info(f"\n=== 完成 === 图表 {charts_ok} 张, 有效数据 {total_ok}/{len(all_data)} 条")


def main():
    parser = argparse.ArgumentParser(description="美股分钟级数据采集 + K线图生成")
    parser.add_argument("--save", action="store_true", help="保存图表和 JSON 到 output/ 目录")
    parser.add_argument("--upload", action="store_true", help="上传数据到 Supabase (us_stock_bars + us_stock_trends)")
    args = parser.parse_args()
    run(save=args.save, upload=args.upload)


if __name__ == "__main__":
    main()
