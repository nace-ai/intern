# ChronoGround • Production Deployment Guide

This guide details the complete deployment process for **ChronoGround: Zero-Token Multimodal Temporal Localization & Policy Compliance Adjudication Engine**.

---

## 1. System Prerequisites & Environment

### Hardware Requirements
- **CPU:** 2 vCPU minimum (4 vCPU recommended for high concurrency)
- **Memory:** 2 GB RAM minimum (4 GB recommended)
- **Disk:** 10 GB SSD for multimodal caching and collision video uploads
- **Network:** Outbound HTTPS access to:
  - `console.nace.ai` / `ndi-api.nace.ai` (Port 443)
  - `api.deepinfra.com` (Port 443)

### Required Environment Variables
Create a `.env` file in the project root:

```bash
# Nace Drex / NDI Grounding Credentials (from https://console.nace.ai)
NACE_API_KEY=nace_sk_your_nace_key_here
NDI_API_KEY=nace_sk_your_nace_key_here
NACE_CONSOLE_URL=https://console.nace.ai/v1
NDI_BASE_URL=https://ndi-api.nace.ai

# DeepInfra System 2 Partner Key
DEEPINFRA_API_KEY=sk-di-your_deepinfra_key_here

# Server Configuration
PORT=8000
HOST=0.0.0.0
```

---

## 2. Deployment Option A: Docker (Recommended)

ChronoGround uses a high-efficiency multi-stage `Dockerfile` (Node 20 frontend compiler + Python 3.12 slim runtime with FFmpeg).

### Build & Run with Docker:
```bash
# 1. Build container image
docker build -t chronoground:latest .

# 2. Run container
docker run -d \
  --name chronoground-app \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/backend/data/videos:/app/backend/data/videos \
  -v $(pwd)/backend/data/policies:/app/backend/data/policies \
  --restart unless-stopped \
  chronoground:latest
```

### Or using Docker Compose:
```bash
docker compose up -d --build
```

---

## 3. Deployment Option B: Bare Metal / Linux VM (systemd)

### 1. Install System Dependencies (Ubuntu/Debian)
```bash
sudo apt-get update && sudo apt-get install -y \
  python3.12 python3.12-venv python3-pip \
  ffmpeg curl git nodejs npm
```

### 2. Clone & Build Frontend
```bash
cd /opt/chronoground
npm install --prefix frontend
npm run build --prefix frontend
```

### 3. Setup Python Virtual Environment
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Create systemd Service Unit (`/etc/systemd/system/chronoground.service`)
```ini
[Unit]
Description=ChronoGround Adjudication Engine
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/chronoground
EnvironmentFile=/opt/chronoground/.env
ExecStart=/opt/chronoground/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chronoground
```

---

## 4. Deployment Option C: Cloud PaaS (Railway / Render / Fly.io / GCP Cloud Run)

ChronoGround is packaged to deploy to any modern container hosting provider out of the box:

### Deploy to Railway / Render / Fly.io:
1. Connect your GitHub repository to Railway or Render.
2. The platform automatically detects the multi-stage `Dockerfile`.
3. Add the following environment variables in the dashboard:
   - `NACE_API_KEY`
   - `NDI_API_KEY`
   - `DEEPINFRA_API_KEY`
   - `PORT=8000`
4. Set healthcheck endpoint: `/api/health`.

### Deploy to Google Cloud Run:
```bash
gcloud run deploy chronoground \
  --source . \
  --port 8000 \
  --allow-unauthenticated \
  --set-env-vars NACE_API_KEY="nace_sk_...",DEEPINFRA_API_KEY="sk-di-..."
```

---

## 5. Automated Verification & Smoke Testing

Run the included verification script to ensure all backend and frontend services are operating correctly:

```bash
./scripts/verify_deployment.sh http://localhost:8000
```

Verification test coverage:
- `GET /api/health` — Verifies API keys & model endpoints.
- `GET /api/media/video` — Verifies streaming video server (`HTTP 200` / `206 Partial Content`).
- `GET /api/media/policy` — Verifies PDF contract delivery.
- `GET /api/policy/full` — Verifies 51-page Texas Personal Auto Policy metadata and exclusions.
- `POST /api/evaluate` — Verifies sub-350ms Drex System 1 zero-token localization.
- `GET /` — Verifies compiled React/Vite UI bundle delivery.

