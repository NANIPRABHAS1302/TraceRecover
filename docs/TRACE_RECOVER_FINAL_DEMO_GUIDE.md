# TraceRecover — Final Capstone Demonstration Guide

This guide walks a student or evaluator through starting and demonstrating TraceRecover **completely offline with zero API keys required**.

---

## 1. Quick Startup & Verification (Windows PowerShell)

```powershell
cd C:\tracerecover
.\venv\Scripts\python.exe -m uvicorn web_dashboard:app --host 127.0.0.1 --port 8000 --proxy-headers
```

In a second terminal, verify the backend health endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 4
```

Expected highlights:
- `status: "ok"`
- `subsystems.execution_modes.offline.operational: true`
- `subsystems.storage.conversations_persistent: true` (`filesystem_json_atomic`)
- `subsystems.execution_modes.live_llm.configured: false` *(honest disclosure; no credentials required)*

Open the web dashboard in Chrome or Edge: **`http://127.0.0.1:8000`**

---

## 2. Dashboard Navigation Tour (2 Minutes)

1. **Overview:** Pooled benchmark metrics (100-task canonical benchmark across 0%, 20%, and 40% fault rates), grouped SVG comparison chart, and dataset inventory.
2. **Query Workspace:** Primary live demonstration tab with conversation threads (`#session=` URL-hash persistence), 10-stage SSE timeline, execution-integrity badges, stage timings, and Claim-Level Verification table.
3. **Trace Replay & Evidence Inspector:** Step-by-step replay of any saved JSONL trace showing `COMMITTED` vs. `DISCARDED` provenance.
4. **Live Runner (6 One-Click Presets):** Deterministic presets including `Normal ANSWER`, `Multi-Hop Lookup`, `40% Fault + Self-Healing`, `Missing Entity ABSTAIN`, `Contradiction Guard`, and `Ablation Toggle (Raw)`.
5. **Connect Hub, Unseen Evaluation Lab, Multi-Provider Comparison & Settings:** Demonstrates MCP/scholarly/browser connector controls, unseen benchmark generalization, fail-closed provider health checks, and calibrated thresholds.

---

## 3. Three Reproducible Demonstrations (Zero API Keys Required)

### Demonstration A — Evidence-Grounded Multi-Step Research (`OFFLINE` Mode)
1. Click **Query Workspace** in the left sidebar.
2. Click **New Conversation** (creates an isolated, persistent thread `conv-...`).
3. Ensure **Execution Provider** is set to `local (Offline Deterministic)` and **Fault %** is `0`.
4. Submit:
   ```text
   Look up support ticket T-401 and calculate (1200 * 1.15)
   ```
5. **What to point out to evaluators:**
   - **Execution Mode Badge:** `OFFLINE` (honestly labeled; never mislabeled `LIVE LLM`).
   - **Execution Integrity & Stage Timings:** `Requested: local`, `Planning: succeeded`, `Synthesis: succeeded`, `Origin: deterministic_synthesizer`, plus real millisecond durations (`Plan`, `Tools/Conn`, `Verify`, `Synth`, `Total`).
   - **10-Stage Live Timeline:** Shows `mock_api` retrieving ticket `T-401` (`cost_usd: 1200`) followed by `calculator` computing `1380.0`, both marked `COMMITTED`.
   - **Claim-Level Verification:** Extracted claims (`CL-1`, `CL-2`) verified `Supported (SUPPORTED)` with cited provenance.
   - **Final Decision:** `ANSWER` (`1380`).
6. **Persistence Proof:** Press `F5` (reload page) or restart Uvicorn, switch back to **Query Workspace**, and select the same `conv-...` thread — your full history is recovered from disk.

### Demonstration B — Contradiction Detection & Context-Aware Verification
*(Note: The Live Runner `Contradiction Guard` preset uses a controlled synthetic contradiction probe; the automated provenance/contradiction suite uses controlled multi-study fixtures.)*

1. Click **Live Runner** in the left sidebar.
2. Click the **Contradiction Guard** one-click preset card, then click **Run TraceRecover**.
3. **What to point out to evaluators:**
   - Step 1 retrieves verified tool evidence (`The capital of France is Paris`).
   - A conflicting candidate claim (`The capital of France is Rome`) is evaluated by `EvidenceChecker` + NLI.
   - The claim is flagged **`CONTRADICTED`** and the abstention policy enforces **`ABSTAIN`** so the hallucinated answer is suppressed.
4. *(Optional CLI proof of Phase 7.3.1 context-aware multi-study verification)*:
   ```powershell
   .\venv\Scripts\python.exe -m pytest -v tests/test_phase7_3_1_provenance_and_contradiction.py
   ```
   Demonstrates `13/13` passing tests proving that separate independent studies reporting different accuracy values are preserved with distinct `study_identity` attribution rather than falsely flagged as logical contradictions.

### Demonstration C — Self-Healing Failure Recovery & Honest Abstention (`OFFLINE` Mode)
1. **Part 1 — Self-Healing Recovery:**
   - In **Query Workspace**, set **Fault %** to `40` and **Seed** to `13`.
   - Submit: `What is 19 * 23 + 4?`
   - **What to point out:** Step 1 (`calculator`) injects a `wrong_output` fault and is marked **`DISCARDED`**. `FailureDetector` + `FailureLocalizer` localize the fault, `RecoveryEngine` executes `TOOL_SWITCH` to `python_exec` (marked **`COMMITTED`**), and TraceRecover returns **`ANSWER`** (`441`) citing only the detector-validated recovered step.
2. **Part 2 — Honest Abstention on Missing Evidence:**
   - Set **Fault %** back to `0`.
   - Submit: `Who discovered the fictional element unobtainium?`
   - **What to point out:** Search returns a no-evidence sentinel (excluded from eligible evidence), grounding drops to `0.0`, and TraceRecover emits **`ABSTAIN`** with a clear `Why / Abstention Reason:` explanation.
3. **Part 3 — Fail-Closed Live LLM Honesty (Optional):**
   - Switch **Execution Provider** to `gemini (Live LLM)` and click **Check Provider Health**.
   - **What to point out:** TraceRecover honestly reports `unconfigured` (`missing_credentials`) and refuses silent deterministic fallback (`HTTP 503` if submitted without `GEMINI_API_KEY`).

---

## 4. Optional Automated Mock & Integrity Demonstration (CLI)

To demonstrate **MOCK** mode and Phase 7.5.2 post-retrieval evidence preservation when a remote LLM fails during synthesis:

```powershell
.\venv\Scripts\python.exe -m pytest -v tests/test_phase7_5_2_synthesis_and_execution_integrity.py
```

*(Uses deterministic mock providers; clearly labeled `MOCK`, never represented as live remote API responses.)*
