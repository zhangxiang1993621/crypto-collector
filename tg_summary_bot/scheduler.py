"""定时任务调度模块"""

import os
import asyncio
import logging
from datetime import datetime

from telethon import TelegramClient

import db
import reporter

logger = logging.getLogger(__name__)


async def run_summary(client: TelegramClient) -> None:
    """遍历所有活跃群组，生成报告并发送到管理频道"""
    hours = int(os.environ.get("SUMMARY_INTERVAL_HOURS", "6"))
    channel_id = os.environ.get("REPORT_CHANNEL_ID", "")

    if not channel_id:
        logger.warning("⚠ REPORT_CHANNEL_ID 未配置，跳过报告生成")
        return

    chats = db.get_active_chats(hours)
    if not chats:
        logger.info("无活跃群组消息，跳过报告生成")
        return

    logger.info(f"开始为 {len(chats)} 个群组生成报告...")

    from bot import send_report_to_channel

    for chat in chats:
        chat_id = chat["chat_id"]
        chat_title = chat["chat_title"]

        report_text = reporter.build_report(chat_id, chat_title, hours)
        if report_text is None:
            logger.info(f"  [{chat_title}] 消息不足，跳过")
            continue

        logger.info(f"  [{chat_title}] 报告生成 ({len(report_text)} 字符)")

        ok = await send_report_to_channel(client, channel_id, report_text)

        messages = db.get_recent_messages(chat_id, hours)
        db.save_report(
            chat_id=chat_id,
            chat_title=chat_title,
            report_text=report_text,
            message_count=len(messages),
            start_time=messages[0]["message_date"] if messages else datetime.utcnow(),
            end_time=datetime.utcnow(),
        )

        if ok:
            logger.info(f"  [{chat_title}] ✓ 已发送")


def cleanup_task():
    """清理旧消息"""
    days = int(os.environ.get("MESSAGE_RETENTION_DAYS", "30"))
    deleted = db.cleanup_old_messages(days)
    if deleted > 0:
        logger.info(f"清理了 {deleted} 条 {days} 天前的旧消息")


def start_scheduler(client: TelegramClient):
    """启动定时任务调度器"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    hours = int(os.environ.get("SUMMARY_INTERVAL_HOURS", "6"))
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        cleanup_task,
        "cron",
        hour=3,
        minute=0,
        id="tg_cleanup",
    )

    async def summary_loop():
        while True:
            await asyncio.sleep(hours * 3600)
            try:
                await run_summary(client)
            except Exception as e:
                logger.error(f"报告生成异常: {e}")

    asyncio.create_task(summary_loop())

    scheduler.start()
    logger.info(f"调度器已启动：每 {hours} 小时生成报告，每天 3:00 清理旧消息")
    return scheduler
