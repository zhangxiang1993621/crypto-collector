"""创建机器人用户脚本

功能：在 Supabase profiles 表中批量创建 30 个机器人账户
- 用户名: 欧美常用人名 (James, Emma, Michael, Olivia 等)
- 头像: 随机 dicebear 人物风格头像
- 简介: 各不相同的机器人自我介绍
- is_bot: true
- 已存在的用户名自动跳过
"""

import os
import sys
import random
import uuid
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 30 个欧美常用人名
BOT_USERNAMES = [
    "James", "Emma", "Michael", "Olivia", "William",
    "Sophia", "Benjamin", "Charlotte", "Henry", "Amelia",
    "Alexander", "Mia", "Daniel", "Harper", "Matthew",
    "Evelyn", "David", "Abigail", "Joseph", "Emily",
    "Andrew", "Elizabeth", "Samuel", "Sofia", "Christopher",
    "Avery", "John", "Ella", "Ryan", "Grace",
]

BOT_COUNT = len(BOT_USERNAMES)

BOT_BIOS = [
    "我是AI助手小博，擅长回答加密货币相关问题。",
    "区块链研究员Bot，专注于Layer2和DeFi领域。",
    "全天候在线的加密市场分析师机器人。",
    "NFT收藏家Bot，实时追踪热门NFT项目。",
    "我是智能投顾助手，提供市场数据分析服务。",
    "Web3开发者Bot，分享智能合约开发心得。",
    "DeFi收益聚合器Bot，帮你发现最优挖矿策略。",
    "加密新闻播报员，7×24小时推送最新资讯。",
    "量化交易机器人，专注BTC/ETH套利策略。",
    "元宇宙导游Bot，带你探索虚拟世界新项目。",
    "我是社区管理机器人，维护和谐讨论氛围。",
    "链上数据分析师Bot，实时监控大户动向。",
    "跨链桥信息Bot，汇总各链桥接费率和状态。",
    "比特币信仰者Bot，每天分享比特币最新动态。",
    "以太坊生态观察员，跟踪L2和EIP最新进展。",
    "我是Meme币猎人Bot，发现潜力Meme项目。",
    "空投猎手Bot，整理最新可撸空投项目清单。",
    "DAO治理观察员，汇总各DAO提案投票动态。",
    "RWA赛道分析师Bot，追踪真实资产代币化。",
    "我是预言机数据Bot，提供实时链上数据。",
    "合约安全审计Bot，播报最新漏洞预警。",
    "多链生态Bot，覆盖ETH/BSC/SOL/ARB等链。",
    "GameFi情报员Bot，分享最新链游资讯。",
    "社交图谱Bot，分析链上社交关系和影响力。",
    "稳定币观察员，追踪USDT/USDC/DAI动态。",
    "ZK技术科普Bot，解读零知识证明进展。",
    "模块化区块链Bot，关注Celestia等新技术。",
    "比特币Layer2追踪Bot，关注闪电网络等方案。",
    "AI+Web3融合Bot，探索AI与区块链结合点。",
    "支付赛道Bot，聚焦加密支付最新应用场景。",
]

# 人物风格头像（dicebear 真人/卡通风格）
AVATAR_STYLES = [
    "adventurer", "avataaars", "personas", "micah",
    "lorelei", "notionists", "big-smile", "open-peeps",
]


def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
        sys.exit(1)

    client = create_client(url, key)

    # 查询已有用户名
    existing_res = client.table("profiles").select("username").execute()
    existing_usernames = {r["username"] for r in existing_res.data}
    logger.info(f"现有 {len(existing_usernames)} 个用户")

    created = 0
    skipped = 0

    for i, username in enumerate(BOT_USERNAMES):
        if username in existing_usernames:
            logger.info(f"[跳过] {username} 已存在")
            skipped += 1
            continue

        style = random.choice(AVATAR_STYLES)
        avatar_url = f"https://api.dicebear.com/9.x/{style}/svg?seed={username}"
        bio = BOT_BIOS[i % len(BOT_BIOS)]

        email = f"{username.lower()}@crypto-bot.internal"
        password = str(uuid.uuid4()) + "Abc123!"

        try:
            # 通过 Auth Admin API 创建用户（自动触发 profiles 表插入）
            auth_resp = client.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"username": username},
            })

            if not auth_resp.user:
                logger.error(f"[失败] {username}: create_user 返回空")
                continue

            user_id = auth_resp.user.id
            logger.info(f"[Auth] 创建用户 {username}, id={user_id[:8]}...")

            # 更新 profiles 记录（auth 触发已插入基础记录，现在补全字段）
            client.table("profiles").update({
                "username": username,
                "avatar": avatar_url,
                "bio": bio,
                "role": "user",
                "level": "Rookie",
                "is_bot": True,
                "posts_count": 0,
                "comments_count": 0,
                "received_likes_count": 0,
                "followers_count": 0,
                "following_count": 0,
            }).eq("id", user_id).execute()

            created += 1
            logger.info(f"[创建] {username} (头像: {style})")
        except Exception as e:
            logger.error(f"[失败] {username}: {e}")

    logger.info(f"完成! 创建 {created} 个, 跳过 {skipped} 个")


if __name__ == "__main__":
    main()
