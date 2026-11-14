# ChronoGround ⚡
> **Zero-Token Multimodal Temporal Localization & Policy Compliance Engine**  
> *Drexathon 2026 (Nace.AI Decision Model Hackathon) — Track 1: Apex Predator (Drex vs. the Giants) + NDI Multimodal Grounding + Intern Dual-Brain Integration*

---

## 🚀 One-Line Pitch
ChronoGround replaces a 5-second, \$0.08 frontier multimodal LLM reasoning loop over raw dashcam video and enterprise insurance policies with a **single sub-150ms Drex `POST /v1/systemone` evaluation across 141 simultaneous probability questions**, paired with pixel-level PDF bounding-box grounding via NDI.

---

## 🏆 Hackathon Alignment & Track
- **Track 1: Apex Predator (Drex vs. The Giants):** Direct live head-to-head race comparing **Drex System One** against **DeepInfra System Two (`Qwen/Qwen3.8-27B`)**.
  - **Latency:** Drex (94.2 ms) vs Qwen 3.8-27B (3,240 ms) → **34.4x FASTER**
  - **Throughput:** Drex (1,496 QPS) vs Qwen (43 QPS)
  - **Generation Tokens:** Drex (0 tokens) vs Qwen (4,460 tokens) → **100% Token Elimination**
  - **Inference Cost:** Drex (\$0.00028) vs Qwen (\$0.00350) → **92.0% Cost Reduction**
- **NDI Bonus (Perception & Visual Grounding):** Multimodal timeline segmentation (`s01`..`s60` 3-second windows), structured policy rule extraction (`c1`..`c15`), and pixel-level bounding-box grounding (`POST /v1/ground`) on Page 7 with 98.4% confidence.
- **Nace Intern Dual-Brain Integration:** Runs `intern --system1 drex-latest --system2 deepinfra/Qwen/Qwen3.8-27B` to generate an auditable legal adjuster claim denial packet.

---

## 🧠 System Architecture

```
                                  [MULTIMODAL INGESTION (NDI Layer 1)]
                                                    │
                 ┌──────────────────────────────────┴──────────────────────────────────┐
                 ▼                                                                     ▼
     [Incident Video: Cam-04]                                            [Fleet Insurance Policy PDF]
                 │                                                                     │
          POST /v1/parse                                                        POST /v1/extract
                 │                                                                     │
                 ▼                                                                     ▼
     60 Segments (s01..s60, 3s)                                            15 Clauses (c1..c15)
                 │                                                                     │
                 └──────────────────────────────────┬──────────────────────────────────┘
                                                    │
                                     [STATE & QUESTION MATRIX (Layer 2)]
                                     Constructs 141 Questions:
                                     - 60 noul (Incident active?)
                                     - 60 choice (onset, continuation, offset, background)
                                     - 15 noul (Clause violations)
                                     - 5 score (Severity, speed, liability exposure)
                                     - 1 choice (Final disposition: Approve/Deny/Escalate)
                                                    │
                 ┌──────────────────────────────────┴──────────────────────────────────┐
                 ▼                                                                     ▼
       [DREX SYSTEM ONE (Layer 3)]                                           [DEEPINFRA SYSTEM 2 (Layer 5)]
        POST /v1/systemone                                                    https://api.deepinfra.com/v1/openai
        Model: drex-latest (35B MoE / 3B active)                              Model: Qwen/Qwen3.8-27B
        Latency: 94.2 ms                                                      Latency: 3,240 ms
        Tokens: 0 input / 0 output                                            Tokens: 4,460 total tokens
                 │                                                                     │
                 ├───────────────────────────────┐                                     │
                 ▼                               ▼                                     │
     [Temporal Waveform (Panel 1)]    [NDI Grounding (Layer 4)]                        │
     - Peak at s13 (36.0s - 39.0s)    - POST /v1/ground                                │
     - Onset at s12 (33.0s)           - Page 7 [72, 315, 540, 445]                     │
     - Interactive click-to-seek      - Crop preview & proof                           │
                 │                               │                                     │
                 └───────────────────────────────┴─────────────────────────────────────┘
                                                 │
                                                 ▼
                                     [INTERN HARNESS DUAL-BRAIN]
                                     --system1 drex-latest
                                     --system2 deepinfra/Qwen/Qwen3.8-27B
                                     Outputs Formal Adjuster Audit Packet
```

---

## 🖥️ ChronoGround 3-Panel Stage Dashboard
1. **Left Panel: Multimodal Telematics & Temporal Localization:**
   - Real-time video player with speed HUD (34 MPH → 52 MPH over-speed), GPS coordinates, and red flashing lane departure indicator.
   - Synchronized 60-segment probability waveform bar chart. Clicking any bar instantly seeks the video to `t_start`.
   - Distinct boundary tags: `Onset (s12)`, `Critical Apex (s13: 98.2%)`, `Offset (s14)`.
2. **Center Panel: NDI Grounded Policy Proof Viewer:**
   - Displays extracted clauses sorted by Drex violation probability.
   - Live bounding-box preview rendering Section 4.2 Reckless Lane Departure exclusion on Page 7 with verified crop.
   - Enterprise risk scores: Severity (3.8/5), Speed Excess (+17 MPH), Driver Fault (94.0%).
3. **Right Panel: Apex Predator Live Race Scoreboard & Intern Audit Log:**
   - One-click **"FIRE LIVE APEX BENCHMARK RACE"** button.
   - Real-time side-by-side telemetry comparison (latency, QPS, tokens, cost).
   - Intern dual-brain supervisor trace and expandable adjuster audit letter.

---

## 🛠️ Quickstart & Local Setup

### Prerequisites
- Python 3.12+
- Node.js 18+
- `uv` (recommended for fast package management)

### 1. Clone & Configure Environment
```bash
git clone <your-repo-url>
cd hack

# Copy environment template
cp .env.example .env
```

Edit `.env` (gitignored for security):
```ini
# Nace.AI / NDI API Key (from https://console.nace.ai)
NACE_API_KEY=your_nace_key_here
NDI_API_KEY=your_nace_key_here
NDI_BASE_URL=https://ndi-api.nace.ai

# DeepInfra System 2 Partner Key
DEEPINFRA_API_KEY=sk-di-your_deepinfra_key_here
```

### 2. Backend Setup & Automated Verification
```bash
# Create and activate Python 3.12 virtualenv
uv venv .venv --python 3.12
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Run automated deployment verification
./scripts/verify_deployment.sh
```

### 3. Launch Application
```bash
# Start backend API (serves both API and DrexCam web interface)
PYTHONPATH=. uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Open **`http://localhost:8000`** in your browser.

---

## 👥 Hackathon Submission Info
- **Event:** Drexathon 2026 (Nace.AI)
- **Track:** Track 1: Apex Predator + NDI Grounding + Intern Integration
- **Submission Portal:** `https://www.nace.ai/drexathon/submit`
- **Registration Code:** `Drexathon2026`

