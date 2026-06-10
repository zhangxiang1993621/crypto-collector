"""报告生成模块 — 统计 + 文本报告"""

import os
import re
from collections import Counter
from datetime import datetime

import db

# 报告中排除的停用词
STOP_WORDS = set(
    """
的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你
会 着 没有 看 好 自己 这 他 她 它 们 那 些 什么 吗 呢 啊 吧 哦
嗯 哈 啦 呀 咯 滴 嗯嗯 哈哈 the a an is are was were be been
being have has had do does did will would shall should can could
may might must i me my we us our you your he she it its they them
their this that these those in on at to for of with by from about
as into through during before after above below between and or not
but so if than too very just now then also here there when where
why how which who whom what all any both each every other some
no nya ya deh sih dong kok kan lah loh nih tuh mah
""".split()
)

HOT_WORD_MIN_LEN = 2


def tokenize(text: str) -> list[str]:
    """简单分词（按空格/CJK字符边界）"""
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text.lower())
    tokens = []
    for word in text.split():
        word = word.strip()
        # 英文单词直接保留
        if word.isascii() and len(word) >= HOT_WORD_MIN_LEN and word not in STOP_WORDS:
            tokens.append(word)
            continue
        # CJK 逐字切
        for ch in word:
            if ch not in STOP_WORDS and not ch.isdigit() and len(ch) >= 1:
                tokens.append(ch)
    return tokens


def build_report(chat_id: int, chat_title: str, hours: int) -> str | None:
    """生成指定群组的统计报告，返回报告文本；消息不足则返回 None"""
    min_count = int(os.environ.get("MIN_MESSAGE_COUNT", "5"))
    top_talkers = int(os.environ.get("TOP_TALKERS_COUNT", "10"))
    top_words_count = int(os.environ.get("TOP_WORDS_COUNT", "10"))

    messages = db.get_recent_messages(chat_id, hours)
    if len(messages) < min_count:
        return None

    end_time = datetime.utcnow()

    # ── 发言排行 ──
    user_counter = Counter()
    name_map: dict[int, str] = {}
    for m in messages:
        uid = m["user_id"]
        user_counter[uid] += 1
        name_map[uid] = m["username"] or m["first_name"] or str(uid)

    # ── 热词提取 ──
    word_counter = Counter()
    for m in messages:
        tokens = tokenize(m["text"] or "")
        word_counter.update(tokens)

    # ── 时段分布 ──
    interval = max(1, hours // 4)  # 分 4 段
    hour_buckets: dict[str, int] = {}
    for m in messages:
        msg_dt = m["message_date"]
        if isinstance(msg_dt, str):
            msg_dt = datetime.fromisoformat(msg_dt)
        bucket_key = f"{msg_dt.hour:02d}:00"
        hour_buckets[bucket_key] = hour_buckets.get(bucket_key, 0) + 1

    # 按小时排序
    sorted_hours = sorted(hour_buckets.items(), key=lambda x: x[0])
    max_hour_count = max(hour_buckets.values()) if hour_buckets else 1

    # ── 构建报告 ──
    lines = [
        f"📊 群聊速报 | {chat_title}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"👥 {len(set(m['user_id'] for m in messages))} 人发言，{len(messages)} 条消息",
    ]

    # 话痨榜
    top_users = user_counter.most_common(top_talkers)
    if top_users:
        medals = ["🥇", "🥈", "🥉"]
        lines.append("")
        lines.append("💬 话痨排行榜")
        for i, (uid, count) in enumerate(top_users):
            medal = medals[i] if i < 3 else f"  {i + 1}."
            lines.append(f"{medal} @{name_map.get(uid, uid)} — {count} 条")

    # 热词榜
    top_words = [
        (w, c) for w, c in word_counter.most_common(top_words_count * 2) if len(w) >= 1
    ][:top_words_count]
    if top_words:
        lines.append("")
        lines.append("🔥 高频热词")
        word_str = " ".join(f"{w}({c})" for w, c in top_words)
        lines.append(word_str)

    # 时段活跃度
    if sorted_hours:
        lines.append("")
        lines.append("📈 时段活跃度")
        bar_max = 15
        for hour_label, count in sorted_hours:
            bar_len = max(1, int(count / max_hour_count * bar_max))
            bar = "█" * bar_len
            lines.append(f"{hour_label}  {bar}{'▏' if bar_len < bar_max else ''} {count}条")

    lines.append("")
    lines.append(f"🕐 统计时段: 最近 {hours} 小时")
    lines.append(f"🤖 自动生成 @ {end_time.strftime('%Y-%m-%d %H:%M')} UTC")

    return "\n".join(lines)
