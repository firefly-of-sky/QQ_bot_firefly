FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY qqbot ./qqbot
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home bot && mkdir /data && chown bot:bot /data
USER bot
ENV PYTHONUNBUFFERED=1 CONFIG_PATH=/app/config.toml DATABASE_PATH=/data/chat.sqlite3
CMD ["python", "-m", "qqbot"]
