"""
Redis-backed conversation history.
  - Persistent across restarts (unlike in-memory LRU)
  - Shared across all worker instances
  - Auto-expires after HISTORY_TTL_HOURS of inactivity
"""
import json
from app.config import HISTORY_MAX_MESSAGES, HISTORY_TTL_HOURS
from app.redis_client import get_redis
from app.logger import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "chat:history:"


def _key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{user_id}"


async def get_history(user_id: int) -> list[dict]:
    """Return the conversation history for a user as a list of messages."""
    r = await get_redis()
    raw = await r.lrange(_key(user_id), 0, -1)
    return [json.loads(m) for m in raw]


async def append_message(user_id: int, role: str, content: str) -> None:
    """Append a message and trim to the max allowed length."""
    r = await get_redis()
    key = _key(user_id)
    pipe = r.pipeline()
    pipe.rpush(key, json.dumps({"role": role, "content": content}))
    pipe.ltrim(key, -HISTORY_MAX_MESSAGES, -1)   # keep last N messages
    pipe.expire(key, HISTORY_TTL_HOURS * 3600)   # reset TTL on activity
    await pipe.execute()


async def clear_history(user_id: int) -> None:
    """Delete all history for a user."""
    r = await get_redis()
    await r.delete(_key(user_id))
    log.debug("History cleared for user %s", user_id)
