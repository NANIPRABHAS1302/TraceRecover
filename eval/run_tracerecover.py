"""Run full TraceRecover, matched baseline, and three controlled ablations."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import run_baseline
from .runner import load_tasks, run_evaluation, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FAILURE_RATES = run_baseline.DEFAULT_FAILURE_RATES
FULL_FEATURES = {
    "enable_failure_detection": True,
    "enable_localization": True,
    "enable_recovery": True,
    "enable_grounding": True,
    "enable_uncertainty": True,
}
ABLATIONS = {
    "without_grounding": {
        "enable_failure_detection": True,
        "enable_localization": True,
        "enable_recovery": True,
        "enable_grounding": False,
        "enable_uncertainty": True,
    },
    "without_uncertainty": {
        "enable_failure_detection": True,
        "enable_localization": True,
        "enable_recovery": True,
        "enable_grounding": True,
        "enable_uncertainty": False,
    },
    "without_localization": {
        "enable_failure_detection": True,
        "enable_localization": False,
        "enable_recovery": True,
        "enable_grounding": True,
        "enable_uncertainty": True,
    },
}


def execute_evaluation(
    *,
    dataset_path: Path | str = PROJECT_ROOT / "eval" / "tasks.json",
    output_dir: Path | str = PROJECT_ROOT / "results",
    log_root: Path | str = PROJECT_ROOT / "logs" / "evaluation",
    failure_rates: Sequence[float] = DEFAULT_FAILURE_RATES,
    seed: int = 42,
    num_tasks: int | None = None,
    answer_threshold: float = 0.15,
    abstain_threshold: float = 0.55,
) -> dict[str, Any]:
    """Run all matched configurations and persist their measured results."""
    dataset_version, all_tasks, digest = load_tasks(dataset_path)
    if num_tasks is not None:
        if num_tasks < 1:
            raise ValueError("num_tasks must be at least one.")
        all_tasks = all_tasks[:num_tasks]
    output = Path(output_dir)
    logs = Path(log_root)

    baseline = run_baseline.execute_evaluation(
        dataset_path=dataset_path,
        output_dir=output,
        log_root=logs,
        failure_rates=failure_rates,
        seed=seed,
        num_tasks=len(all_tasks),
        answer_threshold=answer_threshold,
        abstain_threshold=abstain_threshold,
    )
    full = run_evaluation(
        tasks=all_tasks,
        dataset_version=dataset_version,
        dataset_sha256=digest,
        system_name="tracerecover-full",
        failure_rates=failure_rates,
        seed=seed,
        log_root=logs,
        feature_flags=FULL_FEATURES,
        answer_threshold=answer_threshold,
        abstain_threshold=abstain_threshold,
    )
    ablations = {
        name: run_evaluation(
            tasks=all_tasks,
            dataset_version=dataset_version,
            dataset_sha256=digest,
            system_name=f"tracerecover-{name}",
            failure_rates=failure_rates,
            seed=seed,
            log_root=logs,
            feature_flags=flags,
            answer_threshold=answer_threshold,
            abstain_threshold=abstain_threshold,
        )
        for name, flags in ABLATIONS.items()
    }

    write_json(output / "tracerecover_results.json", full)
    write_json(
        output / "ablation_results.json",
        {
            "experiment": {
                "dataset_version": dataset_version,
                "dataset_sha256": digest,
                "random_seed": seed,
                "failure_rates_percent": list(failure_rates),
                "selected_task_count": len(all_tasks),
                "timestamp": full["experiment"]["timestamp"],
                "ablation_descriptions": {
                    "without_grounding": (
                        "Disables evidence verification and citations while "
                        "retaining other phases."
                    ),
                    "without_uncertainty": (
                        "Disables uncertainty estimation and abstention while "
                        "retaining evidence grounding."
                    ),
                    "without_localization": (
                        "Detects failures but does not localize them for recovery."
                    ),
                },
            },
            "ablations": ablations,
        },
    )
    summary = build_comparison_summary(baseline, full, ablations)
    write_json(output / "comparison_summary.json", summary)
    write_metrics_csv(
        output / "metrics_summary.csv",
        {
            "baseline": baseline,
            "tracerecover": full,
            **ablations,
        },
    )
    return {
        "baseline": baseline,
        "tracerecover": full,
        "ablations": ablations,
        "comparison_summary": summary,
    }


def build_comparison_summary(
    baseline: Mapping[str, Any],
    full: Mapping[str, Any],
    ablations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Create per-condition comparisons without inferring significance."""
    comparisons: list[dict[str, Any]] = []
    baseline_conditions = _conditions_by_rate(baseline)
    full_conditions = _conditions_by_rate(full)
    for rate, base_condition in baseline_conditions.items():
        full_condition = full_conditions.get(rate)
        if full_condition is None:
            continue
        base_metrics = base_condition["metrics"]
        full_metrics = full_condition["metrics"]
        comparisons.append(
            {
                "failure_rate_percent": rate,
                "baseline": base_metrics,
                "tracerecover": full_metrics,
                "tracerecover_minus_baseline": {
                    "task_success_rate": _difference(
                        full_metrics["task_success"]["rate"],
                        base_metrics["task_success"]["rate"],
                    ),
                    "hallucination_rate": _difference(
                        full_metrics["hallucination"]["rate"],
                        base_metrics["hallucination"]["rate"],
                    ),
                    "unsupported_claim_rate": _difference(
                        full_metrics.get("unsupported_claim_rate", {}).get("rate"),
                        base_metrics.get("unsupported_claim_rate", {}).get("rate"),
                    ),
                    "evidence_coverage": _difference(
                        full_metrics.get("evidence_coverage", {}).get("rate"),
                        base_metrics.get("evidence_coverage", {}).get("rate"),
                    ),
                    "average_latency_seconds": _difference(
                        full_metrics["latency"]["average_seconds"],
                        base_metrics["latency"]["average_seconds"],
                    ),
                    "ece": _difference(
                        full_metrics["ece"]["ece"], base_metrics["ece"]["ece"]
                    ),
                    "average_cost_usd": _difference(
                        full_metrics.get("cost", {}).get("average_cost_usd"),
                        base_metrics.get("cost", {}).get("average_cost_usd"),
                    ),
                },
            }
        )
    ablation_metrics = {
        name: [
            {
                "failure_rate_percent": condition["failure_rate_percent"],
                "metrics": condition["metrics"],
            }
            for condition in result["conditions"]
        ]
        for name, result in ablations.items()
    }
    return {
        "dataset_version": full["experiment"]["dataset_version"],
        "dataset_sha256": full["experiment"]["dataset_sha256"],
        "random_seed": full["experiment"]["random_seed"],
        "task_count": full["experiment"]["task_count"],
        "failure_rates_percent": full["experiment"]["failure_rates_percent"],
        "confidence_interval_method": (
            "Wilson score interval, 95%, reported for task success only; "
            "no significance tests performed."
        ),
        "comparisons": comparisons,
        "ablation_metrics": ablation_metrics,
        "limitations": [
            "Small deterministic dataset limits external validity.",
            "The dataset-authored action plan controls tool selection and does not "
            "evaluate free-form planner quality.",
            "Local heuristic confidence is not a calibrated probability; ECE is "
            "reported only as an empirical diagnostic.",
        ],
    }


def write_metrics_csv(
    path: Path | str, systems: Mapping[str, Mapping[str, Any]]
) -> Path:
    """Write a flat, spreadsheet-friendly metrics table for all runs."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "system",
        "failure_rate_percent",
        "task_count",
        "task_successes",
        "task_success_denominator",
        "task_success_rate",
        "task_success_ci95_lower",
        "task_success_ci95_upper",
        "detected_failures",
        "detected_injected_failures",
        "detected_natural_failures",
        "injected_tool_failures",
        "injected_recovery_failures",
        "successful_recoveries",
        "recovery_rate",
        "answers_with_factual_claims",
        "hallucinating_answers",
        "hallucination_rate",
        "unsupported_claims",
        "contradicted_claims",
        "ece",
        "ece_sample_count",
        "correct_abstentions",
        "incorrect_abstentions",
        "missed_abstentions",
        "abstention_precision",
        "abstention_recall",
        "average_latency_seconds",
        "median_latency_seconds",
        "unsupported_claim_rate",
        "evidence_coverage",
        "total_tokens",
        "average_tokens",
        "total_cost_usd",
        "average_cost_usd",
    ]
    with destination.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns)
        writer.writeheader()
        for system_name, result in systems.items():
            for condition in result["conditions"]:
                metrics = condition["metrics"]
                success = metrics["task_success"]
                interval = success["confidence_interval_95"] or {}
                recovery = metrics["recovery"]
                hallucination = metrics["hallucination"]
                unsupported_claim = metrics.get("unsupported_claim_rate", {})
                coverage = metrics.get("evidence_coverage", {})
                ece = metrics["ece"]
                abstention = metrics["abstention"]
                latency = metrics["latency"]
                cost = metrics.get("cost", {})
                writer.writerow(
                    {
                        "system": system_name,
                        "failure_rate_percent": condition["failure_rate_percent"],
                        "task_count": metrics["task_count"],
                        "task_successes": success["successes"],
                        "task_success_denominator": success["denominator"],
                        "task_success_rate": _csv_value(success["rate"]),
                        "task_success_ci95_lower": _csv_value(interval.get("lower")),
                        "task_success_ci95_upper": _csv_value(interval.get("upper")),
                        "detected_failures": recovery["detected_failures"],
                        "detected_injected_failures": (
                            recovery["detected_injected_failures"]
                        ),
                        "detected_natural_failures": (
                            recovery["detected_natural_failures"]
                        ),
                        "injected_tool_failures": recovery["injected_tool_failures"],
                        "injected_recovery_failures": (
                            recovery["injected_recovery_failures"]
                        ),
                        "successful_recoveries": recovery["successful_recoveries"],
                        "recovery_rate": _csv_value(recovery["rate"]),
                        "answers_with_factual_claims": (
                            hallucination["answers_with_factual_claims"]
                        ),
                        "hallucinating_answers": hallucination["hallucinating_answers"],
                        "hallucination_rate": _csv_value(hallucination["rate"]),
                        "unsupported_claims": hallucination["unsupported_claims"],
                        "contradicted_claims": hallucination["contradicted_claims"],
                        "ece": _csv_value(ece["ece"]),
                        "ece_sample_count": ece["sample_count"],
                        "correct_abstentions": abstention["correct_abstentions"],
                        "incorrect_abstentions": abstention["incorrect_abstentions"],
                        "missed_abstentions": abstention["missed_abstentions"],
                        "abstention_precision": _csv_value(
                            abstention["abstention_precision"]
                        ),
                        "abstention_recall": _csv_value(
                            abstention["abstention_recall"]
                        ),
                        "average_latency_seconds": _csv_value(
                            latency["average_seconds"]
                        ),
                        "median_latency_seconds": _csv_value(
                            latency["median_seconds"]
                        ),
                        "unsupported_claim_rate": _csv_value(
                            unsupported_claim.get("rate")
                        ),
                        "evidence_coverage": _csv_value(coverage.get("rate")),
                        "total_tokens": _csv_value(cost.get("total_tokens")),
                        "average_tokens": _csv_value(cost.get("average_tokens")),
                        "total_cost_usd": _csv_value(cost.get("total_cost_usd")),
                        "average_cost_usd": _csv_value(cost.get("average_cost_usd")),
                    }
                )
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-path", type=Path, default=PROJECT_ROOT / "eval" / "tasks.json"
    )
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results")
    parser.add_argument(
        "--log-root", type=Path, default=PROJECT_ROOT / "logs" / "evaluation"
    )
    parser.add_argument(
        "--failure-rates",
        type=float,
        nargs="+",
        default=list(DEFAULT_FAILURE_RATES),
        help="Tool failure percentages (default: 0 20 40).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-tasks", type=int)
    parser.add_argument("--answer-threshold", type=float, default=0.15)
    parser.add_argument("--abstain-threshold", type=float, default=0.55)
    args = parser.parse_args(argv)
    result = execute_evaluation(
        dataset_path=args.dataset_path,
        output_dir=args.output_dir,
        log_root=args.log_root,
        failure_rates=args.failure_rates,
        seed=args.seed,
        num_tasks=args.num_tasks,
        answer_threshold=args.answer_threshold,
        abstain_threshold=args.abstain_threshold,
    )
    _print_summary(result["comparison_summary"])
    print(f"Saved Phase 7 results to {args.output_dir}")
    return 0


def _conditions_by_rate(result: Mapping[str, Any]) -> dict[float, Mapping[str, Any]]:
    return {
        float(condition["failure_rate_percent"]): condition
        for condition in result["conditions"]
    }


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _csv_value(value: Any) -> Any:
    return "" if value is None else value


def _print_summary(summary: Mapping[str, Any]) -> None:
    print("System comparison (rates are measured from this run):")
    for comparison in summary["comparisons"]:
        baseline = comparison["baseline"]
        trace = comparison["tracerecover"]
        print(
            f"failure={comparison['failure_rate_percent']:g}% "
            f"success baseline={baseline['task_success']['rate']} "
            f"TraceRecover={trace['task_success']['rate']} "
            f"recovery={trace['recovery']['successful_recoveries']}/"
            f"{trace['recovery']['detected_failures']} "
            f"hallucination baseline={baseline['hallucination']['rate']} "
            f"TraceRecover={trace['hallucination']['rate']} "
            f"ECE TraceRecover={trace['ece']['ece']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())