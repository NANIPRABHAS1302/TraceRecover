# TraceRecover — Phase 7.4 Capstone Readiness Assessment

**Assessment Date:** 2026-11-25  
**Independent Holdout Version:** `1.0.0` (92 questions, 18 domains, 12 provenance cases, 18 contradiction cases)

---

## Final Readiness Classification

### **2. CONDITIONALLY READY**
> *Architecture, provenance preservation, context-aware contradiction verification, connector security, and controlled evaluations pass on an independent frozen holdout — but live LLM validation (`Regime D`) remains `BLOCKED` by missing credentials, and live-connector open-web synthesis without a live LLM shows expected recall limitations (`36.36%` overall accuracy).*

TraceRecover is **NOT** classified as `READY FOR PHASE 8` yet because genuine end-to-end live LLM intelligence (`Regime D: LIVE LLM + LIVE CONNECTOR`) has still never been executed against real provider credentials, and deterministic open-web retrieval without LLM synthesis exhibits low answer coverage on specialized technical queries. It is **NOT** `NOT READY` because the core verification, provenance, contradiction taxonomy, citation traceability, fault recovery, connector reliability, security, and UI transparency subsystems held up strongly on genuinely unseen holdout cases.

---

## Evidence Supporting `CONDITIONALLY READY`

### Strengths Independently Verified in Phase 7.4
1. **Controlled Holdout Generalization (Regime B `MOCK`, 92 new questions across 18 domains):**
   - `88/92 (95.65%)` overall accuracy; `74/76 (97.37%)` answered accuracy; `99.74%` evidence support rate; `0.26%` unsupported claim rate.
   - `TP=6, FP=0, TN=85, FN=1` (`100.0%` contradiction precision, `85.71%` recall, `0.00%` false contradiction rate).
   - `100.0%` citation precision (`n=68`), `79/79 (100.0%)` claim-to-source mapping accuracy, `269/269 (100.0%)` citation traceability, and `24/24 (100.0%)` end-to-end study attribution accuracy.
2. **Independent Provenance Audit (Task D, 12 cases across 9 patterns):**
   - `12/12` cases passed: `100.0%` study attribution (`12/12`), `100.0%` URL mapping (`15/15`), `100.0%` ambiguity detection (`17/17`), `100.0%` metadata completeness (`31/31`), and **`0.0%` (`0/47`) fabricated metadata rate**.
3. **Independent Labelled Contradiction Audit (Task E, 18 cases across 10 scenarios & 6 classes):**
   - `100.0%` binary accuracy (`TP=6, FP=0, TN=12, FN=0`, `F1=1.0000`) and `17/18 (94.44%)` exact 6-class accuracy.
4. **Live Connector Reliability (Task H, 6/6 live connectors):**
   - `arXiv`, `Crossref`, `OpenAlex`, `Wikipedia`, `DuckDuckGo`, and `Playwright Chromium` all returned real content that passed `ClaimEvidenceVerifier` substantive support checks (`SUPPORTED`, similarities `0.657`–`1.000`).
5. **Dashboard Transparency & Security (Tasks I & J):**
   - Real Chromium E2E confirmed honest `OFFLINE` mode labelling (never misrepresenting offline/mock runs as `LIVE LLM`), full provenance/contradiction/abstention/cost visibility, zero console/page errors, and zero browser secret storage.

### Outstanding Blockers Before Phase 8 Production Hardening
1. **Regime D (`LIVE LLM + LIVE CONNECTOR`) Remains `BLOCKED` (`92/92` blocked):** No `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, or `LOCAL_LLM_BASE_URL` is configured on this host. Real LLM planning, live entailment, token accounting, and cost budgeting remain unvalidated on live traffic.
2. **Open-Web Retrieval Recall Without LLM Query Reformulation (`Regime C: 8/22 = 36.36%` overall, `5/12 = 41.67%` answered):** Deterministic keyword retrieval over Wikipedia/DDG misses fine-grained technical specs (`EPOLLET`, `PostgreSQL MVCC`, `LLVM phi`, `Kubernetes probes`) and returned a generic Nobel Prize page on future-event query `H74-91` (`WARN` instead of `ABSTAIN`), confirming that live LLM query planning + relevance filtering is required for high open-web recall.
3. **Local KB Preemption on Scientific Queries (`H74-55`, `H74-87`):** Static local `search` matched generic keywords (`speed of light`, `boiling point`) before querying external search, causing 1 imprecise unit answer (`299,792 km/s` vs `299,792,458 m/s`) and 1 missed synthetic contradiction (`H74-87`).
4. **Python Expression Routing & Sandbox Scope (`H74-16`, `H74-67`):** Trailing natural language after an expression prevents deterministic `python_exec` routing, and generator expressions are intentionally disallowed by the AST sandbox.

---

## Concrete Gate to Upgrade to `READY FOR PHASE 8`
1. Configure a genuine live LLM provider (`GEMINI_API_KEY` or local `OLLAMA_BASE_URL`) and execute `Regime D` on the frozen Phase 7.4 holdout with provider-reported token telemetry and zero silent fallback.
2. Prevent static local KB snippets from preempting explicit external-search verification queries when external search is enabled.
3. Add temporal future-year abstention guards so generic historical pages (e.g., `"Nobel Prize in Physics"`) cannot produce `WARN` on future-dated questions (`2033`).
