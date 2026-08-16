"""
Telegram bot handlers.
All handlers are async and non-blocking.
"""
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app import ai_client
from app.config import HISTORY_MAX_MESSAGES, RATE_LIMIT_PER_MINUTE
from app.logger import get_logger
from app.rate_limiter import is_rate_limited

log = get_logger(__name__)


# ── Command handlers ────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.info("cmd=/start | user=%s (%s)", user.id, user.username)
    await update.message.reply_text(
        f"👋 مرحباً {user.first_name}!\n\n"
        "أنا مساعد ذكاء اصطناعي. أرسل لي أي رسالة وسأرد عليك فوراً.\n\n"
        "📌 الأوامر المتاحة:\n"
        "• /start — عرض هذه الرسالة\n"
        "• /clear — مسح سجل المحادثة\n"
        "• /help  — المساعدة"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *مساعد الذكاء الاصطناعي*\n\n"
        f"• يتذكر آخر *{HISTORY_MAX_MESSAGES}* رسالة في المحادثة.\n"
        f"• الحد الأقصى: *{RATE_LIMIT_PER_MINUTE}* رسائل في الدقيقة.\n"
        "• استخدم /clear لبدء محادثة جديدة من الصفر.",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await ai_client.clear_history(user_id)
    log.info("cmd=/clear | user=%s", user_id)
    await update.message.reply_text("✅ تم مسح سجل المحادثة. ابدأ محادثة جديدة!")


# ── Message handler ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()

    if not text:
        return

    # Rate limiting
    if is_rate_limited(user_id):
        await update.message.reply_text(
            f"⚠️ لقد تجاوزت الحد المسموح به ({RATE_LIMIT_PER_MINUTE} رسائل/دقيقة).\n"
            "انتظر قليلاً ثم أعد المحاولة."
        )
        return

    log.info("msg | user=%s (%s) | len=%d", user_id, user.username, len(text))

    # Show typing indicator without awaiting (fire-and-forget)
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    try:
        reply = await ai_client.chat(user_id, text)
        # Telegram message limit is 4096 chars; split if needed
        if len(reply) <= 4096:
            await update.message.reply_text(reply)
        else:
            for chunk in _split_text(reply, 4096):
                await update.message.reply_text(chunk)

    except Exception as exc:
        log.error("Handler error | user=%s | %s", user_id, exc)
        await update.message.reply_text(
            "❌ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى."
        )


def _split_text(text: str, max_len: int) -> list[str]:
    """Split long text into chunks respecting Telegram's message size limit."""
    return [text[i: i + max_len] for i in range(0, len(text), max_len)]


# ── Application factory ─────────────────────────────────────────────────────

def build_application(token: str) -> Application:
    """Build and return a configured Telegram Application instance."""
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
