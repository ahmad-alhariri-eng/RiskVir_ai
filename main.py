"""
FastAPI webhook server + embedded AI workers.

Bot is initialized ONCE in lifespan and shared across all workers via app.bot_instance.
"""
import asyncio
import json

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from telegram import Update

from app.bot_instance import bot as _bot
from app.config import (HOST, PORT, TELEGRAM_TOKEN, WEBHOOK_SECRET, WEBHOOK_URL, WORKER_COUNT)
from app.logger import get_logger, setup_logging
from app.queue import enqueue, ensure_consumer_group, stream_len
from app.redis_client import close_redis, get_redis

setup_logging()
log = get_logger(__name__)

_WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}" if WEBHOOK_SECRET else "/webhook"
_worker_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    from worker import run_worker

    # 1. Redis
    r = await get_redis()
    await r.ping()
    log.info("Redis connected ✓")
    await ensure_consumer_group()

    # 2. Initialize shared Bot (one time for all workers)
    await _bot.initialize()
    log.info("Bot initialized ✓")

    # 3. Set Telegram webhook
    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip("/") + _WEBHOOK_PATH
        await _bot.delete_webhook(drop_pending_updates=True)
        await _bot.set_webhook(
            url=full_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        log.info("Webhook set: %s", full_url)
    else:
        log.warning("No WEBHOOK_URL set.")

    # 4. Spawn AI workers
    for i in range(WORKER_COUNT):
        task = asyncio.create_task(run_worker(i), name=f"worker-{i}")
        _worker_tasks.append(task)
    log.info("%d AI workers started ✓", WORKER_COUNT)
    log.info("Server ready.")

    yield  # ── app runs here ──

    # Shutdown
    for task in _worker_tasks:
        task.cancel()
    await asyncio.gather(*_worker_tasks, return_exceptions=True)
    _worker_tasks.clear()

    await _bot.shutdown()
    await close_redis()
    log.info("Shutdown complete.")


api = FastAPI(
    title="RiskVir AI Bot",
    version="2.2.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@api.get("/health")
async def health():
    r = await get_redis()
    await r.ping()
    depth = await stream_len()
    return {"status": "ok", "queue_depth": depth, "workers": WORKER_COUNT}


@api.post(_WEBHOOK_PATH, status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request) -> Response:
    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    await enqueue(data)
    return Response(status_code=status.HTTP_200_OK)


# ── Local dev: polling + workers ──────────────────────────────────────────────
if __name__ == "__main__":
    from worker import run_worker

    if not WEBHOOK_URL:
        log.info("No WEBHOOK_URL — POLLING + WORKER mode")

        async def _run_all():
            r = await get_redis()
            await r.ping()
            await ensure_consumer_group()

            async with _bot:
                # Start polling
                from telegram.ext import Application, CommandHandler, MessageHandler, filters, Updater
                updater = _bot._get_updater()   # internal — use Application instead

            # Simpler: use Application for polling
            from telegram.ext import Application
            tg_app = (
                Application.builder()
                .token(TELEGRAM_TOKEN)
                .build()
            )

            await tg_app.initialize()
            await tg_app.bot.delete_webhook(drop_pending_updates=True)
            await tg_app.start()
            await tg_app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            log.info("Polling started ✓")

            # Also initialize shared bot for workers
            await _bot.initialize()

            worker_tasks = [
                asyncio.create_task(run_worker(i))
                for i in range(WORKER_COUNT)
            ]
            log.info("%d AI workers started ✓", WORKER_COUNT)
            log.info("Bot running. Ctrl+C to stop.")

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
                await _bot.shutdown()
                await close_redis()

        asyncio.run(_run_all())
    else:
        import uvicorn
        uvicorn.run("main:api", host=HOST, port=PORT, workers=1, loop="asyncio")
