"""
Redis connection manager.
Single async connection pool shared across the entire app.
"""
import redis.asyncio as aioredis
from app.config import REDIS_URL
from app.logger import get_logger

log = get_logger(__name__)

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=100,          # pool size — handles 5000 req/s easily
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        log.info("Redis pool created: %s", REDIS_URL)
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
        log.info("Redis pool closed")
