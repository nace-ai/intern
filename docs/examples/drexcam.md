# DrexCam: First-Notice-of-Loss Auto-Adjudication with Intern

[DrexCam](https://github.com/Spartacam/DrexCam) is an autonomous First-Notice-of-Loss (FNOL) collision adjudication platform built on the Nace Intern dual-brain architecture.

It triages raw collision dashcam video against statutory insurance policy contracts (such as the Texas Personal Auto Policy TX 01-PAP-003) in sub-100ms.

---

## Dual-Brain Architecture

DrexCam demonstrates Intern's two-part brain pattern applied to real-time multimodal decisioning:

```
                  ┌────────────────────────────────────────┐
                  │          Raw Video Ingestion           │
                  │        (FFmpeg Frame Extraction)       │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │         Drex System 1 Decider          │
                  │  (Sliding temporal question matrix:    │
                  │   noul/choice at 92ms / zero tokens)   │
                  └───────────────────┬────────────────────┘
                                      │
                     ┌────────────────┴────────────────┐
                     │                                 │
                     ▼                                 ▼
             [Standard Claim]                 [Contested / High-Risk]
             Auto-Approve / Deny                       │
                                                       ▼
                                      ┌────────────────────────────────┐
                                      │    Intern Dual-Brain Harness   │
                                      │  --system1 drex-latest         │
                                      │  --system2 deepinfra/Qwen27B   │
                                      └────────────────┬───────────────┘
                                                       │
                                                       ▼
                                      ┌────────────────────────────────┐
                                      │  Binding Legal Adjuster Packet │
                                      │  (NDI Grounded Clause Proof)   │
                                      └────────────────────────────────┘
```

### 1. System 1 (Drex fast decider)
- **Role:** Fast perception and policy exclusion matching across sliding temporal windows.
- **Protocol:** Dispatches typed question dictionaries (`noul`, `choice`) directly to Nace System One API.
- **Performance:** **92ms latency**, **zero generation tokens**, and **$0.0001 per batch** (50x faster and 97% cheaper than frontier LLM video processing).

### 2. System 2 (Qwen 3.8-27B via DeepInfra)
- **Role:** Synthesis of formal legal adjuster denial and compliance audit packets.
- **Protocol:** Invoked via Intern CLI:
  ```bash
  intern -p --max-turns 1 \
    --system1 drex-latest \
    --system2 deepinfra/Qwen/Qwen3.8-27B \
    "Generate a formal insurance claim denial summary for incident TX-00406..."
  ```

---

## Running the Example

The complete runnable implementation is included directly in this repository at [`examples/drexcam/`](../../examples/drexcam/):

```bash
cd examples/drexcam
pip install -r requirements.txt
cp .env.example .env  # Add your NACE_API_KEY and DEEPINFRA_API_KEY
./scripts/verify_deployment.sh
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Open `http://localhost:8000` to test the collision analysis web UI.

---

## Key Takeaways

1. **Zero-Token Triage:** Rather than streaming megabytes of frames into an LLM context, System 1 localizes the critical collision timestamp with calibrated probabilities.
2. **Deterministic Governance:** The Intern supervisor logs every audit step (`system1_verification`, `policy_clause_binding`, `telematics_integrity`) for regulatory compliance.
3. **Open-Source Repository:** Full source code and deployment instructions are available at [https://github.com/Spartacam/DrexCam](https://github.com/Spartacam/DrexCam).

