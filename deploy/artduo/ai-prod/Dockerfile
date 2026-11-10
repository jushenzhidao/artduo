FROM python:3.12-slim

# ffmpeg 提供抽帧 / GIF / 拼片；fonts-noto-cjk 保证中文片头与字幕不出现方块
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 只装图像处理；其余都是标准库（http.server / sqlite3 / urllib）
RUN pip install --no-cache-dir "Pillow==12.3.0"

WORKDIR /app
COPY . /app

RUN useradd -u 10001 -m app \
 && mkdir -p /app/qa /app/assets/generated /app/assets/subjects /app/assets/_trash \
 && chown -R 10001:10001 /app

ENV PORT=8080 \
    JIMENG_HOST=0.0.0.0 \
    JIMENG_ASS_FONT="Noto Sans CJK SC" \
    FFMPEG_BIN=/usr/bin/ffmpeg \
    FFPROBE_BIN=/usr/bin/ffprobe \
    TZ=Asia/Shanghai

USER 10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4)"

CMD ["python3", "/app/server.py"]
