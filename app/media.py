"""
Media processing utilities.
  - Images   → base64 data URL for vision models
  - Voice    → transcribed text via Whisper API
  - Documents → extracted text (TXT, PDF, CSV, code files)
"""
import asyncio
import base64
import io
from pathlib import Path

from telegram import Bot
from app.logger import get_logger

log = get_logger(__name__)

# Max file size we'll attempt to process (10 MB)
MAX_FILE_BYTES = 10 * 1024 * 1024

# Document extensions we can read as plain text
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".css", ".sh", ".log",
}


# ── Downloader ────────────────────────────────────────────────────────────────

async def download_file(bot: Bot, file_id: str) -> bytes:
    """Download a Telegram file by file_id and return raw bytes."""
    tg_file = await bot.get_file(file_id)
    buf = await tg_file.download_as_bytearray()
    data = bytes(buf)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File too large ({len(data)//1024} KB). Max is 10 MB.")
    log.debug("Downloaded file %s (%d bytes)", file_id, len(data))
    return data


# ── Image ─────────────────────────────────────────────────────────────────────

def image_to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    """Convert raw image bytes to a base64 data URL for vision API calls."""
    b64 = base64.b64encode(data).decode()
    return f"data:{mime};base64,{b64}"


def _photo_mime(file_id: str) -> str:
    """Telegram photos are always JPEG."""
    return "image/jpeg"


# ── Audio / Voice ─────────────────────────────────────────────────────────────

async def transcribe_audio(audio_bytes: bytes, filename: str, client) -> str:
    """
    Transcribe audio using OpenAI-compatible Whisper API.
    Falls back to an error message if the endpoint is unavailable.
    """
    try:
        # openai client expects a file-like tuple: (name, bytes, mime)
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_bytes, "audio/ogg"),
        )
        text = transcript.text.strip()
        log.info("Transcribed audio (%d bytes) → %d chars", len(audio_bytes), len(text))
        return text
    except Exception as e:
        log.warning("Whisper transcription failed: %s", e)
        return None   # caller will handle fallback


# ── Document ──────────────────────────────────────────────────────────────────

def _extract_pdf_text(data: bytes) -> str:
    """Extract text from a PDF using pypdf."""
    try:
        import pypdf  # optional dep
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        return text or "(No text found in PDF)"
    except ImportError:
        return "(PDF support requires pypdf — not installed)"
    except Exception as e:
        log.warning("PDF parse error: %s", e)
        return f"(Could not parse PDF: {e})"


def extract_document_text(data: bytes, filename: str) -> str:
    """
    Extract text from a document based on its extension.
    Returns the extracted text or an informative error string.
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        text = _extract_pdf_text(data)
    elif ext in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception as e:
            text = f"(Could not decode file: {e})"
    else:
        return None   # unsupported type

    # Trim to 8000 chars to stay within model context
    if len(text) > 8000:
        text = text[:8000] + "\n\n... [truncated — file too long]"
    return text
