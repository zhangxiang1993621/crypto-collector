"""Supabase 客户端工厂

统一管理 Supabase 连接，绕过本地代理环境变量，
避免 httpx 通过代理连接导致 SSL 错误：
  httpcore.ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]
"""

import os
import sys
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from dotenv import load_dotenv
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 只在首次导入时加载 .env
_load_done = False


def _ensure_env():
    global _load_done
    if not _load_done:
        load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
        _load_done = True


def get_client() -> "Client":
    """获取绕过代理的 Supabase 客户端

    自动加载 .env，读取 SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY，
    创建 trust_env=False 的 httpx 客户端避免本地代理干扰 SSL 连接。
    """
    _ensure_env()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 环境变量")
        sys.exit(1)

    # trust_env=True → 读取系统代理/环境变量代理（本机需要代理访问外网）
    # timeout=60 → 代理环境网络波动较大，避免 ReadTimeout
    http_client = httpx.Client(trust_env=True, timeout=60)
    return create_client(url, key, options=SyncClientOptions(httpx_client=http_client))
