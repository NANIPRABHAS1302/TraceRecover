# TraceRecover Experimental Evaluation and Validation

## Final 100-Task Multi-Hop Benchmark Run (Primary Measured Results)

The expanded 100-task multi-hop benchmark (`eval/tasks.json`, dataset version `2.0`, SHA-256 `7e3a535c6609021ace84acc86e62fbc641ca6f567feac77a2d5b457f61f5050c`) was executed across all 5 categories (`multi_hop_search_math`, `multi_record_api_aggregation`, `argument_repair_tool_switch`, `missing_evidence_abstention`, and `numeric_contradiction_trap`; 20 tasks each, 70 answerable and 30 abstention-labeled) on Python 3.14.4 / Windows 11 with `local`/`deterministic-local`, seed `42`, and failure injection rates `0%`, `20%`, and `40%`.

Full saved output is at `results/phase8-run-20261123T183333634001Z/`; per-task traces and phase reports are under `logs/evaluation/phase8-run-20261123T183333634001Z/`. Execution and step-level telemetry exports are also saved in `data/tracerecover_execution_telemetry.csv` (600 rows) and `data/tracerecover_step_level_telemetry.csv` (1,769 rows).

### Baseline vs. Full TraceRecover (100 Tasks, Seed 42)

| Failure rate | Baseline success (95% Wilson CI) | TraceRecover success (95% Wilson CI) | Baseline hallucination | TraceRecover hallucination | Baseline unsupported claim rate | TraceRecover unsupported claim rate | TraceRecover recovery | TraceRecover evidence coverage | TraceRecover ECE (n) | TraceRecover abstention (C/I/M; Prec / Rec) | Baseline latency mean/med (ms) | TraceRecover latency mean/med (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0% | 86/100 = 0.860 [0.779, 0.915] | 100/100 = 1.000 [0.963, 1.000] | 13/99 = 0.131 | 0/70 = 0.000 | 0.069 | 0.000 | 0/10 = 0.000 | 0.750 | 0.1964 (100) | 30/0/0 (1.000 / 1.000) | 2.703 / 2.420 | 6.652 / 6.409 |
| 20% | 58/100 = 0.580 [0.482, 0.672] | 89/100 = 0.890 [0.814, 0.937] | 40/99 = 0.404 | 0/60 = 0.000 | 0.219 | 0.000 | 22/42 = 0.524 | 0.650 | 0.2125 (100) | 30/10/0 (0.750 / 1.000) | 2.157 / 2.161 | 6.712 / 6.425 |
| 40% | 40/100 = 0.400 [0.309, 0.498] | 79/100 = 0.790 [0.700, 0.858] | 57/99 = 0.576 | 0/49 = 0.000 | 0.364 | 0.000 | 35/69 = 0.507 | 0.535 | 0.2348 (100) | 30/21/0 (0.588 / 1.000) | 2.141 / 2.158 | 6.929 / 6.394 |

Local deterministic runs incur `0` LLM tokens and `$0.00` estimated API cost (`total_tokens=0`, `total_cost_usd=0.0`); remote provider token/cost accounting is implemented and unit-tested in `config.py` and `tests/test_llm_client.py`.

### Ablation Comparison (100 Tasks, Seed 42)

| Failure rate | Full TraceRecover success (hallucination) | Without grounding success (hallucination) | Without uncertainty success (hallucination) | Without localization success (hallucination / recovery) |
|---:|---:|---:|---:|---:|
| 0% | 100/100 (0.000) | 90/100 (0.111) | 80/100 (0.200) | 100/100 (0.000 / 0/10) |
| 20% | 89/100 (0.000) | 79/100 (0.125) | 69/100 (0.300) | 73/100 (0.185 / 0/42) |
| 40% | 79/100 (0.000) | 70/100 (0.149) | 57/100 (0.430) | 53/100 (0.364 / 0/75) |

### Validation Threshold Calibration Diagnostic (`uncertainty/calibrator.py`)

Running `calibrate_thresholds()` across the 300 TraceRecover validation outputs (`0%`, `20%`, `40%` combined) with `target_abstain_precision=0.85` produced `answer_threshold=0.25`, `abstain_threshold=0.3214`, achieving abstention recall `1.000`, abstention precision `0.744`, abstention F1 `0.853`, clean answer precision `0.994`, and clean answer coverage `0.848`. Because unrecovered tool faults at 20%–40% force strict grounding abstentions on answerable tasks, `target_precision_met=False` is explicitly reported rather than masked.

### Generated SVG Plots (`results/phase8-run-20261123T183333634001Z/plots/`)

- `task_success.svg`
- `hallucination_rate.svg`
- `recovery_rate.svg`
- `ablation_success.svg`
- `average_latency.svg`
- `calibration.svg`

---

## Earlier Phase 8 Post-Fix 30-Task Validation Run (Historical Reference)

The Phase 5 search-evidence correction was validated by rerunning the complete
benchmark after the search “no local evidence found” sentinel had been
excluded from eligible evidence. The run used dataset version 1.0, all 30
tasks, local/deterministic-local provider, seed 42, failure rates 0%, 20%, and
40%, and was executed on Python 3.14.4 / Windows 11. Its timestamp is
`2026-11-12T18:32:23.375791+00:00`. Full output is retained at
`results/phase8-run-20261112T183222398011Z/`; per-task traces and phase reports
are under the identically named `logs/evaluation/` run directory.

| Failure rate | Baseline success (95% Wilson CI) | TraceRecover success (95% Wilson CI) | Baseline hallucination | TraceRecover hallucination | TraceRecover recovery | TraceRecover ECE (n) | Correct / incorrect / missed abstentions (TraceRecover) | Baseline mean / median latency (ms) | TraceRecover mean / median latency (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0% | 30/30 = 1.000 [0.886, 1.000] | 30/30 = 1.000 [0.886, 1.000] | 0/30 = 0.000 | 0/28 = 0.000 | 0/1 = 0.000 | 0.0548 (30) | 2 / 0 / 0 | 1.916 / 1.820 | 6.359 / 6.217 |
| 20% | 23/30 = 0.767 [0.591, 0.882] | 26/30 = 0.867 [0.703, 0.947] | 6/30 = 0.200 | 0/24 = 0.000 | 3/8 = 0.375 | 0.0727 (30) | 2 / 4 / 0 | 1.898 / 1.815 | 6.208 / 5.751 |
| 40% | 19/30 = 0.633 [0.455, 0.781] | 25/30 = 0.833 [0.664, 0.927] | 10/30 = 0.333 | 0/23 = 0.000 | 6/12 = 0.500 | 0.0894 (30) | 2 / 5 / 0 | 1.875 / 1.805 | 9.436 / 6.666 |

Hallucination counts are hallucinating answers / answers containing factual
claims. Unsupported and contradicted claim counts are preserved separately in
the CSV/JSON; no contradicted claims occurred in this run. Recovery is
successful incidents / detected incidents. The 0% denominator includes one
natural missing-ticket error, not an injected failure. Baseline ECE is
unavailable because the baseline emits no confidence signal.

The prior missed-abstention behavior for the two unanswerable tasks did not
recur in this run: TraceRecover correctly abstained on both at each failure
rate. At 20% and 40%, four and five answerable tasks respectively were also
abstained, which explains the incorrect-abstention counts and is a measurable
precision limitation. ECE values are empirical diagnostics of `1 - heuristic
uncertainty`; they do not establish probability calibration.

The full and ablation task-success counts were:

| Failure rate | Full TraceRecover | Without grounding | Without uncertainty | Without localization |
|---:|---:|---:|---:|---:|
| 0% | 30/30 | 30/30 | 29/30 | 30/30 |
| 20% | 26/30 | 26/30 | 25/30 | 23/30 |
| 40% | 25/30 | 25/30 | 24/30 | 19/30 |

The no-localization ablation recovered 0/8 detected incidents at 20% and 0/13
at 40%, versus 3/8 and 6/12 for full TraceRecover. This supports the
implementation-specific observation that localization is used by this
recovery path, but the small, single-seed benchmark does not establish a
general causal effect.

Six dependency-free SVGs are included in the run folder:
`plots/task_success.svg`, `plots/hallucination_rate.svg`,
`plots/recovery_rate.svg`, `plots/ablation_success.svg`,
`plots/average_latency.svg`, and `plots/calibration.svg`. Undefined metrics
remain `N/A`; denominators are shown where relevant. These charts visualize
the saved run values and add no new measurements.

The root-level Phase 7 result files and the historical table below were
preserved. That earlier run predates this validation rerun and should not be
used to describe the updated search-abstention behavior.

## Objective

Compare a no-recovery baseline ReAct agent with TraceRecover under seeded tool
failures, then remove grounding, uncertainty/abstention, and localization one
feature at a time. This is a small, reproducible local experiment, not a claim
of statistical significance or broad real-world performance.

## Experimental setup

- Dataset: `eval/tasks.json`, version `1.0`, 30 deterministic local tasks:
  6 general-knowledge/search, 6 mathematics, 6 safe-Python, 6 mock-ticket API,
  3 multiple-evidence, and 3 multi-tool tasks. The set includes two
  unanswerable tasks.
- Provider/model: `local` / `deterministic-local`; no external API calls.
- Conditions: per-tool-invocation failure injection rates 0%, 20%, and 40%.
- Seed: `42`. Task seeds are `42 + task_index * 1009`. Each recovery injector
  uses a separate seed offset of `1,000,003`, so recovery attempts do not shift
  the planned-call random stream.
- Recovery limits: one retry per retry strategy, at most four recovery
  attempts, and a 15-second recovery time limit.
- Baseline: same dataset-authored tool plan, tools, rates, and task seeds, with
  failure detection, localization, recovery, grounding, and uncertainty
  disabled. The evaluator's offline detector is used only to count failures
  for metrics; it does not affect baseline behavior.
- TraceRecover: all phases 1–6 enabled. Ablations disable one of grounding,
  uncertainty/abstention, or localization while retaining the other phases.
- Planner control: every task supplies a deterministic tool-action plan and
  final-answer template. This reduces action-selection variability but means
  planner quality is **not evaluated**.
- Latency: wall-clock duration of `ReactAgent.run`, including local
  verification and report/trace writes. No network latency is present.

The run metadata in the JSON results records the dataset SHA-256, Python and
platform versions, provider/model, seed strategy, failure rates, feature flags,
thresholds, task count, and timestamp. The measured environment was Python
3.14.4 on Windows 11.

## Metric definitions

- **Task success:** completed expected facts for answerable tasks; for an
  unanswerable task, an explicit refusal or an `ABSTAIN` decision. The separate
  abstention metrics require the actual `ABSTAIN` decision.
- **Recovery rate:** recovered detected failure incidents / detected failure
  incidents. The value is unavailable if the denominator is zero. The 0%
  condition still includes a natural missing-ticket API error, so its recovery
  denominator is one; injected failures and natural failures are separately
  counted in the JSON and CSV.
- **Hallucination rate:** answers with at least one unsupported or contradicted
  factual claim / answers with factual claims. Claims are checked against the
  task's expected and contradictory gold facts. Unsupported and contradicted
  claim counts are reported separately. An `ABSTAIN` response withholds its
  original answer, so that answer is not included in the user-facing
  hallucination denominator.
- **ECE:** ten equal-width confidence bins over [0, 1], with confidence defined
  as the Phase 6 heuristic `1 - uncertainty_score`; the last bin includes 1.
  Baseline ECE is unavailable because the baseline emits no confidence score.
  ECE is an empirical diagnostic, not a claim that the heuristic is calibrated.
- **Abstention:** reports correct abstentions, incorrect abstentions, missed
  abstentions, precision, and recall against the two dataset-labeled
  unanswerable tasks.
- **Latency:** mean and median measured task duration in milliseconds.
- **Intervals:** task-success rates include two-sided 95% Wilson intervals.
  No significance test was performed.

## Baseline vs. TraceRecover

Each condition contains 30 tasks. Hallucination values are
`hallucinating_answers / answers_with_claims`; latency is
`mean / median milliseconds`.

| Injected failure rate | Baseline success (95% Wilson CI) | TraceRecover success (95% Wilson CI) | Baseline hallucination | TraceRecover hallucination | TraceRecover recovery | TraceRecover ECE (n) | Baseline latency | TraceRecover latency |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0% | 30/30 = 1.000 [0.886, 1.000] | 30/30 = 1.000 [0.886, 1.000] | 0/30 = 0.000 | 0/29 = 0.000 | 0/1 = 0.000 | 0.0333 (30) | 1.858 / 1.705 | 6.146 / 6.056 |
| 20% | 23/30 = 0.767 [0.591, 0.882] | 26/30 = 0.867 [0.703, 0.947] | 6/30 = 0.200 | 0/25 = 0.000 | 3/8 = 0.375 | 0.0513 (30) | 1.885 / 1.826 | 5.958 / 5.674 |
| 40% | 19/30 = 0.633 [0.455, 0.781] | 25/30 = 0.833 [0.664, 0.927] | 10/30 = 0.333 | 0/24 = 0.000 | 6/12 = 0.500 | 0.0679 (30) | 1.905 / 1.852 | 6.683 / 6.462 |

Baseline ECE is N/A (no confidence signal). TraceRecover's observed abstention
counts, in `correct / incorrect / missed` order, were `1 / 0 / 1` at 0%,
`1 / 4 / 1` at 20%, and `1 / 5 / 1` at 40%. The baseline made no abstention
decisions and missed both labeled unanswerable tasks in each condition.

The 0% result includes one naturally failing lookup of nonexistent ticket
`T-999`, not an injected failure. At 20%, TraceRecover recorded 7 injected
planned-tool failures and 2 injected recovery-attempt failures. At 40%, it
recorded 12 injected planned-tool failures and 4 injected recovery-attempt
failures. These are realized counts from the seeded runs; the configured
failure percentage is a per-invocation probability, not a guaranteed count.

## Ablations

| Failure rate | Full success | Without grounding | Without uncertainty | Without localization |
|---:|---:|---:|---:|---:|
| 0% | 30/30 | 30/30 | 29/30 | 30/30 |
| 20% | 26/30 | 26/30 | 25/30 | 23/30 |
| 40% | 25/30 | 25/30 | 24/30 | 19/30 |

At 40%, recovery was 6/12 incidents for full TraceRecover, 6/12 without
grounding, 6/12 without uncertainty, and 0/13 without localization. At 20%,
the corresponding rates were 3/8, 3/8, 3/8, and 0/8. The no-localization
results are consistent with localization being needed by this recovery path;
the small sample does not establish a general causal effect. Full per-condition
hallucination, abstention, ECE, latency, and denominator data are in
`results/ablation_results.json` and `results/metrics_summary.csv`.

## Observed evidence example

For `MULTI-04`, the final answer was `sum=7; product=12`. The saved grounding
report labels `sum=7` as `SUPPORTED` by calculator trace step 1 and `product=12`
as `SUPPORTED` by Python trace step 2. The final cited answer was:

```text
sum=7 [SUPPORTED; evidence: step 1]
product=12 [SUPPORTED; evidence: step 2]
```

This case also revealed and fixed a Phase 5 issue: numeric outputs from
unrelated tool actions were being treated as contradictions. The checker now
uses the logged arithmetic operation to avoid cross-comparing an addition
claim with a multiplication result (and vice versa). A regression test covers
both claims and their step citations.

In the earlier Phase 7 run, a no-result search sentinel was mistakenly eligible
as evidence, causing one missed abstention. Phase 8 excluded that sentinel from
evidence and added unit and agent-level regression tests. The post-fix run
reported above correctly abstained on both unanswerable tasks at every
configured failure rate. The root-level Phase 7 result JSON/CSV are preserved
as historical artifacts and have not been rewritten.

## Interpretation and limitations

In these measured runs, TraceRecover achieved higher task success and a lower
answer-level unsupported/contradicted rate than the baseline at 20% and 40%
failures, with recovery succeeding in 3/8 and 6/12 detected incidents. The
full system also had higher local latency, approximately 6 ms versus 2 ms on
this machine, because it performs trace analysis, verification, and reporting.
At 0% the task-success rates were equal. These observations apply only to this
dataset, planner, seed, implementation, and runtime; they are not statistically
significant or evidence of general performance.

Limitations and threats to validity:

- Thirty synthetic/local tasks provide a small sample and limited topic
  coverage.
- Dataset-authored action plans bypass free-form ReAct planning and control
  tool selection.
- Failure injection models independent seeded tool-call faults, not
  correlated outages or realistic service latency.
- One random seed was used; rates have binomial uncertainty and are not
  significance tests.
- Claim extraction and gold-fact matching are deterministic heuristics; the
  evaluator may miss paraphrases or nuanced factual errors.
- ECE is based on a heuristic confidence score and only 30 samples per
  condition; it must not be interpreted as calibrated probability.
- Abstention evaluation has only two unanswerable tasks; the Phase 8 run also
  abstained on several answerable tasks at higher failure rates.
- The new plotter renders SVG with the standard library. The diagrams
  visualize measured CSV/JSON values and do not increase the sample size.

## Reproduction

From the project root, run the matched baseline, full system, ablations, and
plots into a new timestamped directory:

```powershell
python -m eval.run_all --seed 42
```

The runner supports `--failure-rates`, `--dataset-path`, `--num-tasks`,
`--output-dir`, `--log-root`, `--seed`, `--answer-threshold`, and
`--abstain-threshold`. It refuses to overwrite existing files unless
`--overwrite` is explicitly supplied. Saved output includes baseline,
TraceRecover, ablation, comparison-summary JSON, `metrics_summary.csv`, and
six SVG plots. Per-task traces and phase reports are saved separately under
the corresponding `logs/evaluation/` run directory.

The original `python -m eval.run_baseline` and
`python -m eval.run_tracerecover` commands remain available for compatibility;
they write to `results/` directly by default and do not provide the timestamped
collision protection of `eval.run_all`.
