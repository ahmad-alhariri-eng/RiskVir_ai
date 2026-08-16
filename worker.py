"""
AI Worker — reads from Redis Stream and processes updates.

Can run as many instances as needed:
    python worker.py --id 0
    python worker.py --id 1
    python worker.py --id 2
    ...

Each worker is fully independent and stateless.
"""
import asyncio
import argparse
import signal

from telegram import Bot, Update
from telegram.constants import ChatAction

from app import ai_client
from app.config import TELEGRAM_TOKEN, RATE_LIMIT_PER_MINUTE
from app.logger import get_logger, setup_logging
from app.queue import ack, ensure_consumer_group, read_batch
from app.rate_limiter import is_rate_limited
from app.redis_client import close_redis
from app import history as hist

setup_logging()
log = get_logger(__name__)

# Shared Telegram bot instance (just for sending replies)
_bot = Bot(token=TELEGRAM_TOKEN)

_running = True


def _handle_signal(sig, frame):
    global _running
    log.info("Worker shutting down (signal %s)...", sig)
    _running = False


async def _process_update(update_data: dict) -> None:
    """Process a single Telegram update dict."""
    try:
        update = Update.de_json(update_data, _bot)
    except Exception as e:
        log.error("Failed to parse update: %s", e)
        return

    # Only handle text messages
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # ── Commands ────────────────────────────────────────────────────────────
    if text.startswith("/start"):
        await _bot.send_message(
            chat_id,
            f"👋 مرحباً {user.first_name}!\n\n"
            "أنا مساعد ذكاء اصطناعي. أرسل لي أي رسالة وسأرد عليك.\n\n"
            "📌 الأوامر:\n• /start — هذه الرسالة\n• /clear — مسح المحادثة\n• /help — مساعدة",
        )
        return

    if text.startswith("/clear"):
        await hist.clear_history(user_id)
        await _bot.send_message(chat_id, "✅ تم مسح سجل المحادثة!")
        return

    if text.startswith("/help"):
        await _bot.send_message(
            chat_id,
            f"🤖 *مساعد الذكاء الاصطناعي*\n\n"
            f"• يتذكر آخر *20* رسالة.\n"
            f"• الحد الأقصى: *{RATE_LIMIT_PER_MINUTE}* رسائل/دقيقة.",
            parse_mode="Markdown",
        )
        return

    # ── Rate limiting ────────────────────────────────────────────────────────
    if await is_rate_limited(user_id):
        await _bot.send_message(
            chat_id,
            f"⚠️ تجاوزت الحد المسموح ({RATE_LIMIT_PER_MINUTE} رسائل/دقيقة). انتظر قليلاً.",
        )
        return

    log.info("Processing | user=%s (%s) | len=%d", user_id, user.username, len(text))

    # Show typing indicator
    await _bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        reply = await ai_client.chat(user_id, text)
        # Split if over 4096 chars (Telegram limit)
        chunks = [reply[i: i + 4096] for i in range(0, len(reply), 4096)]
        for chunk in chunks:
            await _bot.send_message(chat_id, chunk)

    except Exception as exc:
        log.error("Worker error | user=%s | %s", user_id, exc)
        await _bot.send_message(
            chat_id, "❌ حدث خطأ. يرجى المحاولة مرة أخرى."
        )


async def run_worker(worker_id: int) -> None:
    """Main worker loop: read → process → ack."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("Worker %d starting...", worker_id)
    await ensure_consumer_group()
    log.info("Worker %d ready. Waiting for messages...", worker_id)

    async with _bot:
        while _running:
            batch = await read_batch(worker_id)

            if not batch:
                continue   # timeout, loop again

            # Process all messages in batch concurrently
            tasks = []
            for msg_id, update_data in batch:
                tasks.append(_process_and_ack(msg_id, update_data))

            await asyncio.gather(*tasks, return_exceptions=True)

    await close_redis()
    log.info("Worker %d stopped.", worker_id)


async def _process_and_ack(msg_id: str, update_data: dict) -> None:
    """Process one message then ack it regardless of outcome."""
    try:
        await _process_update(update_data)
    except Exception as e:
        log.error("Unhandled error in worker for msg %s: %s", msg_id, e)
    finally:
        await ack(msg_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Bot Worker")
    parser.add_argument("--id", type=int, default=0, help="Worker ID (unique per instance)")
    args = parser.parse_args()
    asyncio.run(run_worker(args.id))
