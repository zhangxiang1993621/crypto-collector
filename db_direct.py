"""直连 PostgreSQL 数据库工具（绕过 REST API，避开 Supabase Free Plan 作业限制）

用法：
    from db_direct import get_connection, execute_sql, upsert_one, batch_upsert

    # 单条查询
    row = execute_sql("SELECT id, username FROM profiles WHERE username = %s", ('admin',))
    
    # 单条插入/更新
    upsert_one('posts', {'title': 'Hello', 'content': 'World'}, 'id')
    
    # 批量插入/更新
    batch_upsert('tokens', rows, 'coincap_id')

环境变量（优先级从高到低）：
    DATABASE_URL         - 完整连接字符串，如 postgresql://postgres:xxx@db.xxx.supabase.co:5432/postgres
    SUPABASE_DB_PASSWORD - 仅数据库密码（需要配合 SUPABASE_URL 使用）
    SUPABASE_URL         - 项目 URL（自动从中提取 host）
"""

import os
import logging
from typing import Any

from dotenv import load_dotenv
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根目录（db_direct.py 就在项目根目录下）
PROJECT_ROOT = Path(__file__).parent
_load_done = False


def _ensure_env():
    global _load_done
    if not _load_done:
        load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
        _load_done = True


def _parse_database_url(url: str) -> dict[str, Any]:
    """解析 postgresql://... 格式的连接字符串，自动 URL-decode 密码"""
    import re
    from urllib.parse import unquote
    # 匹配格式: postgresql://user:password@host:port/dbname
    # user 可含点号（如 postgres.xxx），dbname 可含连字符
    pattern = (
        r"postgresql://(?P<user>[^:]+):(?P<password>[^@]+)"
        r"@(?P<host>[^:]+):(?P<port>\d+)/(?P<dbname>[\w\-]+)"
    )
    match = re.match(pattern, url)
    if not match:
        raise ValueError(f"无效的 DATABASE_URL 格式: {url}")
    return {
        "host": match.group("host"),
        "port": int(match.group("port")),
        "dbname": match.group("dbname"),
        "user": match.group("user"),
        "password": unquote(match.group("password")),
        "sslmode": "require",
        "connect_timeout": 30,
    }


def _get_db_config() -> dict[str, Any]:
    """获取数据库连接配置"""
    _ensure_env()

    # 优先使用完整的 DATABASE_URL
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        return _parse_database_url(database_url)

    # 否则从 SUPABASE_URL + SUPABASE_DB_PASSWORD 构建
    url = os.environ.get("SUPABASE_URL", "")
    # https://uqlxbcoowpmxmvwkuomc.supabase.co → uqlxbcoowpmxmvwkuomc
    project_ref = url.replace("https://", "").replace(".supabase.co", "").strip("/") if url else ""

    password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not password:
        raise ValueError("缺少 DATABASE_URL 或 SUPABASE_DB_PASSWORD 环境变量")

    return {
        "host": f"db.{project_ref}.supabase.co",
        "port": 5432,
        "dbname": "postgres",
        "user": "postgres",
        "password": password,
        "sslmode": "require",
        "connect_timeout": 30,
    }


def get_connection(autocommit: bool = True):
    """获取直连 PostgreSQL 连接

    Args:
        autocommit: True 时关闭事务自动提交，适合 VACUUM 等需要独立执行的命令

    Returns:
        psycopg2.connection 对象
    """
    import psycopg2

    config = _get_db_config()
    logger.info(f"直连 PostgreSQL: {config['host']}:{config['port']}")

    conn = psycopg2.connect(**config)
    if autocommit:
        conn.autocommit = True  # 关键！关闭事务模式，VACUUM 等命令需要

    return conn


def execute_sql(sql: str, params: tuple | None = None, fetch: bool = True) -> list[dict] | None:
    """执行 SQL 并返回结果

    Args:
        sql: SQL 语句
        params: 参数（元组形式）
        fetch: 是否返回查询结果

    Returns:
        查询结果列表（每行一个 dict），或 None
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(sql, params)

        if fetch:
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        return None

    finally:
        cur.close()
        conn.close()


# ── 便捷函数 ──────────────────────────────────────────────

def count_table(table_name: str, schema: str = "public") -> int:
    """返回表行数"""
    rows = execute_sql(f'SELECT COUNT(*) AS cnt FROM "{schema}"."{table_name}"')
    return rows[0]["cnt"] if rows else 0


def vacuum_table(table_name: str, schema: str = "public", full: bool = True) -> None:
    """对表执行 VACUUM"""
    mode = "VACUUM FULL" if full else "VACUUM"
    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(f'{mode} (VERBOSE, ANALYZE) "{schema}"."{table_name}"')
        for notice in conn.notices:
            logger.info(f"  {notice.strip()}")
        logger.info(f"{mode} {schema}.{table_name} 完成 ✓")
    finally:
        cur.close()
        conn.close()


def truncate_table(table_name: str, schema: str = "public") -> None:
    """清空表（TRUNCATE，比 DELETE 快）"""
    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(f'TRUNCATE TABLE "{schema}"."{table_name}" RESTART IDENTITY CASCADE')
        logger.info(f"TRUNCATE {schema}.{table_name} 完成 ✓")
    finally:
        cur.close()
        conn.close()


def _build_conflict_clause(conflict_key: str) -> str:
    """构建 ON CONFLICT 子句，支持复合键（如 'symbol,bar_time'）"""
    columns = [c.strip() for c in conflict_key.split(",")]
    if len(columns) == 1:
        return f'("{columns[0]}")'
    else:
        col_list = ", ".join([f'"{c}"' for c in columns])
        return f"({col_list})"


def upsert_one(table_name: str, data: dict, conflict_key: str, schema: str = "public") -> str | None:
    """单条 upsert，返回插入/更新行的主键值
    
    Args:
        table_name: 表名
        data: 要插入/更新的数据（dict）
        conflict_key: 冲突检测列名（如 'id', 'title'），复合键用逗号分隔（如 'symbol,bar_time'）
        schema: schema 名，默认 public
        
    Returns:
        主键值，或 None
    """
    columns = list(data.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join([f'"{c}"' for c in columns])
    update_list = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in columns])
    conflict_clause = _build_conflict_clause(conflict_key)
    
    sql = f'''
        INSERT INTO "{schema}"."{table_name}" ({col_list})
        VALUES ({placeholders})
        ON CONFLICT {conflict_clause} DO UPDATE SET {update_list}
        RETURNING "{conflict_key}"
    '''
    params = tuple(data[c] for c in columns)
    
    result = execute_sql(sql, params)
    return result[0][conflict_key] if result else None


def batch_upsert(table_name: str, rows: list[dict], conflict_key: str, schema: str = "public") -> int:
    """批量 upsert，返回处理的总行数
    
    Args:
        table_name: 表名
        rows: 要插入/更新的数据列表
        conflict_key: 冲突检测列名，复合键用逗号分隔（如 'symbol,bar_time'）
        schema: schema 名，默认 public
        
    Returns:
        处理的总行数
    """
    if not rows:
        return 0
    
    # 获取所有列名（使用第一个 rows 的 keys）
    columns = list(rows[0].keys())
    col_list = ", ".join([f'"{c}"' for c in columns])
    placeholders = ", ".join(["%s"] * len(columns))
    update_list = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in columns])
    conflict_clause = _build_conflict_clause(conflict_key)
    
    sql = f'''
        INSERT INTO "{schema}"."{table_name}" ({col_list})
        VALUES ({placeholders})
        ON CONFLICT {conflict_clause} DO UPDATE SET {update_list}
    '''
    
    conn = get_connection(autocommit=False)  # 使用事务
    cur = conn.cursor()
    total = 0
    try:
        for row in rows:
            params = tuple(row.get(c) for c in columns)
            cur.execute(sql, params)
            total += cur.rowcount
        conn.commit()
        logger.info(f"batch_upsert {table_name}: {total} 行 ✓")
        return total
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def select_one(table_name: str, where: dict, columns: str = "*", schema: str = "public") -> dict | None:
    """单行查询（等价于 Supabase .select().eq()）
    
    Args:
        table_name: 表名
        where: WHERE 条件字典，如 {"username": "admin", "status": "active"}
        columns: 要查询的列，默认 "*"
        schema: schema 名，默认 public
        
    Returns:
        单行数据 dict，或 None
    """
    if isinstance(columns, list):
        col_list = ", ".join([f'"{c}"' for c in columns])
    else:
        col_list = columns
    
    where_clause = " AND ".join([f'"{k}" = %s' for k in where.keys()])
    sql = f'SELECT {col_list} FROM "{schema}"."{table_name}" WHERE {where_clause} LIMIT 1'
    params = tuple(where.values())
    
    result = execute_sql(sql, params)
    return result[0] if result else None


def select_all(table_name: str, where: dict | None = None, columns: str = "*", 
               schema: str = "public", limit: int | None = None) -> list[dict]:
    """多行查询（等价于 Supabase .select().eq()）
    
    Args:
        table_name: 表名
        where: WHERE 条件字典，如 {"category": "news"}，None 表示无条件
        columns: 要查询的列，默认 "*"
        schema: schema 名，默认 public
        limit: 返回行数限制
        
    Returns:
        行的字典列表
    """
    if isinstance(columns, list):
        col_list = ", ".join([f'"{c}"' for c in columns])
    else:
        col_list = columns
    
    if where:
        where_clause = " AND ".join([f'"{k}" = %s' for k in where.keys()])
        sql = f'SELECT {col_list} FROM "{schema}"."{table_name}" WHERE {where_clause}'
        params = tuple(where.values())
    else:
        sql = f'SELECT {col_list} FROM "{schema}"."{table_name}"'
        params = None
    
    if limit:
        sql += f" LIMIT {limit}"
    
    if params:
        result = execute_sql(sql, params)
    else:
        result = execute_sql(sql)
    return result if result else []


def insert_one(table_name: str, data: dict, returning: str = "*", schema: str = "public") -> dict | None:
    """单行插入（等价于 Supabase .insert().execute()）
    
    Args:
        table_name: 表名
        data: 要插入的数据
        returning: 返回的列，默认 "*"
        schema: schema 名，默认 public
        
    Returns:
        插入行的数据（包含 RETURNING 列），或 None
    """
    columns = list(data.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join([f'"{c}"' for c in columns])
    
    sql = f'INSERT INTO "{schema}"."{table_name}" ({col_list}) VALUES ({placeholders}) RETURNING {returning}'
    params = tuple(data[c] for c in columns)
    
    result = execute_sql(sql, params)
    return result[0] if result else None


def insert_many(table_name: str, rows: list[dict], schema: str = "public") -> int:
    """批量插入（等价于 Supabase .insert([...]).execute()）
    
    Args:
        table_name: 表名
        rows: 要插入的数据列表
        schema: schema 名，默认 public
        
    Returns:
        插入的行数
    """
    if not rows:
        return 0
    
    columns = list(rows[0].keys())
    col_list = ", ".join([f'"{c}"' for c in columns])
    placeholders = ", ".join(["%s"] * len(columns))
    
    sql = f'INSERT INTO "{schema}"."{table_name}" ({col_list}) VALUES ({placeholders})'
    
    conn = get_connection(autocommit=False)
    cur = conn.cursor()
    total = 0
    try:
        for row in rows:
            params = tuple(row.get(c) for c in columns)
            cur.execute(sql, params)
            total += cur.rowcount
        conn.commit()
        return total
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def update_one(table_name: str, data: dict, where: dict, schema: str = "public") -> int:
    """单行更新（等价于 Supabase .update().eq().execute()）
    
    Args:
        table_name: 表名
        data: 要更新的数据
        where: WHERE 条件字典
        schema: schema 名，默认 public
        
    Returns:
        更新的行数
    """
    set_clause = ", ".join([f'"{k}" = %s' for k in data.keys()])
    where_clause = " AND ".join([f'"{k}" = %s' for k in where.keys()])
    
    sql = f'UPDATE "{schema}"."{table_name}" SET {set_clause} WHERE {where_clause}'
    params = tuple(list(data.values()) + list(where.values()))
    
    result = execute_sql(sql, params, fetch=False)
    return result if result is not None else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # 测试连接
    conn = get_connection()
    print("直连数据库成功 ✓")
    conn.close()
