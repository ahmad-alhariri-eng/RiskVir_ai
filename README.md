# 🤖 RiskVir AI — Telegram Bot

Production-ready Telegram bot powered by **OrcaRouter AI** (DeepSeek / Qwen).

## Architecture

```
Telegram → FastAPI Webhook → Redis Stream → AI Workers → Telegram
```

- **FastAPI** — webhook server, returns 200 in <1ms
- **Redis Streams** — message queue, at-least-once delivery
- **AI Workers** — pull from queue, call OrcaRouter, send reply
- **Redis Rate Limiter** — atomic sliding window, safe across all workers
- **Redis History** — persistent conversation context, TTL auto-expiry

## Capabilities

| Feature | Detail |
|---|---|
| Concurrent users | 5000+/s (limited by OrcaRouter quota) |
| Conversation memory | Last 20 messages per user (Redis, 24h TTL) |
| Rate limiting | 10 messages/minute per user (configurable) |
| Long messages | Auto-split at 4096 chars (Telegram limit) |
| Worker scaling | Add workers horizontally without code changes |

## Quick Start (Local)

```bash
# 1. Clone & install
git clone https://github.com/ahmad-alhariri-eng/RiskVir_ai
cd RiskVir_ai
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your tokens

# 3. Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# 4. Run (polling mode — no webhook needed locally)
python main.py
```

## Production Deploy (Docker Compose)

```bash
# Set WEBHOOK_URL in .env first, then:
docker compose up -d --build

# Scale workers
docker compose up -d --scale worker-0=1 worker-1=1 worker-2=1 worker-3=1
```

## Deploy on Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add **Redis** plugin (Railway auto-sets `REDIS_URL`)
4. Set environment variables:
   ```
   TELEGRAM_TOKEN=...
   ORCAROUTER_API_KEY=...
   WEBHOOK_URL=https://your-app.up.railway.app
   WEBHOOK_SECRET=random-secret-string
   ```
5. Railway builds the Dockerfile and deploys automatically

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | — | Bot token from @BotFather |
| `ORCAROUTER_API_KEY` | ✅ | — | OrcaRouter API key |
| `REDIS_URL` | ✅ | `redis://localhost:6379` | Redis connection string |
| `WEBHOOK_URL` | Prod | — | Public HTTPS URL for webhook |
| `WEBHOOK_SECRET` | Prod | — | Random secret for webhook path |
| `AI_MODEL` | | `deepseek/deepseek-v4-pro-free` | OrcaRouter model |
| `AI_SYSTEM_PROMPT` | | helpful assistant | System prompt |
| `AI_MAX_TOKENS` | | `1024` | Max tokens per reply |
| `HISTORY_MAX_MESSAGES` | | `20` | Messages to remember per user |
| `HISTORY_TTL_HOURS` | | `24` | History expiry in hours |
| `RATE_LIMIT_PER_MINUTE` | | `10` | Max msgs/min per user |
| `WORKER_COUNT` | | `4` | Workers in local dev mode |

## Commands

| Command | Action |
|---|---|
| `/start` | Welcome message |
| `/help` | Show info |
| `/clear` | Reset conversation history |
