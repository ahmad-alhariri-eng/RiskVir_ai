"""
Redis Streams-based message queue.

Producer (webhook):
  - Pushes incoming Telegram updates to the stream instantly (<1ms)
  - Returns 200 to Telegram immediately

Consumer (AI worker):
  - Reads from the stream in batches
  - Calls OrcaRouter and sends reply back via Telegram
  - Acknowledges messages after processing

Why Redis Streams?
  - At-least-once delivery (if worker crashes, message is re-delivered)
  - Multiple consumer groups → horizontal scaling
  - No lost messages
"""
import json
from app.redis_client import get_redis
from app.logger import get_logger

log = get_logger(__name__)

STREAM_KEY = "bot:updates"
CONSUMER_GROUP = "ai-workers"
CONSUMER_NAME_PREFIX = "worker"

# How long (ms) to block waiting for new messages
BLOCK_MS = 2000
# Max messages to read per batch
BATCH_SIZE = 50


# ── Producer ────────────────────────────────────────────────────────────────

async def enqueue(update_data: dict) -> str:
    """Push a Telegram update dict to the stream. Returns the message ID."""
    r = await get_redis()
    msg_id = await r.xadd(
        STREAM_KEY,
        {"payload": json.dumps(update_data)},
        maxlen=50_000,   # cap stream size (auto-trim oldest)
        approximate=True,
    )
    log.debug("Enqueued update | stream_id=%s", msg_id)
    return msg_id


# ── Consumer group bootstrap ─────────────────────────────────────────────────

async def ensure_consumer_group() -> None:
    """Create the consumer group if it doesn't exist yet."""
    r = await get_redis()
    try:
        await r.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        log.info("Consumer group '%s' created on stream '%s'", CONSUMER_GROUP, STREAM_KEY)
    except Exception as e:
        if "BUSYGROUP" in str(e):
            pass  # already exists — fine
        else:
            raise


# ── Consumer ─────────────────────────────────────────────────────────────────

async def read_batch(worker_id: int) -> list[tuple[str, dict]]:
    """
    Block-read up to BATCH_SIZE messages from the stream.
    Returns list of (message_id, update_data).
    """
    r = await get_redis()
    consumer_name = f"{CONSUMER_NAME_PREFIX}-{worker_id}"
    results = await r.xreadgroup(
        CONSUMER_GROUP,
        consumer_name,
        {STREAM_KEY: ">"},   # ">" = only new messages
        count=BATCH_SIZE,
        block=BLOCK_MS,
    )
    if not results:
        return []
    messages = []
    for _stream, entries in results:
        for msg_id, fields in entries:
            try:
                data = json.loads(fields["payload"])
                messages.append((msg_id, data))
            except Exception as e:
                log.error("Failed to parse queued message %s: %s", msg_id, e)
                await ack(msg_id)   # discard malformed
    return messages


async def ack(msg_id: str) -> None:
    """Acknowledge a processed message (removes from PEL)."""
    r = await get_redis()
    await r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)


async def stream_len() -> int:
    """Return current queue depth (useful for monitoring)."""
    r = await get_redis()
    return await r.xlen(STREAM_KEY)
