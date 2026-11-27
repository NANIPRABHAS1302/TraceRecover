# TraceRecover — Phase 8.2 Performance Analysis

**Environment:** Windows 11 (`10.0.26300-SP0`), Python `3.14.4`, 16 logical CPUs, local NVMe storage, `live_llm_used: false`.  
**Benchmark Script:** `eval/phase8_2_performance.py`  
**Reproduction Command:**
```powershell
.\venv\Scripts\python.exe eval/phase8_2_performance.py --label reproduce
```

---

## 1. Execution Mode Benchmarks (Separated by Mode)

### A. OFFLINE Deterministic Execution (12 runs: 9 clean + 3 with 40% fault recovery)
| Metric | BEFORE | AFTER | Notes |
|---|---:|---:|---|
| Requests / Successes / Failures | `12 / 12 / 0` | `12 / 12 / 0` | `3/3` recoveries succeeded in both |
| Mean Latency | `16.200 ms` | `17.572 ms` | `+1.37 ms` from stage timing & structured logging |
| Median Latency | `15.141 ms` | `17.092 ms` | Consistent sub-20 ms |
| P95 / P99 Latency | `23.537 / 26.754 ms` | `24.061 / 26.829 ms` | Tight tail bounds |
| Throughput | `61.65 req/s` | `56.89 req/s` | Offline single-thread |
| CPU Time / Peak `tracemalloc` | `0.1719 s` / `0.427 MB` | `0.2031 s` / `0.432 MB` | Flat memory footprint |

### B. MOCK Remote LLM + Mock Connector Execution (12 runs)
| Metric | BEFORE | AFTER |
|---|---:|---:|
| Requests / Successes / Failures | `12 / 12 / 0` | `12 / 12 / 0` |
| Mean / Median Latency | `19.992 / 19.938 ms` | `20.393 / 20.164 ms` |
| P95 / P99 Latency | `21.065 / 21.372 ms` | `21.397 / 21.860 ms` |
| Throughput | `49.94 req/s` | `48.96 req/s` |
| CPU Time / Peak `tracemalloc` | `0.2500 s` / `0.384 MB` | `0.2500 s` / `0.387 MB` |

*(Live connector-only network benchmarks were not re-run in this script to keep measurements deterministic and independent of third-party network jitter; historical live connector latency remains documented in Phase 7.4/7.5 reports.)*

---

## 2. Backend API Read Endpoints (15 requests each, real `logs/` & `results/`)

| Endpoint | BEFORE Mean | AFTER Mean | Delta | BEFORE Median | AFTER Median | BEFORE P95 | AFTER P95 | Max Payload |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `/api/experiments` | `103.428 ms` | **`26.008 ms`** | **-74.9%** | `102.268 ms` | `22.193 ms` | `108.404 ms` | `40.120 ms` | `7,241 B` |
| `/api/traces?limit=50` | `197.900 ms` | **`69.298 ms`** | **-65.0%** | `192.851 ms` | `51.007 ms` | `216.857 ms` | `134.059 ms` | `28,858 B` |
| `/api/analytics` | `71.310 ms` | **`25.272 ms`** | **-64.6%** | `70.746 ms` | `22.734 ms` | `77.620 ms` | `34.894 ms` | `98,221 B` |
| `/api/datasets` | `36.260 ms` | **`20.117 ms`** | **-44.5%** | `34.878 ms` | `19.312 ms` | `42.415 ms` | `23.352 ms` | `2,935 B` |
| `/api/settings` | `25.611 ms` | **`16.346 ms`** | **-36.2%** | `25.488 ms` | `14.805 ms` | `26.446 ms` | `23.472 ms` | `4,822 B` |
| `/api/health` | `53.681 ms` | **`35.313 ms`** | **-34.2%** | `49.345 ms` | `30.981 ms` | `76.731 ms` | `51.464 ms` | `3,775 B` |
| `/api/connectors/overview` | `16.314 ms` | **`14.012 ms`** | **-14.1%** | `15.723 ms` | `13.801 ms` | `19.807 ms` | `15.394 ms` | `2,485 B` |

*(Note: AFTER means include the 1 cold cache-priming request per endpoint; warm steady-state medians are even lower, e.g., `51.01 ms` for `/api/traces?limit=50` and `22.19 ms` for `/api/experiments`.)*

---

## 3. ConversationStore Storage Scaling

| Operation | BEFORE Mean | AFTER Mean | Delta | BEFORE P95 | AFTER P95 |
|---|---:|---:|---:|---:|---:|
| Create + 4 atomic turns (40 threads) | `47.552 ms` (`18.83 ops/s`) | **`22.617 ms`** (`41.23 ops/s`) | **-52.4% (2.10×)** | `68.800 ms` | **`26.883 ms`** |
| `list_conversations` (5 threads) | `2.448 ms` | **`0.904 ms`** | **-63.1%** | `2.625 ms` | **`1.003 ms`** |
| `list_conversations` (40 threads) | `11.241 ms` | **`1.534 ms`** | **-86.4% (7.33×)** | `11.628 ms` | **`1.607 ms`** |
| `get_conversation` (20 reads) | `1.471 ms` | `1.663 ms` | `+0.19 ms` | `1.646 ms` | `1.939 ms` |
| Leftover `.tmp` files | `0` | `0` | Clean | — | — |

---

## 4. Concurrent Multi-Session Load & Chromium Dashboard

| Metric | BEFORE | AFTER |
|---|---|---|
| **6-Worker Concurrent Sessions (18 queries)** | `18/18` ok, mean `192.55 ms`, P95 `228.04 ms`, `25.44 req/s`, peak `2.758 MB` | `18/18` ok, mean `196.69 ms`, P95 **`214.51 ms`**, `25.03 req/s`, peak `2.656 MB`, isolation `True` |
| **Chromium Initial Page Load (3 runs)** | mean `969.82 ms`, median `960.96 ms`, **`10` API calls/load**, `0` console/page errors | mean **`642.04 ms`** (**-33.8%**), median **`641.85 ms`**, **`6` API calls/load**, `0` console/page errors |
| **Chromium Cold Tab Switch (`logs → workspace`)** | `238.69 ms` *(traces preloaded eagerly)* | `376.83 ms` *(lazy-loaded on first tab click; cached 5s thereafter)* |
