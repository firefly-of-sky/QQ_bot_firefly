FROM python:3.12-slim
WORKDIR /app
COPY requirements.runtime.txt ./
RUN pip install --no-cache-dir --upgrade pip==26.2.1 \
    && pip install --no-cache-dir --retries 5 --resume-retries 5 -r requirements.runtime.txt \
    && useradd --uid 10001 --create-home bot && mkdir /data && chown bot:bot /data
COPY pyproject.toml ./
COPY qqbot ./qqbot
COPY vendor/tulpa ./vendor/tulpa
COPY tulpa.toml ./tulpa.toml
RUN python -m compileall -q qqbot && pip install --no-cache-dir --no-deps .
USER bot
ENV PYTHONUNBUFFERED=1 CONFIG_PATH=/app/config.toml DATABASE_PATH=/data/chat.sqlite3
CMD ["python", "-m", "qqbot"]
