# ==========================================
# Production Dockerfile for DrexCam / ChronoGround
# ==========================================

FROM python:3.12-slim AS runner

# Install system dependencies (ffmpeg and ffprobe for telematics & video frame extraction)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application source and DrexCam web interface
COPY backend/ ./backend/
COPY web/ ./web/

# Ensure media storage directories exist
RUN mkdir -p /app/backend/data/videos /app/backend/data/policies /app/backend/static

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# Healthcheck for container orchestrators (Render, Railway, Fly.io, Kubernetes)
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/api/health || exit 1

# Production entrypoint with Uvicorn (dynamically binds to Render's $PORT or 8000)
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
