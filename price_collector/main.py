import os
import sys
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 直连数据库（绕过 REST API 作业限制）
from db_direct import batch_upsert

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

COINCAP_API_URL = "https://rest.coincap.io/v3/assets"
BATCH_SIZE = 200

COINCAP_API_KEY = os.environ.get("COINCAP_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_DB_PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD")

_missing = []
if not COINCAP_API_KEY:
    _missing.append("COINCAP_API_KEY")
if not SUPABASE_URL:
    _missing.append("SUPABASE_URL")
if not SUPABASE_DB_PASSWORD:
    _missing.append("SUPABASE_DB_PASSWORD")
if _missing:
    logger.error(f"缺少环境变量: {', '.join(_missing)}，跳过采集")
    sys.exit(0)


def fetch_all_assets() -> list[dict]:
    logger.info("开始从 CoinCap API 获取数据...")
    response = httpx.get(
        COINCAP_API_URL,
        params={"limit": 2000},
        headers={"Authorization": f"Bearer {COINCAP_API_KEY}"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    assets = data.get("data", [])
    logger.info(f"成功获取 {len(assets)} 个币种数据")
    return assets


def transform_asset(asset: dict) -> dict:
    return {
        "coincap_id": asset.get("id"),
        "name": asset.get("id"),
        "full_name": asset.get("name"),
        "symbol": asset.get("symbol"),
        "price": _parse_numeric(asset.get("priceUsd")),
        "change_24h": _parse_numeric(asset.get("changePercent24Hr")),
        "market_cap": _parse_numeric(asset.get("marketCapUsd")),
        "volume_24h": _parse_numeric(asset.get("volumeUsd24Hr")),
    }


def _parse_numeric(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def batch_upsert_tokens(rows: list[dict]) -> None:
    """批量 upsert tokens 表"""
    total = len(rows)
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            batch_upsert("tokens", batch, "coincap_id")
            logger.info(
                f"批次 {i // BATCH_SIZE + 1}/{(total + BATCH_SIZE - 1) // BATCH_SIZE}: "
                f"处理 {len(batch)} 条, 进度 {min(i + BATCH_SIZE, total)}/{total}"
            )
        except Exception as e:
            logger.error(f"批次 {i // BATCH_SIZE + 1} 处理失败: {e}")


def main():
    logger.info("=== 加密货币价格采集任务启动 ===")

    assets = fetch_all_assets()

    rows = [transform_asset(asset) for asset in assets]

    batch_upsert_tokens(rows)

    logger.info(f"=== 采集完成, 共更新 {len(rows)} 个币种 ===")


if __name__ == "__main__":
    main()
