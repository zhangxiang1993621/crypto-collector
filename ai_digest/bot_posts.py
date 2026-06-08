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

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEEPSEEK_BASE = "https://api.deepseek.com"


# ────────────────────── 工具 ──────────────────────

def get_client() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def get_deepseek_key() -> str:
    return os.environ["DEEPSEEK_API_KEY"]


def extract_text(html: str) -> str:
    t = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', t).strip()


# ────────────────────── 数据 ──────────────────────

def fetch_news_texts(client: Client, limit: int) -> list[str]:
    """获取最新新闻标题+摘要供 AI 参考"""
    cat_r = client.table("categories").select("id").eq("name", "news").execute()
    if not cat_r.data:
        return []
    cat_id = cat_r.data[0]["id"]

    r = client.table("posts").select("title,content").eq(
        "category_id", cat_id
    ).order("created_at", desc=True).limit(limit).execute()

    texts = []
    for p in r.data:
        plain = extract_text(p["content"])
        if len(plain) >= 30:
            texts.append(f"{p['title']}\n{plain[:350]}")
    return texts


def get_bots(client: Client, count: int) -> list[dict]:
    r = client.table("profiles").select("id,username").eq("is_bot", True).execute()
    return random.sample(r.data, min(count, len(r.data)))


def get_cat_id(client: Client) -> str:
    name = os.environ.get("HOT_TOKENS_CATEGORY_NAME", "Hot Tokens")
    return client.table("categories").select("id").eq("name", name).execute().data[0]["id"]


# ────────────────────── AI 生成 ──────────────────────

def generate_bot_post(api_key: str, bot_name: str, news_brief: str) -> dict | None:
    """为单个机器人生成一篇观点帖"""
    # 随机帖子风格
    styles = [
        "像一个在群里吐槽行情的散户，表达自己的困惑或兴奋",
        "像一个有点经验的老韭菜，发表对当前行情的判断",
        "像一个刚入圈的新手，提出问题引发讨论",
        "像一个技术分析师，从K线/指标角度来看",
        "像一个消息面交易员，对最新新闻做出反应",
    ]
    style = random.choice(styles)

    system_prompt = (
        f"你的名字是 {bot_name}，你是加密货币社区的一个普通用户。\n"
        f"你要{style}。\n"
        "根据以下新闻简报，生成一条帖子。\n"
        "要求：\n"
        f"1. 输出 JSON：{{\"title\": \"标题(20字以内)\", \"body\": \"帖子正文(2-4句口语)\", \"tags\": [\"BTC\", ...]}}\n"
        "2. body 完全口语化，像微信群聊天，不要 AI 腔，不要加\"根据新闻\"\"作为用户\"\n"
        "3. 可以带 emoji、反问、预测、讨论性提问\n"
        "4. tags 英文 1-3 个\n"
        "5. 只输出 JSON"
    )

    resp = httpx.post(
        f"{DEEPSEEK_BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"新闻简报：\n{news_brief}"},
            ],
            "temperature": 0.95,
            "max_tokens": 600,
        },
        timeout=60,
    )

    if resp.status_code != 200:
        logger.error(f"DeepSeek 错误: {resp.status_code}")
        return None

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```\w*\n', '', raw)
        raw = re.sub(r'\n```$', '', raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"JSON 解析失败，跳过")
        return None


# ────────────────────── HTML ──────────────────────

def build_post_html(body: str) -> str:
    return "\n".join([
        f'<p style="font-size:16px;line-height:1.9;color:#333;">{body}</p>',
        '<hr>',
        '<p style="font-size:11px;color:#aaa;">'
        '🤖 以上内容由社区机器人自动生成，仅供参考。</p>',
    ])


# ────────────────────── 标签 ──────────────────────

def sync_tags(client: Client, post_id: str, tags: list[str]) -> None:
    if not tags:
        return
    unique = list(set(tags))
    em = {}
    try:
        r = client.table("tags").select("id,name").in_("name", unique).execute()
        em = {d["name"]: d["id"] for d in r.data}
    except Exception:
        pass
    new = [n for n in unique if n not in em]
    if new:
        try:
            r = client.table("tags").insert([{"name": n, "posts_count": 0} for n in new]).execute()
            for d in r.data:
                em[d["name"]] = d["id"]
        except Exception:
            pass
    for name in unique:
        tid = em.get(name)
        if not tid:
            continue
        try:
            lk = client.table("post_tags").select("post_id").eq("post_id", post_id).eq("tag_id", tid).execute()
            if not lk.data:
                client.table("post_tags").insert({"post_id": post_id, "tag_id": tid}).execute()
        except Exception:
            pass


# ────────────────────── 主流程 ──────────────────────

def run(save: bool = False, bot_count: int = 5, max_news: int = 15):
    logger.info("=== 机器人独立发帖 ===")
    client = get_client()
    news = fetch_news_texts(client, max_news)

    if not news:
        logger.warning("无有效新闻")
        return

    news_brief = "\n\n".join(f"- {t}" for t in news)
    bots = get_bots(client, bot_count)
    logger.info(f"选择 {len(bots)} 个机器人发帖")

    api_key = get_deepseek_key()
    posts = []

    for bot in bots:
        logger.info(f"生成 [{bot['username']}] 的观点帖...")
        ai = generate_bot_post(api_key, bot["username"], news_brief)
        if not ai:
            continue

        html = build_post_html(ai["body"])
        posts.append({
            "bot": bot,
            "title": ai["title"],
            "content": html,
            "tags": ai.get("tags", []) + ["CryptoView", "BotOpinion"],
        })
        logger.info(f"  {ai['title'][:50]}")

    if save:
        cat_id = get_cat_id(client)
        now = datetime.now(timezone.utc).isoformat()
        saved = 0
        for p in posts:
            resp = client.table("posts").insert({
                "title": p["title"],
                "content": p["content"],
                "author_id": p["bot"]["id"],
                "category_id": cat_id,
                "status": "published",
                "created_at": now,
                "updated_at": now,
            }).execute()
            sync_tags(client, resp.data[0]["id"], p["tags"])
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
