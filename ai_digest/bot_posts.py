"""机器人独立发帖脚本

功能：从 news 分类获取最新加密新闻，随机选择机器人独立发帖
     每个机器人以自己身份发表对市场的看法、观点或提问，口语化表达
     所有帖子发布到 Hot Tokens 分类

用法：
    python ai_digest/bot_posts.py                  # 预览不发布
    python ai_digest/bot_posts.py --save           # 生成并入库
    python ai_digest/bot_posts.py --save --bots 5  # 5 个机器人发帖
"""

import os
import sys
import re
import json
import random
import uuid
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

# 子进程执行时需要项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv
# 直连数据库（绕过 REST API 作业限制）
from db_direct import select_one, select_all, insert_one, execute_sql

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEEPSEEK_BASE = "https://api.deepseek.com"


# ────────────────────── 工具 ──────────────────────


def get_deepseek_key() -> str:
    return os.environ["DEEPSEEK_API_KEY"]


def extract_text(html: str) -> str:
    t = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', t).strip()


# ────────────────────── 数据（直连 PostgreSQL） ──────────────────────

def fetch_news_texts(limit: int) -> list[str]:
    """获取最新新闻标题+摘要供 AI 参考"""
    cat_row = select_one("categories", {"name": "news"}, columns="id")
    if not cat_row:
        return []
    cat_id = cat_row["id"]

    sql = '''
        SELECT title, content FROM posts 
        WHERE category_id = %s 
        ORDER BY created_at DESC 
        LIMIT %s
    '''
    rows = execute_sql(sql, (cat_id, limit))

    texts = []
    if rows:
        for p in rows:
            plain = extract_text(p["content"])
            if len(plain) >= 30:
                texts.append(f"{p['title']}\n{plain[:350]}")
    return texts


def get_bots(count: int) -> list[dict]:
    rows = select_all("profiles", {"is_bot": True}, columns="id,username")
    return random.sample(rows, min(count, len(rows))) if rows else []


def get_cat_id() -> str:
    name = os.environ.get("HOT_TOKENS_CATEGORY_NAME", "Hot Tokens")
    row = select_one("categories", {"name": name}, columns="id")
    return row["id"]


# ────────────────────── AI 生成 ──────────────────────

# 机器人角色池：每种角色有不同的关注点和语气
BOT_PERSONAS = [
    {
        "role": "技术分析师",
        "focus": "关注K线形态、支撑阻力、交易量、MACD/RSI等技术指标",
        "tone": "用数据说话，冷静专业",
        "style": "像专业交易员在做复盘",
    },
    {
        "role": "消息面猎手",
        "focus": "关注突发新闻、政策变化、机构动向对市场的影响",
        "tone": "反应迅速，解读消息对行情的影响",
        "style": "像财经记者在发快讯分析",
    },
    {
        "role": "链上数据控",
        "focus": "关注链上资金流、巨鲸地址、交易所流入流出、TVL变化",
        "tone": "数据驱动，从链上发现信号",
        "style": "像数据分析师在分享发现",
    },
    {
        "role": "散户吐槽大王",
        "focus": "关注自己持仓的涨跌、市场的情绪变化、社区热点",
        "tone": "情绪化，带梗，爱用emoji，吐槽主力套路",
        "style": "像在群里和币友吹水",
    },
    {
        "role": "基本面信徒",
        "focus": "关注项目进展、协议升级、生态发展、长期价值",
        "tone": "理性乐观，强调长期视角",
        "style": "像价值投资者在分享研究结论",
    },
    {
        "role": "空头司令",
        "focus": "关注市场风险、泡沫信号、利空因素、可能的下行",
        "tone": "谨慎悲观，指出风险点",
        "style": "像做空者在找做空理由",
    },
    {
        "role": "新韭菜",
        "focus": "对新鲜事物好奇，容易FOMO，会问一些基础问题",
        "tone": "兴奋中带迷茫，渴望学习",
        "style": "像刚入圈的新人在请教",
    },
]


def generate_all_bot_posts(api_key: str, bots: list[dict], news_brief: str) -> list[dict]:
    """一次 API 调用为所有机器人生成观点帖，确保风格各异"""

    # 为每个机器人分配不同角色（循环使用角色池）
    assignments = []
    for i, bot in enumerate(bots):
        persona = BOT_PERSONAS[i % len(BOT_PERSONAS)]
        assignments.append({
            "name": bot["username"],
            "role": persona["role"],
            "focus": persona["focus"],
            "tone": persona["tone"],
        })

    # 构建角色描述
    roles_desc = "\n".join(
        f"- {a['name']}: {a['role']}，{a['focus']}，语气：{a['tone']}"
        for a in assignments
    )

    system_prompt = (
        "你是一个加密货币社区的内容生成器。以下是几个不同的社区成员，"
        "请帮每个人各生成一条帖子。\n\n"
        f"{roles_desc}\n\n"
        "核心要求：\n"
        "1. 每个人的帖子必须完全不同的角度和观点，不能出现相似的表达\n"
        "2. 有人看多、有人看空、有人中立，观点要有冲突和对比\n"
        "3. 每人只关注自己角色对应的领域，不要跨界\n"
        "4. 帖子有emoji、反问、预测，像真人聊天\n"
        "5. body 2-4句口语，不超过50个字\n"
        "6. 输出 JSON 数组，每项格式：\n"
        '   {"name": "用户名", "title": "20字内标题", "body": "正文", "tags": ["TAG"]}\n'
        "7. 只输出 JSON 数组，不要其他文字"
    )

    user_content = f"以下是最新加密市场新闻，请各角色据此发帖：\n\n{news_brief}"

    resp = httpx.post(
        f"{DEEPSEEK_BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.9,
            "max_tokens": 2500,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        logger.error(f"DeepSeek 错误: {resp.status_code}")
        return []

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```\w*\n', '', raw)
        raw = re.sub(r'\n```$', '', raw)

    try:
        results = json.loads(raw)
        if not isinstance(results, list):
            logger.error(f"AI 返回非数组: {raw[:200]}")
            return []
        logger.info(f"AI 一次生成 {len(results)} 条帖子")
        return results
    except json.JSONDecodeError:
        logger.error(f"JSON 解析失败: {raw[:300]}")
        return []


# ────────────────────── HTML ──────────────────────

def build_post_html(body: str) -> str:
    return f'<p style="font-size:16px;line-height:1.9;color:#333;">{body}</p>'


# ────────────────────── 标签（直连 PostgreSQL） ──────────────────────

def sync_tags(post_id: str, tags: list[str]) -> None:
    if not tags:
        return
    unique = list(set(tags))
    em = {}
    if unique:
        placeholders = ", ".join(["%s"] * len(unique))
        sql = f'SELECT id, name FROM tags WHERE name IN ({placeholders})'
        rows = execute_sql(sql, tuple(unique))
        if rows:
            em = {d["name"]: d["id"] for d in rows}

    new = [n for n in unique if n not in em]
    if new:
        for name in new:
            try:
                result = insert_one("tags", {"name": name, "posts_count": 0}, returning="id")
                if result:
                    em[name] = result["id"]
            except Exception:
                pass

    for name in unique:
        tid = em.get(name)
        if not tid:
            continue
        try:
            link = select_one("post_tags", {"post_id": post_id, "tag_id": tid}, columns="post_id")
            if not link:
                insert_one("post_tags", {"post_id": post_id, "tag_id": tid})
        except Exception:
            pass


# ────────────────────── 主流程 ──────────────────────

def run(save: bool = False, bot_count: int = 5, max_news: int = 15):
    logger.info("=== 机器人独立发帖 ===")
    news = fetch_news_texts(max_news)

    if not news:
        logger.warning("无有效新闻")
        return

    news_brief = "\n\n".join(f"- {t}" for t in news)
    bots = get_bots(bot_count)
    logger.info(f"选择 {len(bots)} 个机器人发帖")

    api_key = get_deepseek_key()
    ai_results = generate_all_bot_posts(api_key, bots, news_brief)

    # 建立 bot name → bot record 映射
    bot_map = {b["username"]: b for b in bots}

    posts = []
    for ai in ai_results:
        name = ai.get("name", "")
        bot = bot_map.get(name)
        if not bot:
            logger.warning(f"  跳过未知用户: {name}")
            continue

        html = build_post_html(ai["body"])
        posts.append({
            "bot": bot,
            "title": ai["title"],
            "content": html,
            "tags": ai.get("tags", []) + ["CryptoView", "BotOpinion"],
        })
        logger.info(f"  [{name}] {ai['title'][:50]}")

    if save:
        cat_id = get_cat_id()
        now = datetime.now(timezone.utc).isoformat()
        saved = 0
        for p in posts:
            result = insert_one("posts", {
                "title": p["title"],
                "content": p["content"],
                "author_id": p["bot"]["id"],
                "category_id": cat_id,
                "status": "pending_review",
                "created_at": now,
                "updated_at": now,
            }, returning="id")
            sync_tags(result["id"], p["tags"])
            saved += 1
        logger.info(f"[入库] {saved} 篇")

    logger.info(f"=== 完成 {len(posts)} 篇 ===")


def main():
    p = argparse.ArgumentParser(description="机器人独立发帖")
    p.add_argument("--save", action="store_true")
    p.add_argument("--bots", type=int, default=5, help="发帖机器人数 (默认5)")
    p.add_argument("--max", type=int, default=15, help="参考新闻数 (默认15)")
    args = p.parse_args()
    run(save=args.save, bot_count=args.bots, max_news=args.max)


if __name__ == "__main__":
    main()
