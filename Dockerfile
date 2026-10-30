FROM python:3.11-slim

WORKDIR /app

# Install ffmpeg / ffprobe for audio duration probing and -14.0 LUFS mastering normalization
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py
COPY music_studio_app.py /app/music_studio_app.py
COPY music_studio_ui.html /app/music_studio_ui.html
COPY docs /app/docs

RUN mkdir -p /app/data /app/workspace

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV CLOUD_LOGGING_JSON=true
ENV GOOGLE_CLOUD_LOCATION=global
ENV MUSIC_STUDIO_GEMINI_MODEL=gemini-4-argon
ENV MUSIC_STUDIO_THINKING_LEVEL=MEDIUM
ENV MUSIC_STUDIO_LYRIA_MODEL=lyria-3-pro-preview

EXPOSE 8080

CMD ["python3", "main.py", "--host", "0.0.0.0", "--port", "8080"]
