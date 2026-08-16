"""
FastAPI Telegram Bot — Simple & Reliable Architecture

Telegram → POST /webhook → asyncio.create_task(process) → reply
Redis used ONLY for: history + rate limiting (no queue/stream complexity)
"""
import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from telegram import Bot, Update
from telegram.constants import ChatAction

from app import ai_client
from app import history as hist
from app.config import (
    HOST, PORT, TELEGRAM_TOKEN,
    WEBHOOK_SECRET, WEBHOOK_URL, WORKER_COUNT,
    RATE_LIMIT_PER_MINUTE,
)
from app.logger import get_logger, setup_logging
from app.rate_limiter import is_rate_limited
from app.redis_client import close_redis, get_redis
from app.media import (
    download_file, image_to_data_url,
    transcribe_audio, extract_document_text,
)

setup_logging()
log = get_logger(__name__)

_WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}" if WEBHOOK_SECRET else "/webhook"

# Single Bot instance
_bot = Bot(token=TELEGRAM_TOKEN)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Redis health check
    r = await get_redis()
    await r.ping()
    log.info("Redis connected ✓")

    # Initialize bot
    await _bot.initialize()
    log.info("Bot initialized ✓")

    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip("/") + _WEBHOOK_PATH
        await _bot.delete_webhook(drop_pending_updates=True)
        await _bot.set_webhook(url=full_url, allowed_updates=Update.ALL_TYPES)
        log.info("Webhook set: %s", full_url)
    else:
        log.warning("No WEBHOOK_URL — messages won't arrive via webhook.")

    log.info("Server ready.")
    yield

    await _bot.shutdown()
    await close_redis()
    log.info("Shutdown complete.")


api = FastAPI(title="RiskVir AI Bot", version="3.0.0",
              docs_url=None, redoc_url=None, lifespan=lifespan)


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _send(chat_id: int, text: str, **kwargs):
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await _bot.send_message(chat_id, chunk, **kwargs)


async def _typing(chat_id: int):
    await _bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)


# ── Message Handlers ──────────────────────────────────────────────────────────
async def handle_text(chat_id, user_id, text):
    await _typing(chat_id)
    reply = await ai_client.chat(user_id, text)
    await _send(chat_id, reply)


async def handle_photo(chat_id, user_id, photo_sizes, caption):
    await _typing(chat_id)
    data = await download_file(_bot, photo_sizes[-1].file_id)
    data_url = image_to_data_url(data, "image/jpeg")
    prompt = caption or "ما الذي تراه في هذه الصورة؟ صف بالتفصيل."
    reply = await ai_client.chat_with_image(user_id, prompt, data_url)
    await _send(chat_id, reply)


async def handle_voice(chat_id, user_id, file_id):
    await _typing(chat_id)
    data = await download_file(_bot, file_id)
    transcription = await transcribe_audio(data, "voice.ogg", ai_client.get_client())
    if transcription:
        await _send(chat_id, f"🎤 _{transcription}_", parse_mode="Markdown")
        reply = await ai_client.chat(user_id, transcription)
        await _send(chat_id, reply)
    else:
        await _send(chat_id, "⚠️ لم أتمكن من تحويل الصوت. أرسل رسالة نصية.")


async def handle_document(chat_id, user_id, document, caption):
    await _typing(chat_id)
    filename = document.file_name or "file"
    data = await download_file(_bot, document.file_id)
    content = extract_document_text(data, filename)
    if content is None:
        await _send(chat_id, f"⚠️ نوع الملف `{filename}` غير مدعوم.\nالمدعوم: PDF, TXT, MD, CSV, JSON...",
                    parse_mode="Markdown")
        return
    prompt = f"{caption}\n\n```\n{content}\n```" if caption else f"حلّل:\n```\n{content}\n```"
    reply = await ai_client.chat(user_id, prompt)
    await _send(chat_id, reply)


# ── Main Update Processor ─────────────────────────────────────────────────────
async def process_update(update_data: dict):
    try:
        update = Update.de_json(update_data, _bot)
    except Exception as e:
        log.error("Parse error: %s", e)
        return

    msg = update.message
    if not msg:
        return

    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    text = (msg.text or "").strip()
    caption = (msg.caption or "").strip()

    # Commands
    if text == "/start":
        await _send(chat_id,
            f"👋 مرحباً {user.first_name}!\n\n"
            "💬 *نص* — دردشة\n"
            "📷 *صورة* — تحليل\n"
            "🎤 *صوت* — تحويل لنص والرد\n"
            "📄 *ملف* — PDF, TXT, CSV, JSON...\n\n"
            "الأوامر: /start · /clear · /help",
            parse_mode="Markdown")
        return

    if text == "/clear":
        await hist.clear_history(user_id)
        await _send(chat_id, "✅ تم مسح سجل المحادثة!")
        return

    if text == "/help":
        await _send(chat_id,
            f"🤖 *RiskVir AI*\n• يتذكر آخر 20 رسالة\n• حد: {RATE_LIMIT_PER_MINUTE} رسائل/دقيقة",
            parse_mode="Markdown")
        return

    # Rate limit
    if await is_rate_limited(user_id):
        await _send(chat_id, f"⚠️ تجاوزت الحد ({RATE_LIMIT_PER_MINUTE} رسائل/دقيقة).")
        return

    log.info("msg | user=%s | %s", user_id,
             "photo" if msg.photo else "voice" if msg.voice else
             "audio" if msg.audio else "doc" if msg.document else "text")

    try:
        if msg.photo:
            await handle_photo(chat_id, user_id, msg.photo, caption)
        elif msg.voice:
            await handle_voice(chat_id, user_id, msg.voice.file_id)
        elif msg.audio:
            await handle_voice(chat_id, user_id, msg.audio.file_id)
        elif msg.document:
            await handle_document(chat_id, user_id, msg.document, caption)
        elif text:
            await handle_text(chat_id, user_id, text)
        else:
            await _send(chat_id, "⚠️ هذا النوع غير مدعوم.")
    except Exception as e:
        log.error("Handler error | user=%s | %s", user_id, e)
        await _send(chat_id, "❌ حدث خطأ. حاول مجدداً.")


# ── Webhook endpoint ──────────────────────────────────────────────────────────
@api.get("/health")
async def health():
    r = await get_redis()
    await r.ping()
    return {"status": "ok"}


@api.post(_WEBHOOK_PATH, status_code=200)
async def webhook(request: Request) -> Response:
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        return Response(status_code=400)
    # Fire and forget — returns 200 immediately
    asyncio.create_task(process_update(data))
    return Response(status_code=200)


# ── Local polling mode ────────────────────────────────────────────────────────
if __name__ == "__main__":
    from telegram.ext import Application, MessageHandler, CommandHandler, filters

    async def _polling():
        r = await get_redis()
        await r.ping()

        app = Application.builder().token(TELEGRAM_TOKEN).build()

        async def on_update(update, context):
            await process_update(update.to_dict())

        app.add_handler(MessageHandler(filters.ALL, on_update))

        await app.initialize()
        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        log.info("Polling started ✓")

        # Also init shared bot
        await _bot.initialize()

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await _bot.shutdown()
            await close_redis()

    asyncio.run(_polling())
