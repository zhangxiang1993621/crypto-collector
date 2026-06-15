"""哔哩哔哩热门视频爬虫

基于 CloakBrowser 抓取 B站热门视频列表，包含 UP 主详细信息。
输出 Markdown 格式文件。

用法:
    python bilibili_scraper/bilibili_scraper.py
"""

import re
import json
import time
import logging
from typing import TYPE_CHECKING
from datetime import datetime, timezone

import httpx
from cloakbrowser import launch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

POPULAR_URL = "https://www.bilibili.com/v/popular/all?spm_id_from=333.1007.0.0"


def extract_bvid(url: str) -> str | None:
    """从视频 URL 中提取 BV 号"""
    m = re.search(r"/video/(BV[\w]+)", url)
    return m.group(1) if m else None


def get_video_detail(bvid: str) -> dict | None:
    """通过 B站 API 获取视频详细信息（含 UP 主 mid）"""
    api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        resp = httpx.get(api_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        }, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return data["data"]
        logger.warning(f"API 返回错误 bvid={bvid}: {data.get('message')}")
    except Exception as e:
        logger.error(f"获取视频详情失败 bvid={bvid}: {e}")
    return None


def get_up_info(mid: int, retry: int = 0) -> dict | None:
    """通过 B站 API 获取 UP 主空间信息"""
    if retry >= 3:
        logger.warning(f"UP 主信息 API 重试耗尽 mid={mid}")
        return None
    api_url = f"https://api.bilibili.com/x/space/acc/info?mid={mid}"
    try:
        resp = httpx.get(api_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Referer": f"https://space.bilibili.com/{mid}",
            "Cookie": "buvid3=auto",  # 简单 cookie 避免被拦截
        }, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return data["data"]
        msg = data.get("message", "")
        if "频繁" in msg or data.get("code") == -412:
            wait = 3 * (retry + 1)
            logger.info(f"频率限制，等待 {wait}s 后重试...")
            time.sleep(wait)
            return get_up_info(mid, retry + 1)
        if "非法" in msg:
            logger.warning(f"UP {mid} 空间不可访问")
            return None
        logger.warning(f"UP 主信息 API 错误 mid={mid}: {msg}")
    except Exception as e:
        logger.error(f"获取 UP 主信息失败 mid={mid}: {e}")
        if retry < 2:
            time.sleep(3)
            return get_up_info(mid, retry + 1)
    return None


def format_count(num: int | str) -> str:
    """格式化数字为易读形式"""
    if isinstance(num, str):
        return num
    if num >= 10000:
        return f"{num / 10000:.1f}万"
    return str(num)


def main():
    logger.info("=" * 50)
    logger.info("B站热门视频爬虫启动")

    # 第一步：使用 CloakBrowser 抓取热门列表
    logger.info("正在启动 CloakBrowser（无头模式）...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.set_viewport_size({"width": 1920, "height": 1080})

    try:
        logger.info(f"正在访问: {POPULAR_URL}")
        page.goto(POPULAR_URL, wait_until="networkidle", timeout=30000)

        # 等待视频卡片加载
        page.wait_for_selector(".video-card", timeout=15000)
        logger.info("页面加载完成，开始提取视频卡片...")

        # 提取视频卡片数据
        videos = page.evaluate("""() => {
            const cards = document.querySelectorAll('.video-card');
            return Array.from(cards).map(card => ({
                title: card.querySelector('.video-name')?.textContent?.trim() || '',
                link: card.querySelector('a[href*="/video/"]')?.href || '',
                up_name: card.querySelector('.up-name__text')?.textContent?.trim() || '',
                play: card.querySelector('.play-text')?.textContent?.trim() || '',
                like: card.querySelector('.like-text')?.textContent?.trim() || '',
                tag: card.querySelector('.rcmd-tag')?.textContent?.trim() || '',
            }));
        }""")

        logger.info(f"共提取到 {len(videos)} 个视频")

    finally:
        browser.close()
        logger.info("浏览器已关闭")

    # 第二步：通过 API 获取每个视频的详细信息（含 UP 主 mid 和粉丝数）
    logger.info("正在通过 API 获取视频详情和 UP 主信息...")
    for i, v in enumerate(videos):
        bvid = extract_bvid(v.get("link", ""))
        if not bvid:
            logger.warning(f"无法提取 BV 号，跳过: {v.get('title', '')}")
            continue

        # 获取视频详情（含 owner mid、stat）
        detail = get_video_detail(bvid)
        if detail:
            owner = detail.get("owner", {})
            stat = detail.get("stat", {})
            mid = owner.get("mid")

            # 用 API 数据补充/修正统计数据
            if stat:
                v["play"] = format_count(stat.get("view", 0))
                v["danmaku"] = format_count(stat.get("danmaku", 0))
                v["like"] = format_count(stat.get("like", 0))
                v["coin"] = format_count(stat.get("coin", 0))
                v["favorite"] = format_count(stat.get("favorite", 0))

            # UP 主基本信息来自视频 API
            if mid:
                v["up_info"] = {
                    "name": owner.get("name", v.get("up_name", "")),
                    "mid": mid,
                    "face": owner.get("face", ""),
                }
                v["up_name"] = owner.get("name", v.get("up_name", ""))
                logger.info(f"  [{i+1}/{len(videos)}] {v['title'][:30]}... | UP: {v['up_name']} | 播放: {v['play']}")
            else:
                v["up_info"] = {}
        else:
            logger.warning(f"  [{i+1}/{len(videos)}] {v['title'][:30]}... | API 获取失败，使用原始数据")
            v["up_info"] = {}

    logger.info("视频列表提取完成")
    logger.info("=" * 50)

    # 打印摘要
    for i, v in enumerate(videos[:10]):
        logger.info(f"  [{i+1}] {v['title'][:50]}... | UP: {v.get('up_name', '?')} | 播放: {v.get('play', '?')}")

    return videos


if __name__ == "__main__":
    main()
