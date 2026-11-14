# Step-by-Step Render Deployment Guide for ChronoGround

This repository contains a pre-configured `render.yaml` Blueprint and an optimized multi-stage `Dockerfile` designed for instant zero-configuration deployment on [Render](https://render.com).

---

## 1. Prerequisites
- A GitHub or GitLab account with this repository pushed to it.
- An account at [render.com](https://render.com).
- Your API Keys ready:
  - **NACE_API_KEY**: e.g. `nace_sk_...` (from [console.nace.ai](https://console.nace.ai))
  - **DEEPINFRA_API_KEY**: e.g. `sk-di-...` (from [deepinfra.com](https://deepinfra.com))

---

## 2. Option A: 1-Click Blueprint Deployment (Recommended)

1. Log into your **Render Dashboard** ([dashboard.render.com](https://dashboard.render.com)).
2. Click **New +** in the top navigation bar and select **Blueprint**.
3. Select your GitHub repository (`hack` or your cloned repo).
4. Render will automatically detect `render.yaml` and configure the service:
   - **Service Name:** `chronoground-adjudicator`
   - **Environment:** `Docker`
   - **Healthcheck Path:** `/api/health`
5. Under **Environment Variables**, fill in your secret keys:
   - `NACE_API_KEY`: Paste your `nace_sk_...` key.
   - `NDI_API_KEY`: (Optional, can be same as `NACE_API_KEY`).
   - `DEEPINFRA_API_KEY`: Paste your `sk-di-...` key.
6. Click **Apply Blueprint**.

Render will now build the container, install system dependencies (`ffmpeg`, `python3.12`, `nodejs`), compile the React/TypeScript frontend bundle, and deploy the service.

---

## 3. Option B: Manual Web Service Deployment

If you prefer setting it up as a standard Web Service:

1. Click **New +** -> **Web Service**.
2. Select your repository.
3. Configure the following fields:
   - **Name:** `chronoground`
   - **Region:** Any (e.g., `Oregon (US West)` or `Ohio (US East)`)
   - **Branch:** `main`
   - **Runtime:** **Docker**
   - **Dockerfile Path:** `./Dockerfile`
   - **Docker Context:** `.`
   - **Instance Type:** `Starter` ($7/mo) or `Free` (ensure at least 512MB RAM for builds)
4. Under **Advanced** -> **Health Check Path**, enter:
   ```
   /api/health
   ```
5. Under **Environment Variables**, click **Add Environment Variable** and add:
   | Key | Value |
   | :--- | :--- |
   | `NACE_API_KEY` | `nace_sk_your_key_here` |
   | `NDI_API_KEY` | `nace_sk_your_key_here` |
   | `NACE_CONSOLE_URL` | `https://console.nace.ai/v1` |
   | `NDI_BASE_URL` | `https://ndi-api.nace.ai` |
   | `DEEPINFRA_API_KEY` | `sk-di-your_key_here` |
6. Click **Create Web Service**.

---

## 4. Verification Post-Deployment

Once the build finishes and shows **Live**, open your public Render URL:
`https://chronoground-adjudicator.onrender.com`

You can verify the deployment health in your terminal:
```bash
./scripts/verify_deployment.sh https://chronoground-adjudicator.onrender.com
```

### Verified Features on Render:
1. **Landing Page:** Upload Collision Video dropzone with immediate zero-token compliance adjudication.
2. **Video Streaming:** Native HTML5 streaming with MP4 byte-range support.
3. **Model Benchmark:** Live System 1 (Drex) vs System 2 (DeepInfra Qwen) race.
4. **Policy Document:** 51-Page Texas Personal Auto Policy embedded PDF viewer.

