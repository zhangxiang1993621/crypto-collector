"""Telegram 群消息汇总 — 主入口

使用 Telethon 以用户身份监听已加入的群组消息，无需机器人。

首次运行需手机号验证登录，生成的 session 文件会保存在本地，
后续运行自动复用，无需重复登录。

用法：
    python main.py
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

import db
import bot
import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def check_config() -> None:
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        if not os.environ.get(key, "").strip():
            logger.error(f"❌ {key} 未配置，请在 .env 中设置")
            sys.exit(1)

    if not os.environ.get("REPORT_CHANNEL_ID", "").strip():
        logger.warning("⚠ REPORT_CHANNEL_ID 未配置，报告将无法发送")


async def main():
    check_config()

    db.init_db()
    logger.info("数据库已初始化")

    # 连接 Telethon（首次运行会提示输入验证码）
    client = await bot.get_client()

    # 注册消息监听
    await bot.start_listener(client)

    # 启动定时任务
    sched = scheduler.start_scheduler(client)

    logger.info("✅ Telegram 群消息汇总运行中...")
    logger.info("   按 Ctrl+C 停止")

    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        logger.info("收到退出信号...")
    finally:
        await bot.disconnect_client(client)
        sched.shutdown(wait=False)
        logger.info("已停止")


if __name__ == "__main__":
    asyncio.run(main())
