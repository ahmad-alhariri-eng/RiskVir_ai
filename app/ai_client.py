"""
OrcaRouter AI client — Redis-history edition.
  - Uses Redis for persistent conversation history
  - Shared connection pool across all workers
"""
import asyncio
from typing import Any

from openai import AsyncOpenAI

from app import history as hist
from app.config import (
    AI_MAX_TOKENS,
    AI_MODEL,
    AI_SYSTEM_PROMPT,
    ORCAROUTER_API_KEY,
    ORCAROUTER_BASE_URL,
)
from app.logger import get_logger

log = get_logger(__name__)

# Single shared client — AsyncOpenAI manages its own connection pool internally
_client = AsyncOpenAI(
    base_url=ORCAROUTER_BASE_URL,
    api_key=ORCAROUTER_API_KEY,
    max_retries=2,
    timeout=30.0,
)


async def chat(user_id: int, user_message: str) -> str:
    """
    1. Append user message to Redis history
    2. Build messages list
    3. Call OrcaRouter
    4. Append assistant reply to Redis history
    5. Return reply text
    """
    # Save user message first
    await hist.append_message(user_id, "user", user_message)

    # Build full context
    history = await hist.get_history(user_id)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        *history,
    ]

    log.info("AI request | user=%s | model=%s | ctx=%d msgs",
             user_id, AI_MODEL, len(messages))

    try:
        response = await _client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            max_tokens=AI_MAX_TOKENS,
        )
        reply = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else "?"
        log.info("AI reply | user=%s | tokens=%s", user_id, tokens)
    except Exception as exc:
        log.error("OrcaRouter error | user=%s | %s", user_id, exc)
        raise

    # Save assistant reply
    await hist.append_message(user_id, "assistant", reply)
    return reply
