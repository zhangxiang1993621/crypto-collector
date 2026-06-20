"""管理员 AI 加密市场日报

功能：从 news 分类查询最新帖子，使用 DeepSeek AI 以数字货币专家身份
      生成摘要、分析判断、预判展望，由管理员 indoAdmin 发布到 Hot Tokens

用法：
    python ai_digest/ai_digest.py                  # 仅生成不发布
    python ai_digest/ai_digest.py --save           # 生成并入库
    python ai_digest/ai_digest.py --save --max 15  # 取最新 15 条新闻
"""

import os
import sys
import re
import json
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


# ────────────────────── 配置 ──────────────────────

def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def get_deepseek_key() -> str:
    return os.environ["DEEPSEEK_API_KEY"]


# ────────────────────── 数据查询 ──────────────────────

def fetch_news_posts(client: Client, limit: int) -> list[str]:
    """查询 news 分类最新帖子，返回纯文本列表"""
    cat_r = client.table("categories").select("id").eq("name", "news").execute()
    if not cat_r.data:
        logger.error("未找到 news 分类")
        sys.exit(1)

    result = client.table("posts").select("title,content").eq(
        "category_id", cat_r.data[0]["id"]
    ).order("created_at", desc=True).limit(limit).execute()

    texts = []
    for p in result.data:
        plain = re.sub(r'<[^>]+>', ' ', p["content"])
        plain = re.sub(r'\s+', ' ', plain).strip()
        if len(plain) >= 30:
            texts.append(f"{p['title']}\n{plain[:400]}")

    logger.info(f"查询到 {len(result.data)} 条新闻, 有效 {len(texts)} 条")
    return texts


def lookup_admin(client: Client) -> dict:
    username = os.environ.get("POSTS_AUTHOR_USERNAME", "indoAdmin")
    result = client.table("profiles").select("id,username").eq("username", username).execute()
    return result.data[0]


def get_hot_tokens_cat_id(client: Client) -> str:
    name = os.environ.get("HOT_TOKENS_CATEGORY_NAME", "Hot Tokens")
    result = client.table("categories").select("id").eq("name", name).execute()
    return result.data[0]["id"]


# ────────────────────── AI 调用 ──────────────────────

def call_deepseek_digest(api_key: str, news_texts: list[str]) -> dict:
    """调用 DeepSeek 生成摘要+分析+预判"""
    news_block = "\n\n---\n\n".join(
        f"[{i + 1}] {t}" for i, t in enumerate(news_texts)
    )

    system_prompt = (
        "你是一位资深数字货币研究员。根据以下最新加密市场新闻，生成一份专业日报。\n"
        "输出 JSON 格式：\n"
        "{\n"
        '  "title": "25字以内的吸引眼球标题",\n'
        '  "summary": "50-80字的市场摘要，讲清楚今天发生了哪些关键事件",\n'
        '  "analysis": "80-120字的深度分析，从宏观、链上数据、资金流向角度剖析市场，体现专业洞察",\n'
        '  "outlook": "50-80字的后市展望，给出明确的短期预判和需要关注的关键信号",\n'
        '  "tags": ["BTC", "ETH", "DeFi", ...]\n'
        "}\n"
        "要求：\n"
        "1. summary 简洁概括关键事件\n"
        "2. analysis 要有逻辑支撑，引用新闻中的具体信息\n"
        "3. outlook 要给出明确方向判断（看多/看空/震荡）和关键观察点\n"
        "4. tags 2-4 个英文关键词\n"
        "5. 只输出 JSON，不要其他文字"
    )

    resp = httpx.post(
        f"{DEEPSEEK_BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"以下是最近加密市场新闻：\n\n{news_block}"},
            ],
            "temperature": 0.7,
            "max_tokens": 1200,
        },
        timeout=90,
    )

    if resp.status_code != 200:
        logger.error(f"DeepSeek 错误: {resp.status_code}")
        sys.exit(1)

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```\w*\n', '', raw)
        raw = re.sub(r'\n```$', '', raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"JSON 解析失败: {raw[:300]}")
        sys.exit(1)


# ────────────────────── HTML 构建 ──────────────────────

def build_html(ai: dict, news_count: int) -> str:
    """构建日报 HTML"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return "\n".join([
        f'<p style="color:#888;font-size:13px;">'
        f'📅 加密市场日报 | {now} (UTC) | 基于 {news_count} 条新闻</p>',

        '<div style="background:#fef9e7;padding:14px 16px;border-radius:8px;'
        'margin:14px 0;border-left:4px solid #f1c40f;">'
        f'<p style="font-weight:bold;color:#7d6608;margin:0 0 6px;">📋 市场摘要</p>'
        f'<p style="color:#5d4e37;line-height:1.8;margin:0;">{_escape(ai["summary"])}</p>'
        '</div>',

        '<div style="background:#eaf2f8;padding:14px 16px;border-radius:8px;'
        'margin:14px 0;border-left:4px solid #2980b9;">'
        f'<p style="font-weight:bold;color:#1a5276;margin:0 0 6px;">🔍 深度分析</p>'
        f'<p style="color:#1b4f72;line-height:1.8;margin:0;">{_escape(ai["analysis"])}</p>'
        '</div>',

        '<div style="background:#e8f8f5;padding:14px 16px;border-radius:8px;'
        'margin:14px 0;border-left:4px solid #1abc9c;">'
        f'<p style="font-weight:bold;color:#0e6251;margin:0 0 6px;">📈 后市展望</p>'
        f'<p style="color:#145a32;line-height:1.8;margin:0;">{_escape(ai["outlook"])}</p>'
        '</div>',

        '<hr>'
        '<p style="font-size:11px;color:#aaa;">'
        '⚠ 以上内容由 AI 生成，仅供参考，不构成投资建议。</p>',
    ])


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ────────────────────── 标签 ──────────────────────

def sync_tags(client: Client, post_id: str, tags: list[str]) -> None:
    if not tags:
        return
    unique = list(set(tags))
    existing_map = {}
    try:
        r = client.table("tags").select("id,name").in_("name", unique).execute()
        existing_map = {d["name"]: d["id"] for d in r.data}
    except Exception:
        pass

    new = [n for n in unique if n not in existing_map]
    if new:
        try:
            r = client.table("tags").insert([{"name": n, "posts_count": 0} for n in new]).execute()
            for d in r.data:
                existing_map[d["name"]] = d["id"]
        except Exception:
            pass

    for name in unique:
        tid = existing_map.get(name)
        if not tid:
            continue
        try:
            link = client.table("post_tags").select("post_id").eq("post_id", post_id).eq("tag_id", tid).execute()
            if not link.data:
                client.table("post_tags").insert({"post_id": post_id, "tag_id": tid}).execute()
        except Exception:
            pass


# ────────────────────── 主流程 ──────────────────────

def run(save: bool = False, max_news: int = 10):
    logger.info("=== 管理员 AI 加密日报 ===")
    client = get_client()
    news = fetch_news_posts(client, max_news)

    if not news:
        logger.warning("无有效新闻")
        return

    api_key = get_deepseek_key()
    ai = call_deepseek_digest(api_key, news)
    html = build_html(ai, len(news))

    logger.info(f"标题: {ai['title']}")
    logger.info(f"摘要: {ai['summary'][:80]}...")
    logger.info(f"标签: {ai.get('tags', [])}")

    if save:
        admin = lookup_admin(client)
        cat_id = get_hot_tokens_cat_id(client)
        now = datetime.now(timezone.utc).isoformat()

        resp = client.table("posts").insert({
            "title": ai["title"],
            "content": html,
            "author_id": admin["id"],
            "category_id": cat_id,
            "status": "pending_review",
            "created_at": now,
            "updated_at": now,
        }).execute()

        pid = resp.data[0]["id"]
        tags = ai.get("tags", []) + ["AI分析", "CryptoAI", "MarketDigest"]
        sync_tags(client, pid, tags)
        logger.info(f"[入库] id={pid[:8]}... 标签: {tags}")

    logger.info("=== 日报完成 ===")


def main():
    p = argparse.ArgumentParser(description="管理员 AI 加密日报")
    p.add_argument("--save", action="store_true")
    p.add_argument("--max", type=int, default=10)
    args = p.parse_args()
    run(save=args.save, max_news=args.max)


if __name__ == "__main__":
    main()
