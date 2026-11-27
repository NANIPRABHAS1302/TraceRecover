# Repository Cleanup & Preservation Audit (Phase 5)

## Preserved Assets (Untouched)

- Core research modules: `agent/react_agent.py`, `agent/planner.py`, `grounding/`, `healing/`, `uncertainty/`, `eval/runner.py`, `eval/metrics.py`, `eval/tasks.json`, `tools/calculator.py`, `tools/python_exec.py`, `tools/mock_api.py`, `tools/search_tool.py`, `tools/failure_injector.py`, `config.py`.
- Canonical benchmark runs: all five `results/phase8-run-*` directories and root `results/` reports.
- Populated Phase 4 validation report: `results/phase4-validation-20261124T160745Z/`.
- Local environment configuration: `.env`.

## Quarantined & Removed Obsolete Items

| Item | Reason | Action |
|---|---|---|
| `frontend/` | Superseded dark React/Vite draft (`static/index.html` is the active 14-section white dashboard; `FRONTEND_DIST_DIR` fallback removed from `web_dashboard.py`) | Quarantined to `.quarantine/` -> full test verification -> deleted |
| `results/phase4-validation-20261124T160204Z/` | Empty aborted Phase 4 output folder (`0` files) | Quarantined -> deleted after verification |
| `results/phase4-validation-20261124T160303Z/` | Empty aborted Phase 4 output folder (`0` files) | Quarantined -> deleted after verification |

## Runtime Output Hygiene

`.gitignore` and `.dockerignore` ignore runtime connector state (`data/connectors/`), logs (`logs/`), versioned uploads (`data/versions/`), unseen runs (`data/unseen_benchmarks/`), and `.quarantine/`.
