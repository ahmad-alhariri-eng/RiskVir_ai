"""
Shared Bot instance — initialized once and reused across all workers.
"""
from telegram import Bot
from app.config import TELEGRAM_TOKEN

# Single Bot instance shared across workers
bot = Bot(token=TELEGRAM_TOKEN)
