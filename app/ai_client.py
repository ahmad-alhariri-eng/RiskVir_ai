"""
OrcaRouter AI client — supports text, vision (images), and audio transcription.
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

# Single shared client — manages its own connection pool
_client = AsyncOpenAI(
    base_url=ORCAROUTER_BASE_URL,
    api_key=ORCAROUTER_API_KEY,
    max_retries=2,
    timeout=60.0,
)


def get_client() -> AsyncOpenAI:
    """Return the shared OpenAI-compatible client (used by media transcription too)."""
    return _client


async def chat(user_id: int, user_message: str) -> str:
    """Standard text chat."""
    await hist.append_message(user_id, "user", user_message)
    history = await hist.get_history(user_id)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        *history,
    ]
    reply = await _call(user_id, messages)
    await hist.append_message(user_id, "assistant", reply)
    return reply


async def chat_with_image(user_id: int, caption: str, image_data_url: str) -> str:
    """
    Vision chat — sends image as base64 data URL alongside the caption.
    Uses the last N text messages as history context.
    """
    history = await hist.get_history(user_id)

    # Build vision user message
    user_content: list[dict[str, Any]] = []
    if caption:
        user_content.append({"type": "text", "text": caption})
    user_content.append({
        "type": "image_url",
        "image_url": {"url": image_data_url, "detail": "auto"},
    })

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_content},
    ]

    reply = await _call(user_id, messages)

    # Save text-only version in history
    saved_text = caption or "[Image]"
    await hist.append_message(user_id, "user", saved_text)
    await hist.append_message(user_id, "assistant", reply)
    return reply


async def _call(user_id: int, messages: list[dict]) -> str:
    """Shared API call with logging."""
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
        return reply
    except Exception as exc:
        log.error("OrcaRouter error | user=%s | %s", user_id, exc)
        raise
