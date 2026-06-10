"""Telegram 客户端 — 用用户账号监听群消息 + 发送报告

使用 Telethon (MTProto API)，无需把机器人拉入群。
账号已加入的群组即可自动读取消息。
"""

import os
import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.types import User

import collector
import db

logger = logging.getLogger(__name__)

SESSION_FILE = str(db.DB_PATH.parent / "tg_session")

_client: TelegramClient | None = None


async def get_client() -> TelegramClient:
    """获取或创建 Telethon 客户端"""
    global _client
    if _client is not None and _client.is_connected():
        return _client

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    phone = os.environ.get("TELEGRAM_PHONE", "")

    _client = TelegramClient(SESSION_FILE, api_id, api_hash)

    await _client.start(phone=phone or None)
    logger.info("Telethon 客户端已连接")
    return _client


async def send_report_to_channel(client: TelegramClient, channel_id: str, text: str) -> bool:
    """发送报告到专属管理频道（使用用户账号）"""
    if not channel_id:
        logger.warning("未配置 REPORT_CHANNEL_ID，无法发送报告")
        return False
    try:
        await client.send_message(
            channel_id,
            text,
            parse_mode=None,       # 纯文本
            link_preview=False,
        )
        logger.info(f"报告已发送到频道 {channel_id}")
        return True
    except Exception as e:
        logger.error(f"发送报告失败: {e}")
        return False


async def start_listener(client: TelegramClient) -> None:
    """注册消息监听器"""

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event: events.NewMessage.Event):
        msg = event.message
        if msg is None or not msg.text:
            return

        chat = await event.get_chat()
        sender = await event.get_sender()

        chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id)

        username = ""
        first_name = ""
        user_id = 0
        if isinstance(sender, User):
            user_id = sender.id
            username = sender.username or ""
            first_name = sender.first_name or ""

        collector.save(
            chat_id=chat.id,
            chat_title=chat_title,
            user_id=user_id,
            username=username,
            first_name=first_name,
            text=msg.text,
            message_date=msg.date.replace(tzinfo=None),
        )

    # 只监听群组/超级群（忽略私聊和频道）
    # 通过 add_event_handler 时可以加 chats= 过滤，这里不加则监听所有对话
    logger.info("消息监听器已注册")


async def disconnect_client(client: TelegramClient) -> None:
    """断开连接"""
    if client and client.is_connected():
        await client.disconnect()
        logger.info("Telethon 客户端已断开")
