FROM python:3.12-slim

# Install ffmpeg + deno
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.deno/bin:$PATH"

# Install Python deps
RUN pip install --no-cache-dir yt-dlp typer rich

# Pre-cache yt-dlp EJS components for YouTube
RUN yt-dlp --remote-components ejs:github --simulate "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>/dev/null || true

# Copy app
WORKDIR /app
COPY anysong.py .

# Output dir
RUN mkdir -p /music

ENTRYPOINT ["python", "anysong.py"]
CMD ["--help"]
