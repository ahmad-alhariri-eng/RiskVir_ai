"""
Redis-backed sliding-window rate limiter using atomic Lua script.
  - Works correctly across multiple worker processes (unlike in-memory)
  - O(1) per check thanks to Lua atomic execution
"""
from app.config import RATE_LIMIT_PER_MINUTE
from app.redis_client import get_redis
from app.logger import get_logger
import time

log = get_logger(__name__)

_KEY_PREFIX = "ratelimit:"
WINDOW_SECONDS = 60

# Atomic Lua script: count requests in sliding window, add current timestamp
_LUA_SCRIPT = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now, now)
    redis.call('EXPIRE', key, window)
    return 0   -- NOT limited
else
    return 1   -- LIMITED
end
"""


async def is_rate_limited(user_id: int) -> bool:
    """
    Returns True if the user exceeded RATE_LIMIT_PER_MINUTE requests/minute.
    Atomic and safe across multiple workers.
    """
    r = await get_redis()
    key = f"{_KEY_PREFIX}{user_id}"
    now_ms = int(time.time() * 1000)

    result = await r.eval(
        _LUA_SCRIPT,
        1,
        key,
        now_ms,
        WINDOW_SECONDS * 1000,
        RATE_LIMIT_PER_MINUTE,
    )

    if result == 1:
        log.warning("Rate limit hit | user=%s", user_id)
        return True
    return False
