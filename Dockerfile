FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CHRONOS_DB_PATH=/data/timelines.db

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 chronos \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin chronos \
    && install -d -o 10001 -g 10001 -m 0750 /data \
    && touch /data/.chronos-volume \
    && chown 10001:10001 /data/.chronos-volume

COPY --chown=10001:10001 app ./app

USER 10001:10001
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--log-level", "info"]
