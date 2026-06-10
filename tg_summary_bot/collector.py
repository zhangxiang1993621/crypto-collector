"""消息采集模块 — 过滤、去重、写入"""

import re
import logging

import db

logger = logging.getLogger(__name__)

# 过滤规则
MIN_TEXT_LENGTH = 2
# 纯 emoji / 链接 / 转发消息跳过
SKIP_PATTERNS = [
    r"^\s*$",  # 空消息
    r"^/[a-zA-Z]+",  # 机器人指令
]
INVALID_TEXT_PATTERNS = re.compile("|".join(SKIP_PATTERNS))


def should_skip(text: str | None) -> bool:
    """判断消息是否应跳过"""
    if not text:
        return True
    if len(text.strip()) < MIN_TEXT_LENGTH:
        return True
    if INVALID_TEXT_PATTERNS.match(text.strip()):
        return True
    return False


def save(
    chat_id: int,
    chat_title: str,
    user_id: int,
    username: str,
    first_name: str,
    text: str,
    message_date,
) -> None:
    """保存一条消息（含过滤）"""
    if should_skip(text):
        return

    name = username or first_name or str(user_id)
    try:
        db.save_message(chat_id, chat_title, user_id, name, first_name, text, message_date)
        logger.debug(f"[{chat_title}] {name}: {text[:40]}...")
    except Exception as e:
        logger.error(f"消息保存失败: {e}")
