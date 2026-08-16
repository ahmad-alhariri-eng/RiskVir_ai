FROM python:3.13-slim

# Don't write .pyc files, don't buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Railway injects $PORT automatically; default to 8000
ENV PORT=8000

EXPOSE $PORT

# Production: gunicorn + uvicorn workers (4 workers handles 50 req/s easily)
CMD gunicorn main:api \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:$PORT \
    --timeout 60 \
    --keep-alive 5 \
    --log-level info
