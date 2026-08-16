"""
FastAPI webhook server — production entry point.

Architecture:
  Telegram → POST /webhook → enqueue to Redis Stream → return 200
  Redis Stream → AI Worker(s) → call OrcaRouter → send Telegram reply

This separation means:
  - Webhook returns in <1ms (just a Redis write)
  - Workers scale independently (add more workers = more throughput)
  - No lost messages even if a worker crashes (Redis Streams at-least-once)
"""
import asyncio
import json

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from telegram import Bot, Update

from app.config import (
    HOST, PORT,
    TELEGRAM_TOKEN,
    WEBHOOK_SECRET, WEBHOOK_URL,
    WORKER_COUNT,
)
from app.logger import get_logger, setup_logging
from app.queue import enqueue, ensure_consumer_group
from app.redis_client import close_redis, get_redis
from app.queue import stream_len

setup_logging()
log = get_logger(__name__)

_WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}" if WEBHOOK_SECRET else "/webhook"

_bot = Bot(token=TELEGRAM_TOKEN)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify Redis connection
    r = await get_redis()
    await r.ping()
    log.info("Redis connected ✓")

    await ensure_consumer_group()

    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip("/") + _WEBHOOK_PATH
        async with _bot:
            await _bot.delete_webhook(drop_pending_updates=True)
            await _bot.set_webhook(
                url=full_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
        log.info("Webhook set: %s", full_url)
    else:
        log.warning("No WEBHOOK_URL — set it in .env for production webhook mode.")

    log.info("API server ready on %s%s", WEBHOOK_URL or "localhost", _WEBHOOK_PATH)
    yield

    await close_redis()
    log.info("Shutdown complete.")


# ── FastAPI ───────────────────────────────────────────────────────────────────
api = FastAPI(
    title="Telegram AI Bot",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@api.get("/health")
async def health():
    """Health check + queue depth for monitoring."""
    r = await get_redis()
    await r.ping()
    depth = await stream_len()
    return {"status": "ok", "queue_depth": depth}


@api.post(_WEBHOOK_PATH, status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request) -> Response:
    """
    Receive Telegram update → push to Redis Stream → return 200 immediately.
    Processing happens asynchronously in worker.py.
    """
    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    await enqueue(data)
    return Response(status_code=status.HTTP_200_OK)


# ── Local dev: polling + workers in one process ───────────────────────────────
if __name__ == "__main__":
    import uvicorn
    from worker import run_worker
    from telegram.ext import Application, CommandHandler, MessageHandler, filters
    from app.handlers import build_application

    if not WEBHOOK_URL:
        log.info("No WEBHOOK_URL — starting in POLLING + WORKER mode (dev)")

        async def _run_all():
            setup_logging()

            # Bootstrap Redis stream
            r = await get_redis()
            await r.ping()
            await ensure_consumer_group()
            log.info("Redis connected ✓")

            # Build polling bot (enqueues to Redis instead of processing directly)
            tg_app = build_application(TELEGRAM_TOKEN)
            await tg_app.initialize()
            await tg_app.bot.delete_webhook(drop_pending_updates=True)
            await tg_app.start()
            await tg_app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            log.info("Polling started ✓")

            # Spawn WORKER_COUNT AI workers
            worker_tasks = [
                asyncio.create_task(run_worker(i))
                for i in range(WORKER_COUNT)
            ]
            log.info("%d AI workers started ✓", WORKER_COUNT)
            log.info("Bot running. Press Ctrl+C to stop.")

            try:
                await asyncio.gather(*worker_tasks)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                for t in worker_tasks:
                    t.cancel()
                await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()
                await close_redis()

        asyncio.run(_run_all())

    else:
        uvicorn.run(
            "main:api",
            host=HOST,
            port=PORT,
            workers=4,
            loop="asyncio",
            access_log=False,
        )
