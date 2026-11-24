"""Additive statistical validation and 5-fold cross-fitted threshold audit.

Reads saved benchmark JSON outputs and execution telemetry CSV without modifying
any core agent, grounding, recovery, or uncertainty runtime modules.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.metrics import expected_calibration_error, wilson_interval
from grounding.claim_extractor import ClaimExtractor
from grounding.evidence_checker import EvidenceChecker
from grounding.nli_verifier import DebertaNLIVerifier
from uncertainty.abstention import AbstentionPolicy
from uncertainty.calibrator import calibrate_thresholds


DEFAULT_RESULTS_DIR = (
    PROJECT_ROOT / "results" / "phase8-run-20261123T183333634001Z"
)
DEFAULT_TELEMETRY_CSV = (
    PROJECT_ROOT / "data" / "tracerecover_execution_telemetry.csv"
)
DEFAULT_TASKS_PATH = PROJECT_ROOT / "eval" / "tasks.json"

_REFUSAL_MARKERS = (
    "no local evidence found",
    "could not complete",
    "cannot complete",
    "cannot answer",
    "i am abstaining",
    "was not found",
    "not found",
)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _contains_fact(text: str, fact: str) -> bool:
    norm_text = _normalize(text)
    norm_fact = _normalize(fact)
    if not norm_text or not norm_fact:
        return False
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(norm_fact)}(?![a-z0-9])",
            norm_text,
        )
        is not None
    )


def _is_explicit_refusal(text: str) -> bool:
    norm = _normalize(text)
    return any(_normalize(marker) in norm for marker in _REFUSAL_MARKERS)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _decision_distribution(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(results)
    counts = {
        "ANSWER": 0,
        "WARN": 0,
        "ABSTAIN": 0,
        "NOT_APPLICABLE": 0,
    }
    for row in results:
        decision = str(row.get("final_decision", "NOT_APPLICABLE"))
        counts[decision] = counts.get(decision, 0) + 1
    return {
        "total": total,
        "counts": counts,
        "rates": {
            key: (count / total if total else None)
            for key, count in counts.items()
        },
    }


def _check_telemetry_parity(
    telemetry_path: Path,
    baseline_data: Mapping[str, Any],
    tracerecover_data: Mapping[str, Any],
) -> dict[str, Any]:
    """Cross-verify execution_telemetry.csv row counts and decision totals."""
    if not telemetry_path.is_file():
        return {"available": False, "parity_ok": False, "reason": "CSV not found."}

    with telemetry_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    csv_by_sys_rate: dict[tuple[str, float], list[dict[str, str]]] = {}
    for row in rows:
        key = (row["system"], float(row["failure_rate_percent"]))
        csv_by_sys_rate.setdefault(key, []).append(row)

    mismatches: list[str] = []
    for sys_name, payload in (
        ("baseline", baseline_data),
        ("tracerecover", tracerecover_data),
    ):
        for cond in payload["conditions"]:
            rate = float(cond["failure_rate_percent"])
            csv_rows = csv_by_sys_rate.get((sys_name, rate), [])
            json_rows = cond["results"]
            if len(csv_rows) != len(json_rows):
                mismatches.append(
                    f"{sys_name}@{rate:g}%: CSV has {len(csv_rows)} rows vs JSON {len(json_rows)}"
                )
                continue
            csv_succ = sum(r["success"].lower() == "true" for r in csv_rows)
            json_succ = sum(r["success"] is True for r in json_rows)
            if csv_succ != json_succ:
                mismatches.append(
                    f"{sys_name}@{rate:g}%: CSV successes {csv_succ} vs JSON {json_succ}"
                )
            csv_dec = {
                d: sum(r["final_decision"] == d for r in csv_rows)
                for d in ("ANSWER", "WARN", "ABSTAIN", "NOT_APPLICABLE")
            }
            json_dec = _decision_distribution(json_rows)["counts"]
            if csv_dec != json_dec:
                mismatches.append(
                    f"{sys_name}@{rate:g}%: decision mismatch CSV={csv_dec} vs JSON={json_dec}"
                )

    return {
        "available": True,
        "csv_path": str(telemetry_path),
        "total_csv_rows": len(rows),
        "parity_ok": len(mismatches) == 0,
        "mismatches": mismatches,
    }


def _independent_gold_and_nli_audit(
    results: Sequence[Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
    nli_checker: EvidenceChecker,
) -> dict[str, Any]:
    """Compute independent gold accuracy, selective accuracy, NLI hallucination, and zero-claim audit."""
    answerable_total = 0
    answerable_gold_correct = 0
    overall_gold_correct = 0

    non_abstained_total = 0
    selective_strict_correct = 0
    selective_task_criterion_correct = 0

    answer_only_total = 0
    answer_only_correct = 0
    warn_only_total = 0
    warn_only_correct = 0

    gold_claim_answers = 0
    gold_hallucinating_answers = 0

    nli_evaluated_answers = 0
    nli_hallucinating_answers = 0
    nli_total_claims = 0
    nli_unsupported_claims = 0
    nli_contradicted_claims = 0

    zero_claim_non_abstained: list[dict[str, Any]] = []
    extractor = ClaimExtractor()

    for row in results:
        task_id = str(row["task_id"])
        task = tasks_by_id[task_id]
        should_abstain = bool(task["should_abstain"])
        expected_facts = list(task["expected_facts"])
        contradictory_facts = list(task["contradictory_facts"])
        decision = str(row.get("final_decision", "NOT_APPLICABLE"))
        response = str(row.get("final_response", ""))
        raw_answer = row.get("answer")
        abstained = decision == "ABSTAIN"

        # Independent gold evaluation
        if should_abstain:
            is_task_correct = abstained or _is_explicit_refusal(response)
            is_strict_non_abstain_correct = False
        else:
            answerable_total += 1
            has_all_expected = bool(expected_facts) and all(
                _contains_fact(response, fact) for fact in expected_facts
            )
            has_no_contradiction = not any(
                _contains_fact(response, fact) for fact in contradictory_facts
            )
            is_task_correct = (not abstained) and has_all_expected and has_no_contradiction
            is_strict_non_abstain_correct = is_task_correct
            if is_task_correct:
                answerable_gold_correct += 1

        if is_task_correct:
            overall_gold_correct += 1

        if not abstained:
            non_abstained_total += 1
            if is_strict_non_abstain_correct:
                selective_strict_correct += 1
            if is_task_correct:
                selective_task_criterion_correct += 1
            if decision == "ANSWER":
                answer_only_total += 1
                if is_strict_non_abstain_correct:
                    answer_only_correct += 1
            elif decision == "WARN":
                warn_only_total += 1
                if is_strict_non_abstain_correct:
                    warn_only_correct += 1

            # Check zero-claim denominator exclusion (e.g. /99 explanation)
            claims = extractor.extract(raw_answer)
            if len(claims) == 0:
                zero_claim_non_abstained.append(
                    {
                        "task_id": task_id,
                        "category": task["category"],
                        "query": task["query"],
                        "raw_answer": raw_answer,
                        "final_decision": decision,
                        "should_abstain": should_abstain,
                        "task_success": bool(row.get("success")),
                        "reason": (
                            "ClaimExtractor requires at least one alphanumeric character; "
                            f"raw emitted answer {raw_answer!r} contains zero factual claims "
                            "and is therefore excluded from the claim-bearing hallucination denominator."
                        ),
                    }
                )
            else:
                gold_claim_answers += 1
                if row.get("hallucination") is True:
                    gold_hallucinating_answers += 1

                # Independent NLI-grounded verification against committed trace steps
                trace_path = Path(str(row.get("trace_path", "")))
                trace_steps = _read_jsonl(trace_path)
                failure_report_path = row.get("failure_report_path")
                failure_report = (
                    _read_json(Path(failure_report_path))
                    if failure_report_path and Path(failure_report_path).is_file()
                    else None
                )
                recovery_report_path = (
                    Path(str(trace_path).replace(".jsonl", "-recovery-report.json"))
                    if str(trace_path).endswith(".jsonl")
                    else None
                )
                recovery_report = (
                    _read_json(recovery_report_path)
                    if recovery_report_path and recovery_report_path.is_file()
                    else None
                )
                recovery_log_path = (
                    Path(str(trace_path).replace(".jsonl", "-recovery.jsonl"))
                    if str(trace_path).endswith(".jsonl")
                    else None
                )
                recovery_records = (
                    _read_jsonl(recovery_log_path) if recovery_log_path else []
                )
                nli_report = nli_checker.verify(
                    str(raw_answer),
                    trace_steps,
                    query=task["query"],
                    recovery_report=recovery_report,
                    recovery_records=recovery_records,
                    failure_report=failure_report,
                )
                if nli_report["total_claim_count"] > 0:
                    nli_evaluated_answers += 1
                    nli_total_claims += nli_report["total_claim_count"]
                    nli_unsupported_claims += nli_report["unsupported_count"]
                    nli_contradicted_claims += nli_report["contradicted_count"]
                    if (
                        nli_report["unsupported_count"] > 0
                        or nli_report["contradicted_count"] > 0
                    ):
                        nli_hallucinating_answers += 1

    total = len(results)
    return {
        "independent_gold_accuracy_all": {
            "rate": overall_gold_correct / total if total else None,
            "correct": overall_gold_correct,
            "denominator": total,
        },
        "independent_gold_accuracy_answerable": {
            "rate": (
                answerable_gold_correct / answerable_total
                if answerable_total
                else None
            ),
            "correct": answerable_gold_correct,
            "denominator": answerable_total,
        },
        "selective_accuracy_strict": {
            "description": (
                "Gold accuracy strictly over non-abstained outputs; any non-abstained "
                "response on a should_abstain=True task counts as incorrect."
            ),
            "rate": (
                selective_strict_correct / non_abstained_total
                if non_abstained_total
                else None
            ),
            "correct": selective_strict_correct,
            "denominator": non_abstained_total,
        },
        "selective_accuracy_task_criterion": {
            "description": (
                "Task-criterion accuracy over non-abstained outputs (crediting "
                "explicit textual refusal strings on should_abstain=True tasks)."
            ),
            "rate": (
                selective_task_criterion_correct / non_abstained_total
                if non_abstained_total
                else None
            ),
            "correct": selective_task_criterion_correct,
            "denominator": non_abstained_total,
        },
        "selective_accuracy_by_decision": {
            "ANSWER": {
                "rate": (
                    answer_only_correct / answer_only_total
                    if answer_only_total
                    else None
                ),
                "correct": answer_only_correct,
                "denominator": answer_only_total,
            },
            "WARN": {
                "rate": (
                    warn_only_correct / warn_only_total
                    if warn_only_total
                    else None
                ),
                "correct": warn_only_correct,
                "denominator": warn_only_total,
            },
        },
        "gold_fact_hallucination_rate": {
            "rate": (
                gold_hallucinating_answers / gold_claim_answers
                if gold_claim_answers
                else None
            ),
            "hallucinating_answers": gold_hallucinating_answers,
            "denominator": gold_claim_answers,
        },
        "nli_grounded_hallucination_rate": {
            "backend": nli_checker.nli_verifier.backend_name
            if nli_checker.nli_verifier
            else "none",
            "answer_level_rate": (
                nli_hallucinating_answers / nli_evaluated_answers
                if nli_evaluated_answers
                else None
            ),
            "hallucinating_answers": nli_hallucinating_answers,
            "denominator": nli_evaluated_answers,
            "claim_level_ungrounded_rate": (
                (nli_unsupported_claims + nli_contradicted_claims) / nli_total_claims
                if nli_total_claims
                else None
            ),
            "unsupported_claims": nli_unsupported_claims,
            "contradicted_claims": nli_contradicted_claims,
            "total_claims": nli_total_claims,
        },
        "zero_claim_non_abstained_tasks": zero_claim_non_abstained,
    }


def mcnemar_paired_test(
    baseline_results: Sequence[Mapping[str, Any]],
    tracerecover_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute exact two-sided McNemar binomial test and continuity-corrected chi2."""
    base_map = {str(r["task_id"]): bool(r["success"]) for r in baseline_results}
    trace_map = {str(r["task_id"]): bool(r["success"]) for r in tracerecover_results}
    common_ids = sorted(set(base_map).intersection(trace_map))

    n11 = n10 = n01 = n00 = 0
    for tid in common_ids:
        b = base_map[tid]
        t = trace_map[tid]
        if b and t:
            n11 += 1
        elif b and not t:
            n10 += 1  # baseline right, TraceRecover wrong
        elif not b and t:
            n01 += 1  # TraceRecover right, baseline wrong
        else:
            n00 += 1

    n_disc = n10 + n01
    if n_disc == 0:
        exact_p = 1.0
        chi2_cc = 0.0
        chi2_p = 1.0
    else:
        k = min(n10, n01)
        tail_prob = sum(math.comb(n_disc, i) for i in range(0, k + 1)) / (2 ** n_disc)
        exact_p = min(1.0, 2.0 * tail_prob)
        chi2_cc = (max(0.0, abs(n10 - n01) - 1.0) ** 2) / n_disc
        chi2_p = math.erfc(math.sqrt(chi2_cc / 2.0))

    return {
        "paired_task_count": len(common_ids),
        "contingency_table": {
            "both_correct_n11": n11,
            "baseline_only_n10": n10,
            "tracerecover_only_n01": n01,
            "both_wrong_n00": n00,
        },
        "discordant_pairs": n_disc,
        "net_gain_tasks": n01 - n10,
        "exact_two_sided_p_value": exact_p,
        "chi2_continuity_corrected": chi2_cc,
        "chi2_p_value": chi2_p,
        "significant_at_0_05": exact_p < 0.05,
        "significant_at_0_01": exact_p < 0.01,
    }


def brier_score(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute Brier score over (confidence = 1 - uncertainty, success) pairs."""
    pairs = [
        (float(r["confidence"]), 1.0 if r["success"] else 0.0)
        for r in results
        if isinstance(r.get("confidence"), (int, float))
        and not isinstance(r.get("confidence"), bool)
        and isinstance(r.get("success"), bool)
    ]
    if not pairs:
        return {"brier_score": None, "sample_count": 0}
    score = sum((conf - outcome) ** 2 for conf, outcome in pairs) / len(pairs)
    return {"brier_score": score, "sample_count": len(pairs)}


def cross_fitted_threshold_validation(
    tracerecover_data: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
    *,
    num_folds: int = 5,
    seed: int = 42,
    target_abstain_precision: float = 0.85,
) -> dict[str, Any]:
    """Perform stratified 5-fold group-by-task_id cross-fitted threshold calibration.

    Grouping by task_id ensures all failure-rate runs (0%, 20%, 40%) of any task
    stay strictly in the same fold, preventing any task or threshold leakage.
    """
    # Group all task runs across 0/20/40% by task_id
    runs_by_task: dict[str, list[dict[str, Any]]] = {}
    for cond in tracerecover_data["conditions"]:
        rate = float(cond["failure_rate_percent"])
        for row in cond["results"]:
            enriched = dict(row)
            enriched["_rate"] = rate
            runs_by_task.setdefault(str(row["task_id"]), []).append(enriched)

    # Stratify task IDs by category so every fold has balanced categories & abstention ratios
    rng = random.Random(seed)
    by_category: dict[str, list[str]] = {}
    for tid in sorted(runs_by_task.keys()):
        cat = str(tasks_by_id[tid]["category"])
        by_category.setdefault(cat, []).append(tid)

    folds: list[list[str]] = [[] for _ in range(num_folds)]
    for cat in sorted(by_category.keys()):
        tids = list(by_category[cat])
        rng.shuffle(tids)
        for idx, tid in enumerate(tids):
            folds[idx % num_folds].append(tid)

    fold_reports: list[dict[str, Any]] = []
    oof_predictions: list[dict[str, Any]] = []
    default_predictions: list[dict[str, Any]] = []

    for fold_idx in range(num_folds):
        test_tids = set(folds[fold_idx])
        train_tids = set(runs_by_task.keys()) - test_tids
        assert train_tids.isdisjoint(test_tids), "Train/test leakage detected!"

        train_records = [
            rec for tid in sorted(train_tids) for rec in runs_by_task[tid]
        ]
        test_records = [
            rec for tid in sorted(test_tids) for rec in runs_by_task[tid]
        ]

        cal = calibrate_thresholds(
            train_records, target_abstain_precision=target_abstain_precision
        )
        fitted_policy = AbstentionPolicy(
            answer_threshold=cal.answer_threshold,
            abstain_threshold=cal.abstain_threshold,
            require_grounding=True,
        )
        default_policy = AbstentionPolicy(
            answer_threshold=0.15,
            abstain_threshold=0.55,
            require_grounding=True,
        )

        fold_oof = _apply_policy_to_records(test_records, tasks_by_id, fitted_policy)
        fold_def = _apply_policy_to_records(test_records, tasks_by_id, default_policy)
        oof_predictions.extend(fold_oof)
        default_predictions.extend(fold_def)

        fold_summary = _summarize_policy_predictions(fold_oof)
        fold_reports.append(
            {
                "fold": fold_idx + 1,
                "train_task_count": len(train_tids),
                "test_task_count": len(test_tids),
                "train_run_count": len(train_records),
                "test_run_count": len(test_records),
                "fitted_answer_threshold": cal.answer_threshold,
                "fitted_abstain_threshold": cal.abstain_threshold,
                "train_target_precision_met": cal.target_precision_met,
                "held_out_metrics": fold_summary,
            }
        )

    ans_thresholds = [f["fitted_answer_threshold"] for f in fold_reports]
    abs_thresholds = [f["fitted_abstain_threshold"] for f in fold_reports]
    return {
        "cv_protocol": (
            f"{num_folds}-fold stratified group-by-task_id cross-fitting "
            f"(seed={seed}; 80% train tasks -> 20% held-out tasks across all failure rates; "
            "zero task overlap between train and test folds)."
        ),
        "target_abstain_precision": target_abstain_precision,
        "fitted_threshold_summary": {
            "answer_threshold_mean": statistics.mean(ans_thresholds),
            "answer_threshold_stdev": (
                statistics.stdev(ans_thresholds) if len(ans_thresholds) > 1 else 0.0
            ),
            "abstain_threshold_mean": statistics.mean(abs_thresholds),
            "abstain_threshold_stdev": (
                statistics.stdev(abs_thresholds) if len(abs_thresholds) > 1 else 0.0
            ),
        },
        "out_of_fold_aggregate": _summarize_policy_predictions(oof_predictions),
        "default_threshold_baseline_0_15_0_55": _summarize_policy_predictions(
            default_predictions
        ),
        "folds": fold_reports,
    }


def _apply_policy_to_records(
    records: Sequence[Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
    policy: AbstentionPolicy,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for rec in records:
        task = tasks_by_id[str(rec["task_id"])]
        g_path = rec.get("grounding_report_path")
        u_path = rec.get("uncertainty_report_path")
        grounding_report = (
            _read_json(Path(g_path)) if g_path and Path(g_path).is_file() else {}
        )
        uncertainty_report = (
            _read_json(Path(u_path)) if u_path and Path(u_path).is_file() else {}
        )
        decision = policy.decide(grounding_report, uncertainty_report)
        abstained = decision.final_decision == "ABSTAIN"
        should_abstain = bool(task["should_abstain"])
        if should_abstain:
            success = abstained or _is_explicit_refusal(decision.final_response)
            strict_non_abstain_ok = False
        else:
            expected_facts = task["expected_facts"]
            contradictory_facts = task["contradictory_facts"]
            has_expected = bool(expected_facts) and all(
                _contains_fact(decision.final_response, fact)
                for fact in expected_facts
            )
            no_contra = not any(
                _contains_fact(decision.final_response, fact)
                for fact in contradictory_facts
            )
            success = (not abstained) and has_expected and no_contra
            strict_non_abstain_ok = success
        outcomes.append(
            {
                "task_id": rec["task_id"],
                "failure_rate_percent": rec["_rate"],
                "should_abstain": should_abstain,
                "decision": decision.final_decision,
                "success": success,
                "strict_non_abstain_ok": strict_non_abstain_ok,
            }
        )
    return outcomes


def _summarize_policy_predictions(
    outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = len(outcomes)
    successes = sum(o["success"] for o in outcomes)
    non_abstained = [o for o in outcomes if o["decision"] != "ABSTAIN"]
    sel_correct = sum(o["strict_non_abstain_ok"] for o in non_abstained)

    tp = sum(o["should_abstain"] and o["decision"] == "ABSTAIN" for o in outcomes)
    fp = sum(
        (not o["should_abstain"]) and o["decision"] == "ABSTAIN" for o in outcomes
    )
    fn = sum(o["should_abstain"] and o["decision"] != "ABSTAIN" for o in outcomes)

    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        (2 * prec * rec / (prec + rec))
        if (prec is not None and rec is not None and (prec + rec) > 0)
        else None
    )
    return {
        "sample_count": total,
        "task_success_rate": successes / total if total else None,
        "selective_accuracy_strict": (
            sel_correct / len(non_abstained) if non_abstained else None
        ),
        "non_abstained_count": len(non_abstained),
        "answer_count": sum(o["decision"] == "ANSWER" for o in outcomes),
        "warn_count": sum(o["decision"] == "WARN" for o in outcomes),
        "abstain_count": sum(o["decision"] == "ABSTAIN" for o in outcomes),
        "abstain_precision": prec,
        "abstain_recall": rec,
        "abstain_f1": f1,
    }


def run_statistical_validation(
    results_dir: Path = DEFAULT_RESULTS_DIR,
    telemetry_csv: Path = DEFAULT_TELEMETRY_CSV,
    tasks_path: Path = DEFAULT_TASKS_PATH,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Generate the complete statistical validation report from saved artifacts."""
    baseline_data = _read_json(results_dir / "baseline_results.json")
    tracerecover_data = _read_json(results_dir / "tracerecover_results.json")
    tasks_payload = _read_json(tasks_path)
    tasks_by_id = {str(t["task_id"]): t for t in tasks_payload["tasks"]}

    base_by_rate = {
        float(c["failure_rate_percent"]): c["results"]
        for c in baseline_data["conditions"]
    }
    trace_by_rate = {
        float(c["failure_rate_percent"]): c["results"]
        for c in tracerecover_data["conditions"]
    }
    rates = sorted(set(base_by_rate).intersection(trace_by_rate))

    nli_checker = EvidenceChecker(
        use_nli=True, nli_verifier=DebertaNLIVerifier(force_fallback=True)
    )

    per_condition: list[dict[str, Any]] = []
    pooled_base: list[dict[str, Any]] = []
    pooled_trace: list[dict[str, Any]] = []

    for rate in rates:
        b_rows = base_by_rate[rate]
        t_rows = trace_by_rate[rate]
        # Build unique composite IDs for pooled paired McNemar
        for row in b_rows:
            cp = dict(row)
            cp["task_id"] = f"{row['task_id']}@{rate:g}"
            pooled_base.append(cp)
        for row in t_rows:
            cp = dict(row)
            cp["task_id"] = f"{row['task_id']}@{rate:g}"
            pooled_trace.append(cp)

        b_succ = sum(bool(r["success"]) for r in b_rows)
        t_succ = sum(bool(r["success"]) for r in t_rows)

        per_condition.append(
            {
                "failure_rate_percent": rate,
                "baseline": {
                    "task_success_rate": b_succ / len(b_rows),
                    "task_success_count": b_succ,
                    "task_count": len(b_rows),
                    "wilson_95_ci": wilson_interval(b_succ, len(b_rows)),
                    "decisions": _decision_distribution(b_rows),
                    "accuracy_and_hallucination": _independent_gold_and_nli_audit(
                        b_rows, tasks_by_id, nli_checker
                    ),
                    "brier_score": brier_score(b_rows),
                    "ece": expected_calibration_error(b_rows, bins=10),
                },
                "tracerecover": {
                    "task_success_rate": t_succ / len(t_rows),
                    "task_success_count": t_succ,
                    "task_count": len(t_rows),
                    "wilson_95_ci": wilson_interval(t_succ, len(t_rows)),
                    "decisions": _decision_distribution(t_rows),
                    "accuracy_and_hallucination": _independent_gold_and_nli_audit(
                        t_rows, tasks_by_id, nli_checker
                    ),
                    "brier_score": brier_score(t_rows),
                    "ece": expected_calibration_error(t_rows, bins=10),
                },
                "paired_mcnemar_test": mcnemar_paired_test(b_rows, t_rows),
            }
        )

    pooled_mcnemar = mcnemar_paired_test(pooled_base, pooled_trace)
    pooled_trace_brier = brier_score(pooled_trace)
    pooled_trace_ece = expected_calibration_error(pooled_trace, bins=10)
    cv_report = cross_fitted_threshold_validation(
        tracerecover_data, tasks_by_id, num_folds=5, seed=seed
    )
    telemetry_check = _check_telemetry_parity(
        telemetry_csv, baseline_data, tracerecover_data
    )

    report = {
        "metadata": {
            "results_dir": str(results_dir),
            "telemetry_csv": str(telemetry_csv),
            "dataset_version": tracerecover_data["experiment"]["dataset_version"],
            "dataset_sha256": tracerecover_data["experiment"]["dataset_sha256"],
            "seed": seed,
            "task_count_per_rate": len(tasks_by_id),
            "failure_rates_percent": rates,
        },
        "telemetry_csv_parity": telemetry_check,
        "conditions": per_condition,
        "pooled_across_all_rates": {
            "total_paired_runs": len(pooled_trace),
            "baseline_task_success_rate": (
                sum(bool(r["success"]) for r in pooled_base) / len(pooled_base)
            ),
            "tracerecover_task_success_rate": (
                sum(bool(r["success"]) for r in pooled_trace) / len(pooled_trace)
            ),
            "paired_mcnemar_test": pooled_mcnemar,
            "tracerecover_brier_score": pooled_trace_brier,
            "tracerecover_ece": {
                "ece": pooled_trace_ece["ece"],
                "sample_count": pooled_trace_ece["sample_count"],
            },
        },
        "five_fold_cross_fitted_thresholds": cv_report,
    }

    output_path = results_dir / "statistical_validation_report.json"
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return report


def format_plain_text_summary(report: Mapping[str, Any]) -> str:
    """Render a clean plain-text defense table from the validation report."""
    lines: list[str] = []
    meta = report["metadata"]
    parity = report["telemetry_csv_parity"]
    lines.append("TRACERECOVER STATISTICAL VALIDATION REPORT")
    lines.append(f"Results Dir:   {meta['results_dir']}")
    lines.append(f"Telemetry CSV: {meta['telemetry_csv']} (parity_ok={parity['parity_ok']}, rows={parity.get('total_csv_rows', 0)})")
    lines.append(f"Dataset:       v{meta['dataset_version']} | {meta['task_count_per_rate']} tasks/rate | seed={meta['seed']}")
    lines.append("")

    # 1. Decision distribution
    lines.append("1. EXACT DECISION DISTRIBUTION (ANSWER / WARN / ABSTAIN / NOT_APPLICABLE)")
    lines.append("System        | Rate | ANSWER        | WARN         | ABSTAIN       | NOT_APPLICABLE")
    lines.append("--------------+------+---------------+--------------+---------------+---------------")
    for cond in report["conditions"]:
        rate = cond["failure_rate_percent"]
        for sys_key, label in (("baseline", "Baseline"), ("tracerecover", "TraceRecover")):
            dec = cond[sys_key]["decisions"]
            c, r = dec["counts"], dec["rates"]
            lines.append(
                f"{label:<13} | {rate:3.0f}% | "
                f"{c['ANSWER']:3d} ({r['ANSWER']*100:5.1f}%) | "
                f"{c['WARN']:3d} ({r['WARN']*100:4.1f}%) | "
                f"{c['ABSTAIN']:3d} ({r['ABSTAIN']*100:5.1f}%) | "
                f"{c['NOT_APPLICABLE']:3d} ({r['NOT_APPLICABLE']*100:5.1f}%)"
            )
    lines.append("")

    # 2. Gold Accuracy, Selective Accuracy, Hallucination & /99 denominator explanation
    lines.append("2. INDEPENDENT GOLD ACCURACY, SELECTIVE ACCURACY & NLI-GROUNDED HALLUCINATION")
    lines.append("System        | Rate | Gold(All) | Gold(Ans70) | SelAcc(Strict) | ANSWER-Acc | WARN-Acc  | GoldHalluc    | NLI-Halluc")
    lines.append("--------------+------+-----------+-------------+----------------+------------+-----------+---------------+---------------")
    zero_claim_notes: list[str] = []
    for cond in report["conditions"]:
        rate = cond["failure_rate_percent"]
        for sys_key, label in (("baseline", "Baseline"), ("tracerecover", "TraceRecover")):
            ah = cond[sys_key]["accuracy_and_hallucination"]
            ga = ah["independent_gold_accuracy_all"]
            gn = ah["independent_gold_accuracy_answerable"]
            ss = ah["selective_accuracy_strict"]
            da = ah["selective_accuracy_by_decision"]["ANSWER"]
            dw = ah["selective_accuracy_by_decision"]["WARN"]
            gh = ah["gold_fact_hallucination_rate"]
            nh = ah["nli_grounded_hallucination_rate"]
            lines.append(
                f"{label:<13} | {rate:3.0f}% | "
                f"{ga['correct']:3d}/{ga['denominator']:<3d}   | "
                f"{gn['correct']:2d}/{gn['denominator']:<2d} ({gn['rate']:.3f}) | "
                f"{ss['correct']:2d}/{ss['denominator']:<3d} ({ss['rate']:.3f}) | "
                f"{_fmt_ratio(da)} | "
                f"{_fmt_ratio(dw)} | "
                f"{gh['hallucinating_answers']:2d}/{gh['denominator']:<2d} ({gh['rate']:.3f})  | "
                f"{nh['hallucinating_answers']:2d}/{nh['denominator']:<2d} ({nh['answer_level_rate']:.3f})"
            )
            for z in ah["zero_claim_non_abstained_tasks"]:
                zero_claim_notes.append(
                    f"  - {label} @ {rate:g}%: {z['task_id']} ({z['category']}) emitted raw answer {z['raw_answer']!r} -> 0 alphanumeric claims -> excluded from claim-bearing hallucination denominator (making it /99 instead of /100), while still counted as task_success={z['task_success']}."
                )
    if zero_claim_notes:
        lines.append("")
        lines.append("Explanation of /99 Denominator Tasks:")
        lines.extend(zero_claim_notes)
    lines.append("")

    # 3. Wilson 95% CI & McNemar Paired Test
    lines.append("3. WILSON 95% CONFIDENCE INTERVALS & PAIRED MCNEMAR TEST (TRACERECOVER VS BASELINE)")
    lines.append("Rate | Baseline (95% CI)          | TraceRecover (95% CI)      | Delta   | n11/n10/n01/n00 | McNemar Exact p | Chi2_cc (p)")
    lines.append("-----+----------------------------+----------------------------+---------+-----------------+-----------------+-----------------")
    for cond in report["conditions"]:
        rate = cond["failure_rate_percent"]
        b = cond["baseline"]
        t = cond["tracerecover"]
        mc = cond["paired_mcnemar_test"]
        ct = mc["contingency_table"]
        delta = t["task_success_rate"] - b["task_success_rate"]
        lines.append(
            f"{rate:3.0f}% | "
            f"{b['task_success_rate']:.3f} [{b['wilson_95_ci']['lower']:.3f}, {b['wilson_95_ci']['upper']:.3f}] | "
            f"{t['task_success_rate']:.3f} [{t['wilson_95_ci']['lower']:.3f}, {t['wilson_95_ci']['upper']:.3f}] | "
            f"{delta:+.3f}  | "
            f"{ct['both_correct_n11']:2d}/{ct['baseline_only_n10']:2d}/{ct['tracerecover_only_n01']:2d}/{ct['both_wrong_n00']:2d}     | "
            f"{mc['exact_two_sided_p_value']:.6g}        | "
            f"{mc['chi2_continuity_corrected']:.3f} ({mc['chi2_p_value']:.4g})"
        )
    pm = report["pooled_across_all_rates"]["paired_mcnemar_test"]
    pct = pm["contingency_table"]
    lines.append(
        f"ALL  | {report['pooled_across_all_rates']['baseline_task_success_rate']:.3f} (n=300 pooled)         | "
        f"{report['pooled_across_all_rates']['tracerecover_task_success_rate']:.3f} (n=300 pooled)         | "
        f"{report['pooled_across_all_rates']['tracerecover_task_success_rate'] - report['pooled_across_all_rates']['baseline_task_success_rate']:+.3f}  | "
        f"{pct['both_correct_n11']:3d}/{pct['baseline_only_n10']:2d}/{pct['tracerecover_only_n01']:3d}/{pct['both_wrong_n00']:2d}   | "
        f"{pm['exact_two_sided_p_value']:.6g}        | "
        f"{pm['chi2_continuity_corrected']:.3f} ({pm['chi2_p_value']:.4g})"
    )
    lines.append("")

    # 4. Brier Score, ECE & 5-Fold Cross-Fitted Threshold Check
    lines.append("4. CALIBRATION METRICS (BRIER SCORE & ECE) AND 5-FOLD CROSS-FITTED THRESHOLD VALIDATION")
    lines.append("Rate | TraceRecover Brier | TraceRecover ECE | Baseline Brier / ECE")
    lines.append("-----+--------------------+------------------+---------------------")
    for cond in report["conditions"]:
        rate = cond["failure_rate_percent"]
        tb = cond["tracerecover"]["brier_score"]["brier_score"]
        te = cond["tracerecover"]["ece"]["ece"]
        lines.append(f"{rate:3.0f}% | {tb:.4f} (n=100)     | {te:.4f} (n=100)   | N/A (no confidence signal)")
    lines.append(
        f"ALL  | {report['pooled_across_all_rates']['tracerecover_brier_score']['brier_score']:.4f} (n=300)     | "
        f"{report['pooled_across_all_rates']['tracerecover_ece']['ece']:.4f} (n=300)   | N/A (no confidence signal)"
    )
    lines.append("")

    cv = report["five_fold_cross_fitted_thresholds"]
    ts = cv["fitted_threshold_summary"]
    oof = cv["out_of_fold_aggregate"]
    dfm = cv["default_threshold_baseline_0_15_0_55"]
    lines.append("5-Fold Stratified Group-by-Task_ID Cross-Fitting (80% Train -> 20% Held-Out; Zero Leakage):")
    lines.append(
        f"  Fitted Thresholds Across 5 Folds: answer_threshold = {ts['answer_threshold_mean']:.4f} +/- {ts['answer_threshold_stdev']:.4f} | "
        f"abstain_threshold = {ts['abstain_threshold_mean']:.4f} +/- {ts['abstain_threshold_stdev']:.4f}"
    )
    lines.append("  Fold | Train/Test | Fit (Ans / Abs)  | HeldOut Succ | HeldOut SelAcc | Abst Prec | Abst Rec | Ans/Warn/Abst")
    lines.append("  -----+------------+------------------+--------------+----------------+-----------+----------+--------------")
    for f in cv["folds"]:
        m = f["held_out_metrics"]
        lines.append(
            f"   {f['fold']}   | {f['train_run_count']:3d} / {f['test_run_count']:2d}   | "
            f"{f['fitted_answer_threshold']:.4f} / {f['fitted_abstain_threshold']:.4f}  | "
            f"{m['task_success_rate']:.3f}        | "
            f"{m['selective_accuracy_strict']:.3f}          | "
            f"{m['abstain_precision']:.3f}     | "
            f"{m['abstain_recall']:.3f}    | "
            f"{m['answer_count']:2d} / {m['warn_count']:2d} / {m['abstain_count']:2d}"
        )
    lines.append(
        f"  OOF Aggregate (n=300): TaskSucc={oof['task_success_rate']:.3f} | "
        f"SelAcc(Strict)={oof['selective_accuracy_strict']:.3f} | "
        f"AbstPrec={oof['abstain_precision']:.3f} | AbstRec={oof['abstain_recall']:.3f} | "
        f"AbstF1={oof['abstain_f1']:.3f} | Ans/Warn/Abst={oof['answer_count']}/{oof['warn_count']}/{oof['abstain_count']}"
    )
    lines.append(
        f"  Default (0.15/0.55):   TaskSucc={dfm['task_success_rate']:.3f} | "
        f"SelAcc(Strict)={dfm['selective_accuracy_strict']:.3f} | "
        f"AbstPrec={dfm['abstain_precision']:.3f} | AbstRec={dfm['abstain_recall']:.3f} | "
        f"AbstF1={dfm['abstain_f1']:.3f} | Ans/Warn/Abst={dfm['answer_count']}/{dfm['warn_count']}/{dfm['abstain_count']}"
    )
    return "\n".join(lines)


def _fmt_ratio(entry: Mapping[str, Any]) -> str:
    denom = entry["denominator"]
    if denom == 0 or entry["rate"] is None:
        return "N/A (0/0) "
    return f"{entry['correct']:2d}/{denom:<2d} ({entry['rate']:.2f})"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--telemetry-csv", type=Path, default=DEFAULT_TELEMETRY_CSV)
    parser.add_argument("--tasks-path", type=Path, default=DEFAULT_TASKS_PATH)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    report = run_statistical_validation(
        results_dir=args.results_dir,
        telemetry_csv=args.telemetry_csv,
        tasks_path=args.tasks_path,
        seed=args.seed,
    )
    print(format_plain_text_summary(report))
    print(f"\nSaved JSON report to: {args.results_dir / 'statistical_validation_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

