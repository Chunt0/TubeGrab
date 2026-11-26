# syntax=docker/dockerfile:1

# ---- 1. build the React UI -------------------------------------------------
FROM oven/bun:1.3 AS web
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN bun run build

# ---- 2. runtime: Python + yt-dlp + ffmpeg + bun ----------------------------
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JS runtime to solve YouTube's player challenges; reuse bun
COPY --from=web /usr/local/bin/bun /usr/local/bin/bun

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app.py docker/entrypoint.sh ./
COPY static ./static
COPY --from=web /web/dist ./web/dist

ENV TUBEGRAB_DOWNLOADS=/downloads \
    TUBEGRAB_COOKIES=/config/cookies.txt \
    TUBEGRAB_PORT=8765 \
    PUID=1026 \
    PGID=100 \
    YTDLP_AUTOUPDATE=1 \
    HOME=/tmp \
    PYTHONUNBUFFERED=1

VOLUME ["/downloads"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"TUBEGRAB_PORT\"]}/api/jobs',timeout=4)"

ENTRYPOINT ["tini", "--", "/app/entrypoint.sh"]
