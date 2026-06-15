"""新闻转帖组装脚本

功能：将抓取的新闻数据组装成帖子格式（HTML 富文本）
- 根据配置的用户名从 profiles 表查询 author_id
- 根据配置的分类名从 categories 表查询 category_id
- 内容生成为 HTML，原文链接为超链接，图片转 base64
- 组装后的数据保存到文件，可选写入 Supabase posts 表

用法：python assemble_posts.py [--input 新闻JSON] [--output 输出文件] [--save]
"""

import os
import sys
import json
import base64
import logging
import argparse
from pathlib import Path

from typing import TYPE_CHECKING

import httpx
from dotenv import load_dotenv
from supabase_client import get_client

if TYPE_CHECKING:
    from supabase import Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 20

# 加载环境变量
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")


def get_env(env_name: str) -> str:
    """获取必需的环境变量"""
    value = os.environ.get(env_name)
    if not value:
        logger.error(f"缺少环境变量: {env_name}")
        sys.exit(1)
    return value


def lookup_author_id(client: "Client", username: str) -> str:
    """根据用户名从 profiles 表查询 author_id"""
    try:
        result = client.table("profiles").select("id,username").eq("username", username).execute()
        if result.data:
            profile = result.data[0]
            logger.info(f"找到作者: {profile['username']} (id={profile['id']})")
            return profile["id"]
        else:
            logger.error(f"未找到用户: {username}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"查询 profiles 失败: {e}")
        sys.exit(1)


def lookup_category_id(client: "Client", category_name: str) -> str:
    """根据分类名从 categories 表查询 category_id"""
    try:
        result = client.table("categories").select("id,name").eq("name", category_name).execute()
        if result.data:
            category = result.data[0]
            logger.info(f"找到分类: {category['name']} (id={category['id']})")
            return category["id"]
        else:
            logger.error(f"未找到分类: {category_name}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"查询 categories 失败: {e}")
        sys.exit(1)


def load_news(news_path: str) -> list[dict]:
    """加载抓取的新闻 JSON 文件"""
    path = Path(news_path)
    if not path.exists():
        logger.error(f"新闻文件不存在: {news_path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        news_list = json.load(f)

    logger.info(f"已加载 {len(news_list)} 条新闻")
    return news_list


def download_image_as_base64(url: str) -> dict | None:
    """下载图片并转为 base64 data URI

    参数:
        url: 图片 URL（相对路径自动补全域名）

    返回:
        {"src": "data:image/...", "filename": "xxx.png"} 或 None
    """
    if not url:
        return None

    # 处理相对路径
    if url.startswith("/"):
        url = f"https://www.binance.bh{url}"

    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "image/png")
        b64 = base64.b64encode(resp.content).decode("utf-8")
        data_uri = f"data:{content_type};base64,{b64}"

        # 提取文件名
        filename = url.split("/")[-1].split("?")[0] or "image.png"
        if "." not in filename:
            ext = content_type.split("/")[-1] or "png"
            filename = f"image.{ext}"

        return {"src": data_uri, "filename": filename}
    except Exception as e:
        logger.warning(f"  图片下载失败 ({url[:80]}...): {e}")
        return None


def build_html_content(news: dict) -> tuple[str, list[dict]]:
    """构建 HTML 富文本内容和图片列表

    参数:
        news: 单条新闻数据

    返回:
        (html内容字符串, 图片列表 [{src: base64, filename: ...}])
    """
    parts = []
    image_list = []

    # 元信息行: 来源 + 时间
    meta_parts = [f"来源: {news.get('source', 'Binance News')}"]
    if news.get("time"):
        meta_parts.append(f"{news['time']}前")
    parts.append(f'<p style="color:#888;font-size:14px;">{" | ".join(meta_parts)}</p>')

    # 币种标签
    if news.get("coins"):
        coin_spans = []
        for coin in news["coins"]:
            text = coin.get("text", "")
            coin_url = coin.get("url", "")
            if coin_url:
                coin_spans.append(
                    f'<a href="{coin_url}" target="_blank" '
                    f'style="display:inline-block;margin:2px 4px;padding:2px 8px;'
                    f'background:#f0b90b;color:#000;border-radius:4px;text-decoration:none;font-size:13px;">'
                    f'{text}</a>'
                )
            else:
                coin_spans.append(
                    f'<span style="display:inline-block;margin:2px 4px;padding:2px 8px;'
                    f'background:#f0b90b;color:#000;border-radius:4px;font-size:13px;">{text}</span>'
                )
        parts.append(f'<p>{"".join(coin_spans)}</p>')

    # 正文内容:按段落分割
    content = news.get("content", "")
    if content:
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
        for p in paragraphs:
            # HTML 转义
            p_escaped = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            parts.append(f"<p>{p_escaped}</p>")

    # 原文链接（超链接形式）
    if news.get("url"):
        parts.append(
            f'<p style="margin-top:16px;">'
            f'<a href="{news["url"]}" target="_blank" '
            f'style="color:#f0b90b;text-decoration:none;">'
            f'查看原文 &rarr;</a></p>'
        )

    # 下载图片并转 base64（去重,最多取前5张内容图）
    seen_srcs = set()
    for img in news.get("images", [])[:5]:
        src = img.get("src", "")
        if not src or src in seen_srcs:
            continue
        seen_srcs.add(src)

        base64_info = download_image_as_base64(src)
        if base64_info:
            image_list.append(base64_info)

    # 在 HTML 中插入图片
    for img in image_list[:3]:  # 正文最多嵌入3张图
        parts.insert(
            1,  # 在元信息后插入
            f'<p><img src="{img["src"]}" alt="{img["filename"]}" '
            f'style="max-width:100%;border-radius:8px;margin:8px 0;" /></p>'
        )

    html = "\n".join(parts)
    return html, image_list


def assemble_posts(news_list: list[dict], author_id: str, category_id: str) -> list[dict]:
    """将新闻组装成帖子格式（HTML 富文本）"""
    posts = []

    for news in news_list:
        # 构建 HTML 内容和图片列表
        html_content, image_list = build_html_content(news)

        post = {
            "title": news["title"],
            "content": html_content,
            "author_id": author_id,
            "category_id": category_id,
            "status": "published",
            "images": image_list,
            "is_hot": False,
            "is_pinned": False,
            "tags": news.get("tags", []),
            "_meta": {
                "source_news_index": news.get("index"),
                "source_url": news.get("url"),
                "source_time": news.get("time"),
                "source_coins": news.get("coins"),
                "source_tags": news.get("tags", []),
            },
        }
        posts.append(post)

    return posts


def batch_insert_posts(client: "Client", posts: list[dict]) -> int:
    """批量插入帖子到 Supabase posts 表（标题去重, 标签关联）

    参数:
        client: Supabase 客户端
        posts: 帖子列表（含 _meta 字段，插入时自动去除）

    返回:
        成功插入的条数
    """
    # 查询现有标题，避免重复
    try:
        existing = client.table("posts").select("title").execute()
        existing_titles = {r["title"] for r in existing.data}
        logger.info(f"数据库中已有 {len(existing_titles)} 条帖子")
    except Exception as e:
        logger.warning(f"查询现有标题失败，将全部插入: {e}")
        existing_titles = set()

    # 过滤重复
    new_posts = [p for p in posts if p["title"] not in existing_titles]
    skipped = len(posts) - len(new_posts)
    if skipped > 0:
        logger.info(f"过滤重复标题: 跳过 {skipped} 条，待插入 {len(new_posts)} 条")

    total = len(new_posts)
    inserted = 0
    inserted_post_ids = []  # 记录新插入的 post_id，用于关联 tag

    for i in range(0, total, BATCH_SIZE):
        batch = new_posts[i : i + BATCH_SIZE]
        # 去除 _meta 和 id 字段，保留 tags 供后续关联
        clean_batch = []
        for post in batch:
            clean = {k: v for k, v in post.items() if k != "_meta"}
            clean.pop("id", None)
            clean.pop("tags", None)  # tags 单独通过 post_tags 关联
            clean_batch.append(clean)

        try:
            result = client.table("posts").insert(clean_batch).execute()
            count = len(result.data)
            inserted += count
            for row in result.data:
                inserted_post_ids.append(row["id"])
            logger.info(
                f"批次 {i // BATCH_SIZE + 1}/{(total + BATCH_SIZE - 1) // BATCH_SIZE}: "
                f"插入 {count} 条, 进度 {min(i + BATCH_SIZE, total)}/{total}"
            )
        except Exception as e:
            logger.error(f"批次 {i // BATCH_SIZE + 1} 插入失败: {e}")

    # 处理标签关联
    if inserted > 0:
        sync_post_tags(client, new_posts[:inserted], inserted_post_ids)

    return inserted


def sync_post_tags(client: "Client", posts: list[dict], post_ids: list[str]) -> None:
    """同步帖子标签：查找/创建 tag，写入 post_tags 关联表

    参数:
        client: Supabase 客户端
        posts: 已插入的帖子列表
        post_ids: 对应的 post_id 列表（顺序一致）
    """
    # 收集所有 tag 名称
    all_tag_names = []
    post_tag_map = {}  # post_id -> [tag_name, ...]
    for idx, post in enumerate(posts):
        tag_names = post.get("tags", [])
        if tag_names:
            post_id = post_ids[idx]
            # tag 不加 # 前缀（与 indo_news_scraper 等保持一致）
            cleaned = [t.lstrip("#") for t in tag_names]
            post_tag_map[post_id] = cleaned
            all_tag_names.extend(cleaned)

    if not all_tag_names:
        return

    unique_names = list(set(all_tag_names))
    logger.info(f"处理标签: 共 {len(unique_names)} 个唯一标签")

    # 查询已有标签
    try:
        existing = client.table("tags").select("id,name").in_("name", unique_names).execute()
        existing_map = {r["name"]: r["id"] for r in existing.data}
    except Exception as e:
        logger.warning(f"查询标签失败: {e}")
        existing_map = {}

    # 创建不存在的标签
    new_names = [n for n in unique_names if n not in existing_map]
    if new_names:
        new_tags = [{"name": n, "posts_count": 0} for n in new_names]
        try:
            result = client.table("tags").insert(new_tags).execute()
            for r in result.data:
                existing_map[r["name"]] = r["id"]
            logger.info(f"  创建 {len(result.data)} 个新标签: {new_names}")
        except Exception as e:
            logger.warning(f"  创建标签失败: {e}")

    # 写入 post_tags 关联（去重）
    post_tag_records = []
    for post_id, tag_names in post_tag_map.items():
        for name in tag_names:
            tag_id = existing_map.get(name)
            if tag_id:
                post_tag_records.append({"post_id": post_id, "tag_id": tag_id})

    if post_tag_records:
        try:
            # 先查已有关联避免重复
            all_post_ids = list(post_tag_map.keys())
            all_tag_ids = list(set(r["tag_id"] for r in post_tag_records))
            existing_links = client.table("post_tags").select("post_id,tag_id") \
                .in_("post_id", all_post_ids) \
                .in_("tag_id", all_tag_ids) \
                .execute()
            existing_pairs = {(r["post_id"], r["tag_id"]) for r in existing_links.data}

            new_records = [r for r in post_tag_records if (r["post_id"], r["tag_id"]) not in existing_pairs]
            if new_records:
                client.table("post_tags").insert(new_records).execute()
                logger.info(f"  关联 {len(new_records)} 条 post_tags")

                # 更新每个 tag 的 posts_count
                tag_count_delta = {}
                for r in new_records:
                    tag_count_delta[r["tag_id"]] = tag_count_delta.get(r["tag_id"], 0) + 1
                for tag_id, delta in tag_count_delta.items():
                    try:
                        client.rpc("increment_tag_count", {"tag_id": tag_id, "delta": delta}).execute()
                    except Exception:
                        # 如果没有 rpc，直接用 update
                        try:
                            tag = client.table("tags").select("posts_count").eq("id", tag_id).single().execute()
                            new_count = (tag.data.get("posts_count", 0) or 0) + delta
                            client.table("tags").update({"posts_count": new_count}).eq("id", tag_id).execute()
                        except Exception:
                            pass  # 更新失败不阻塞主流程
        except Exception as e:
            logger.warning(f"  关联标签失败: {e}")


def print_summary(posts: list[dict]):
    """打印汇总信息"""
    print("\n" + "=" * 60)
    print(f"组装完成！共 {len(posts)} 条帖子")
    print("=" * 60)

    for i, post in enumerate(posts[:3]):
        print(f"\n[{i + 1}] {post['title'][:60]}")
        print(f"    author_id: {post['author_id']}")
        print(f"    category_id: {post['category_id']}")
        print(f"    status: {post['status']}")
        content_preview = post["content"][:120].replace("\n", " ")
        print(f"    content preview: {content_preview}...")

    if len(posts) > 3:
        print(f"\n... 还有 {len(posts) - 3} 条帖子")


def main():
    parser = argparse.ArgumentParser(description="新闻转帖组装工具")
    parser.add_argument(
        "--save", action="store_true",
        help="写入 Supabase posts 表"
    )
    args = parser.parse_args()

    logger.info("=== 新闻转帖组装任务启动 ===")

    # 加载配置
    author_username = get_env("POSTS_AUTHOR_USERNAME")
    category_name = get_env("POSTS_CATEGORY_NAME")

    # 连接 Supabase (绕过代理)
    client = get_client()

    # 查找 author_id 和 category_id
    author_id = lookup_author_id(client, author_username)
    category_id = lookup_category_id(client, category_name)

    # 组装帖子（需要外部提供 news_list 数据源）
    logger.warning("assemble_posts 已精简为 Supabase 直接入库模式，请使用 news_scraper.py --save")
    return

    # 写入数据库
    if args.save:
        logger.info("开始写入 Supabase posts 表...")
        inserted = batch_insert_posts(client, posts)
        logger.info(f"写入完成，成功插入 {inserted}/{len(posts)} 条")

    logger.info("=== 组装完成 ===")


if __name__ == "__main__":
    main()
