# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COOKIE_HTTP_SEEDER_DATA=/data \
    COOKIE_HTTP_SEEDER_HOST=0.0.0.0 \
    COOKIE_HTTP_SEEDER_PORT=18765

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY cookie_http_seeder ./cookie_http_seeder

RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 18765

# Publish only to host loopback in compose; keep cookies on the /data volume.
CMD ["cookie-http-seeder", "serve"]
