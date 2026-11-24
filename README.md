# TraceRecover

**An Evidence-Grounded Self-Healing and Uncertainty-Aware Framework for Reliable Tool-Augmented Large Language Model Agents**

TraceRecover is a local research prototype that records tool traces, detects
and localizes failures, attempts bounded recovery, checks answer claims against
successful tool outputs, and warns or abstains when evidence is weak.

## Quick start (Windows PowerShell)

Use Python 3.10 or newer. From the project root:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py --provider local
```

The local provider is deterministic, offline, and makes no API calls. The
included demo uses local search facts, a calculator, safe Python evaluation,
and a mock ticket service.

For a remote provider, set `LLM_PROVIDER` and the matching API key in the
ignored `.env` file. Supported providers are Gemini, Anthropic, OpenAI, and
local. Never commit `.env` or paste a key into source code.

## Run the agent

```powershell
python app.py --provider local
python app.py --provider local --question "What is the capital of France?"
python app.py --provider local --failure-rate 20 --seed 1 --question "What is the capital of France?"
python app.py --provider local --failure-rate 40 --seed 42
```

The CLI reports the original answer for audit, detected failures, recovery
attempts, claim labels and evidence-step citations, grounding score, available
uncertainty signals, decision, and final user-facing response. Trace and report
files are written under `logs/`.

The thresholds are configurable:

```powershell
python app.py --provider local --answer-threshold 0.1 --abstain-threshold 0.55
```

## Run the complete evaluation

The safest one-command workflow runs the baseline, full TraceRecover, and the
three ablations using the same dataset, rates, and seed, then writes SVG plots:

```powershell
python -m eval.run_all --seed 42
```

By default, results go into a new timestamped directory under `results/`, and
traces/reports into a corresponding subdirectory under `logs/evaluation/`.
Existing outputs are not replaced implicitly. To choose another destination:

```powershell
python -m eval.run_all `
  --output-dir results\class-demo `
  --log-root logs\evaluation\class-demo `
  --failure-rates 0 20 40 `
  --seed 42
```

Add `--num-tasks 5` for a quick smoke run. Use `--overwrite` only when you
intend to replace result and plot files in that specific output directory.
The evaluation requires the deterministic local provider and makes no external
LLM/API calls.

The original runner commands remain available:

```powershell
python -m eval.run_baseline --seed 42
python -m eval.run_tracerecover --seed 42
```

Those legacy commands write to the top-level `results/` files by default; use
`eval.run_all` for timestamped, collision-protected runs.

To regenerate plots from an existing evaluation directory:

```powershell
python -m eval.plot_results `
  --results-dir results\phase8-run-20261112T183222398011Z
```

The plotter uses only the Python standard library. It writes SVGs for task
success, answer-level hallucination rate, recovery rate, ablation success,
latency, and calibration. Undefined values are shown as `N/A`; denominators
are included with ratio points. The calibration diagram is a diagnostic of the
heuristic confidence score, not proof of calibrated probabilities.

## Results from the Phase 8 validation run

The measured run used the 30-task dataset, local provider, seed 42, and failure
rates 0%, 20%, and 40%. It ran on Python 3.14.4 / Windows 11. The fresh output
is in `results/phase8-run-20261112T183222398011Z/`; the earlier Phase 7 result
files at the root of `results/` remain unchanged.

| Failure rate | Baseline success | TraceRecover success | Baseline hallucinating answers | TraceRecover hallucinating answers | TraceRecover recovery | TraceRecover ECE |
|---:|---:|---:|---:|---:|---:|---:|
| 0% | 30/30 (1.000) | 30/30 (1.000) | 0/30 (0.000) | 0/28 (0.000) | 0/1 | 0.0548 |
| 20% | 23/30 (0.767) | 26/30 (0.867) | 6/30 (0.200) | 0/24 (0.000) | 3/8 | 0.0727 |
| 40% | 19/30 (0.633) | 25/30 (0.833) | 10/30 (0.333) | 0/23 (0.000) | 6/12 | 0.0894 |

Hallucination values are answers containing unsupported or contradicted claims
over answers with factual claims; recovery values are successful recoveries
over detected failures. At 0% there was one natural missing-ticket failure,
which explains the non-zero recovery denominator. Task-success intervals,
abstention results, all ablations, and detailed methodology are in
[docs/experimental_evaluation.md](docs/experimental_evaluation.md) and the
run-specific JSON/CSV files. These are small, synthetic/local measurements,
not claims of statistical significance or general performance.

## Architecture and learning material

- [System architecture](docs/system_architecture.md): components, evidence
  lifecycle, and a Mermaid pipeline diagram.
- [Experimental evaluation](docs/experimental_evaluation.md): setup, metrics,
  measured results, ablations, and validity limits.
- [Viva preparation](docs/viva_preparation.md): concise explanations of the
  main research concepts.
- [Live demo script](docs/demo_script.md): reproducible 8–10 minute sequence
  with normal, recovery, warning, abstention, and multi-tool examples.

## Tests

```powershell
python -m pytest -q
```

The tests use deterministic fixtures and do not require API credentials.

## Limitations

- The local provider and dataset are deliberately small and synthetic.
- The evaluation uses task-authored tool plans, so it does not measure
  free-form planner quality.
- Claim extraction and verification are deterministic heuristics; paraphrases
  and nuanced claims can be missed or conservatively left unsupported.
- One seed and 30 tasks provide limited evidence. ECE is an empirical
  diagnostic for heuristic confidence, not a statistically calibrated
  probability.
- Local tool latency does not represent network-backed service latency.
