FROM python:3.12-slim

LABEL org.opencontainers.image.title="ytm2j" \
      org.opencontainers.image.description="YouTube Music -> local folders downloader (yt-dlp)" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# ffmpeg from the system image is more reliable inside a container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY ytm2j ./ytm2j

# Music output lives on a mounted volume; config is stored there too.
ENV YTM2J_OUTPUT_DIR=/data/music \
    XDG_CONFIG_HOME=/data/config
VOLUME ["/data"]

EXPOSE 8765

CMD ["python", "-m", "ytm2j", "serve", "--host", "0.0.0.0", "--port", "8765"]
