FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000

EXPOSE $PORT

# Single uvicorn worker — AI workers run as asyncio tasks inside the same process
CMD uvicorn main:api --host 0.0.0.0 --port $PORT --loop asyncio --log-level info
