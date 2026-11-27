# TraceRecover — Phase 7.5 Live Intelligence Architecture & Provider Activation Audit

**Audit Timestamp (UTC):** `2026-11-25T04:33:18Z`  
**Live Activation Status:** **`3. BLOCKED — no genuine provider execution possible`**

---

## 1. Task A — Provider Credential & Local Runtime Audit

All five supported live LLM provider configurations were inspected without printing, logging, or exposing any secret values:

| Provider | Expected Environment Variable / Endpoint | Process Env Set | `.env` Non-Empty | Local Socket / CLI | Live `health_check()` Status | Error Category |
|---|---|---:|---:|---|---|---|
| `gemini` | `GEMINI_API_KEY` | No (`len=0`) | No | N/A | `unconfigured` | `missing_credentials` |
| `openai` | `OPENAI_API_KEY` | No (`len=0`) | No | N/A | `unconfigured` | `missing_credentials` |
| `anthropic` | `ANTHROPIC_API_KEY` | No (`len=0`) | No | N/A | `unconfigured` | `missing_credentials` |
| `ollama` | `OLLAMA_BASE_URL` (`127.0.0.1:11434`) | No (`len=0`) | No | Port closed; `ollama` CLI absent | `unconfigured` | `missing_credentials` |
| `local_openai` | `LOCAL_LLM_BASE_URL` (`127.0.0.1:1234`) | No (`len=0`) | No | Port closed | `unconfigured` | `missing_credentials` |

### Honesty & Zero-Silent-Fallback Verification
- `select_preferred_live_provider()` returned `live_available = false`, `selected_provider = "local"`, `mode = "offline_deterministic"`.
- Explicitly requesting `provider="gemini"` raises `ProviderUnavailableError` immediately (`refusing silent fallback`).
- No mock completion was substituted for Regime D (`LIVE LLM + LIVE CONNECTOR`).

---

## 2. End-to-End 12-Stage Pipeline Readiness (Verified Architecture, Pending Live Model)

TraceRecover's complete pipeline in `agent/open_ended_runner.py` is wired to execute all 12 stages in a single traced run once a genuine provider credential or local model server is supplied:

1. **Query Interpretation & Safety Guard:** `sanitize_and_guard_query()` + `DynamicQueryPlanner.analyze_query()`.
2. **Dynamic Research Planning:** `ResearchPlanner` + `DynamicQueryPlanner.next_action()` (with live LLM JSON tool-action planning when `provider != "local"`).
3. **Intelligent Source Selection:** `IntelligentSourceSelector` classifying domain and primary vs secondary sources.
4. **External Document Retrieval:** `ExternalSearchAdapter` (Wikipedia/DDG/Tavily/SerpAPI), `ConnectorManager` (arXiv, Crossref, OpenAlex, Semantic Scholar), and Playwright Chromium docs extraction, wrapped with Phase 7.5 external-search hygiene (`_wrap_external_search_with_hygiene`).
5. **Evidence Collection:** Strict separation of committed vs discarded tool outputs (`ClaimEvidenceVerifier.build_evidence_corpus`).
6. **Provenance-Preserving Claim Extraction:** `_extract_claims_with_provenance()` wrapping protected `ClaimExtractor`.
7. **Study Provenance Preservation:** 20-field observed-only provenance schema (`None` when unavailable; zero fabricated metadata).
8. **Claim-Level Verification:** Deterministic semantic + strict numeric/date verification + optional live LLM entailment (`create_llm_entailment_evaluator`) framing all evidence strictly inside `<UNTRUSTED_EVIDENCE_DATA>...</UNTRUSTED_EVIDENCE_DATA>`.
9. **Context-Aware Contradiction Assessment:** Six-class taxonomy (`none`, `bibliographic_metadata_difference`, `different_experimental_context`, `comparable_differing_research_results`, `logical_contradiction`, `insufficient_comparison_context`).
10. **Structured Evidence-Grounded Synthesis:** `EvidenceGroundedSynthesizer` passing evidence fenced as untrusted data to the configured provider.
11. **Citation Generation & Traceability:** Every cited URL validated against actually retrieved external sources.
12. **Uncertainty & Abstention Handling:** `UncertaintyEstimator`, `AbstentionPolicy`, and `classify_abstention_and_uncertainty`.

---

## 3. Live-Connector Hygiene Enhancements (Phase 7.5 Task E Findings)

Based on the Phase 7.4 live-connector root-cause audit, three generalizable, non-invasive improvements were added to `agent/open_ended_runner.py` (with zero changes to protected core modules or frozen Phase 7.4 holdout files) and covered by development regression tests in `tests/test_phase7_5_live_readiness_and_hygiene.py`:

1. **HTML Entity Unescaping (`html.unescape`):** Prevents Wikipedia entities (`&quot;`, `&amp;`) from splitting claims at `;`.
2. **Off-Topic Search Noise Pruning & Future-Year Abstention Guard:** Drops clearly unrelated search hits when higher-relevance hits exist, and abstains cleanly (`NO_EXTERNAL_EVIDENCE`) when a future-tense query targeting a future year (`>= 2028`) retrieves only historical articles mentioning earlier years.
3. **Compact Keyword Query Fallback:** When a verbose natural-language query (`>= 7` words) yields `NO_EXTERNAL_EVIDENCE` on live Wikipedia/DDG, retries once with top 5 high-signal technical/proper-noun tokens (never retrying future-tense unanswerable queries).
