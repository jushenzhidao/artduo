FROM python:3.12-slim

# ffmpeg 提供 xfade/ass/drawtext；fonts-noto-cjk 保证中文片头与字幕不出现方块
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py

RUN useradd -u 10001 -m app \
 && mkdir -p /data/out /data/work \
 && chown -R 10001:10001 /data

ENV REEL_PORT=8080 \
    REEL_DATA=/data \
    REEL_FONT="Noto Sans CJK SC"

USER 10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/v1/healthz',timeout=4)"

CMD ["python3", "/app/app.py"]
