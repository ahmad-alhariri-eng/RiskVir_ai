"""
Configuration module — loads and validates all environment variables.
Fails fast at startup if required variables are missing.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = _require("TELEGRAM_TOKEN")
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_URL: str = os.environ.get("WEBHOOK_URL", "")

# ── OrcaRouter ─────────────────────────────────────────────────────────────
ORCAROUTER_API_KEY: str = _require("ORCAROUTER_API_KEY")
ORCAROUTER_BASE_URL: str = os.environ.get("ORCAROUTER_BASE_URL", "https://api.orcarouter.ai/v1")

# Available free models:
#   deepseek/deepseek-v4-pro-free
#   deepseek/deepseek-v4-flash-free
#   qwen/qwen3.8-27b-free
AI_MODEL: str = os.environ.get("AI_MODEL", "deepseek/deepseek-v4-pro-free")
AI_SYSTEM_PROMPT: str = os.environ.get(
    "AI_SYSTEM_PROMPT",
    "You are a helpful, concise, and friendly AI assistant.",
)
AI_MAX_TOKENS: int = int(os.environ.get("AI_MAX_TOKENS", "1024"))

# ── Redis ──────────────────────────────────────────────────────────────────
# Local: redis://localhost:6379
# Railway / Render: set REDIS_URL in environment variables
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379")

# ── Conversation history ───────────────────────────────────────────────────
HISTORY_MAX_MESSAGES: int = int(os.environ.get("HISTORY_MAX_MESSAGES", "20"))
HISTORY_TTL_HOURS: int = int(os.environ.get("HISTORY_TTL_HOURS", "24"))

# ── Rate limiting ──────────────────────────────────────────────────────────
RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "10"))

# ── Server ─────────────────────────────────────────────────────────────────
HOST: str = os.environ.get("HOST", "0.0.0.0")
PORT: int = int(os.environ.get("PORT", "8000"))

# ── Worker ─────────────────────────────────────────────────────────────────
# How many AI workers to spawn when running locally (python main.py)
WORKER_COUNT: int = int(os.environ.get("WORKER_COUNT", "4"))
