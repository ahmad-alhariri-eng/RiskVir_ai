"""
AI Worker — reads from Redis Stream and processes updates.
Supports: text, photos (vision), voice/audio (Whisper), documents (PDF/text).

NOTE: Bot is initialized once in main.py lifespan and shared here.
"""
import asyncio
import signal

from telegram import Update
from telegram.constants import ChatAction

from app import ai_client
from app.bot_instance import bot as _bot
from app.config import TELEGRAM_TOKEN, RATE_LIMIT_PER_MINUTE
from app.logger import get_logger, setup_logging
from app.queue import ack, ensure_consumer_group, read_batch
from app.rate_limiter import is_rate_limited
from app.redis_client import close_redis
from app import history as hist
from app.media import (
    download_file, image_to_data_url,
    transcribe_audio, extract_document_text,
)

setup_logging()
log = get_logger(__name__)

_running = True


def _handle_signal(sig, frame):
    global _running
    log.info("Worker shutting down (signal %s)...", sig)
    _running = False


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _send(chat_id: int, text: str, **kwargs) -> None:
    chunks = [text[i: i + 4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        await _bot.send_message(chat_id, chunk, **kwargs)


async def _typing(chat_id: int) -> None:
    await _bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def _handle_text(chat_id: int, user_id: int, text: str) -> None:
    await _typing(chat_id)
    reply = await ai_client.chat(user_id, text)
    await _send(chat_id, reply)


async def _handle_photo(chat_id: int, user_id: int, photo_sizes, caption: str) -> None:
    await _typing(chat_id)
    file_id = photo_sizes[-1].file_id
    try:
        data = await download_file(_bot, file_id)
        data_url = image_to_data_url(data, "image/jpeg")
        prompt = caption or "ما الذي تراه في هذه الصورة؟ صف بالتفصيل."
        reply = await ai_client.chat_with_image(user_id, prompt, data_url)
        await _send(chat_id, reply)
    except ValueError as e:
        await _send(chat_id, f"⚠️ {e}")
    except Exception as e:
        log.error("Photo handler error: %s", e)
        await _send(chat_id, "❌ تعذّر معالجة الصورة، حاول مرة أخرى.")


async def _handle_voice(chat_id: int, user_id: int, file_id: str) -> None:
    await _typing(chat_id)
    try:
        data = await download_file(_bot, file_id)
        client = ai_client.get_client()
        transcription = await transcribe_audio(data, "voice.ogg", client)

        if transcription:
            await _send(chat_id, f"🎤 *نص الصوت:* _{transcription}_", parse_mode="Markdown")
            reply = await ai_client.chat(user_id, transcription)
            await _send(chat_id, reply)
        else:
            await _send(chat_id,
                "⚠️ لم أتمكن من تحويل الصوت لنص. "
                "أرسل رسالة نصية بدلاً من ذلك.")
    except ValueError as e:
        await _send(chat_id, f"⚠️ {e}")
    except Exception as e:
        log.error("Voice handler error: %s", e)
        await _send(chat_id, "❌ تعذّر معالجة الصوت.")


async def _handle_document(chat_id: int, user_id: int, document, caption: str) -> None:
    await _typing(chat_id)
    filename = document.file_name or "file"
    try:
        data = await download_file(_bot, document.file_id)
        text_content = extract_document_text(data, filename)

        if text_content is None:
            await _send(chat_id,
                f"⚠️ نوع الملف `{filename}` غير مدعوم.\n"
                "المدعوم: PDF, TXT, MD, CSV, JSON, XML, YAML, وملفات الكود.",
                parse_mode="Markdown")
            return

        user_prompt = (
            f"{caption}\n\n" if caption else "حلّل محتوى الملف التالي:\n\n"
        ) + f"```\n{text_content}\n```"

        reply = await ai_client.chat(user_id, user_prompt)
        await _send(chat_id, reply)

    except ValueError as e:
        await _send(chat_id, f"⚠️ {e}")
    except Exception as e:
        log.error("Document handler error | file=%s | %s", filename, e)
        await _send(chat_id, "❌ تعذّر قراءة الملف.")


# ── Main dispatcher ───────────────────────────────────────────────────────────

async def _process_update(update_data: dict) -> None:
    try:
        update = Update.de_json(update_data, _bot)
    except Exception as e:
        log.error("Failed to parse update: %s", e)
        return

    msg = update.message
    if not msg:
        return

    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    caption = (msg.caption or "").strip()
    text = (msg.text or "").strip()

    # ── Commands ──────────────────────────────────────────────────────────────
    if text.startswith("/start"):
        await _send(chat_id,
            f"👋 مرحباً {user.first_name}!\n\n"
            "أنا مساعد ذكاء اصطناعي. يمكنك إرسال:\n"
            "💬 *رسالة نصية* — للدردشة\n"
            "📷 *صورة* — لوصفها وتحليلها\n"
            "🎤 *رسالة صوتية* — سأحوّلها لنص وأجيب\n"
            "📄 *ملف* — PDF, TXT, CSV, JSON...\n\n"
            "📌 الأوامر: /start · /clear · /help",
            parse_mode="Markdown")
        return

    if text.startswith("/clear"):
        await hist.clear_history(user_id)
        await _send(chat_id, "✅ تم مسح سجل المحادثة!")
        return

    if text.startswith("/help"):
        await _send(chat_id,
            f"🤖 *RiskVir AI Bot*\n\n"
            f"• يتذكر آخر *20* رسالة (24 ساعة)\n"
            f"• الحد الأقصى: *{RATE_LIMIT_PER_MINUTE}* رسائل/دقيقة\n"
            f"• يدعم: نص، صور، صوت، PDF، ملفات نصية",
            parse_mode="Markdown")
        return

    # ── Rate limiting ──────────────────────────────────────────────────────────
    if await is_rate_limited(user_id):
        await _send(chat_id,
            f"⚠️ تجاوزت الحد المسموح ({RATE_LIMIT_PER_MINUTE} رسائل/دقيقة). انتظر قليلاً.")
        return

    log.info("Processing | user=%s | type=%s", user_id,
             "photo" if msg.photo else "voice" if msg.voice else
             "audio" if msg.audio else "doc" if msg.document else "text")

    try:
        if msg.photo:
            await _handle_photo(chat_id, user_id, msg.photo, caption)
        elif msg.voice:
            await _handle_voice(chat_id, user_id, msg.voice.file_id)
        elif msg.audio:
            await _handle_voice(chat_id, user_id, msg.audio.file_id)
        elif msg.document:
            await _handle_document(chat_id, user_id, msg.document, caption)
        elif text:
            await _handle_text(chat_id, user_id, text)
        else:
            await _send(chat_id, "⚠️ هذا النوع من المحتوى غير مدعوم حالياً.")
    except Exception as exc:
        log.error("Unhandled error | user=%s | %s", user_id, exc)
        await _send(chat_id, "❌ حدث خطأ. يرجى المحاولة مرة أخرى.")


# ── Worker loop ───────────────────────────────────────────────────────────────

async def run_worker(worker_id: int) -> None:
    """Worker loop — no Bot lifecycle management here (handled by main.py lifespan)."""
    log.info("Worker %d starting...", worker_id)
    await ensure_consumer_group()
    log.info("Worker %d ready. Waiting for messages...", worker_id)

    while _running:
        try:
            batch = await read_batch(worker_id)
            if not batch:
                continue
            tasks = [_process_and_ack(msg_id, data) for msg_id, data in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Worker %d loop error: %s", worker_id, e)
            await asyncio.sleep(1)

    log.info("Worker %d stopped.", worker_id)


async def _process_and_ack(msg_id: str, update_data: dict) -> None:
    try:
        await _process_update(update_data)
    except Exception as e:
        log.error("Worker error for msg %s: %s", msg_id, e)
    finally:
        await ack(msg_id)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, default=0)
    args = parser.parse_args()

    async def _main():
        async with _bot:
            await run_worker(args.id)

    asyncio.run(_main())
