"""数据库模块 — SQLite 本地存储"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "tg_summary.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            chat_title TEXT DEFAULT '',
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            text TEXT NOT NULL,
            message_date DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
        CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(message_date);
        CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            chat_title TEXT DEFAULT '',
            report_text TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            start_time DATETIME NOT NULL,
            end_time DATETIME NOT NULL,
            posted BOOL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def save_message(
    chat_id: int,
    chat_title: str,
    user_id: int,
    username: str,
    first_name: str,
    text: str,
    message_date: datetime,
) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO messages (chat_id, chat_title, user_id, username, first_name, text, message_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (chat_id, chat_title, user_id, username, first_name, text, message_date),
    )
    conn.commit()
    conn.close()


def get_recent_messages(chat_id: int, hours: int = 6) -> list[dict]:
    """获取指定群组最近 N 小时的消息"""
    since = datetime.utcnow() - timedelta(hours=hours)
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM messages
           WHERE chat_id = ? AND message_date >= ?
           ORDER BY message_date ASC""",
        (chat_id, since),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_chats(hours: int = 24) -> list[dict]:
    """获取最近有消息的群组列表"""
    since = datetime.utcnow() - timedelta(hours=hours)
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT chat_id, chat_title
           FROM messages
           WHERE message_date >= ?
           ORDER BY chat_title""",
        (since,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_report(
    chat_id: int,
    chat_title: str,
    report_text: str,
    message_count: int,
    start_time: datetime,
    end_time: datetime,
) -> int:
    conn = get_conn()
    c = conn.execute(
        """INSERT INTO reports (chat_id, chat_title, report_text, message_count, start_time, end_time)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (chat_id, chat_title, report_text, message_count, start_time, end_time),
    )
    conn.commit()
    report_id = c.lastrowid
    conn.close()
    return report_id


def mark_report_posted(report_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE reports SET posted = 1 WHERE id = ?", (report_id,))
    conn.commit()
    conn.close()


def cleanup_old_messages(days: int = 30) -> int:
    cutoff = datetime.utcnow() - timedelta(days=days)
    conn = get_conn()
    c = conn.execute("DELETE FROM messages WHERE message_date < ?", (cutoff,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted
