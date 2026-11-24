"""Run the no-recovery, no-grounding baseline evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .runner import load_tasks, run_evaluation, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FAILURE_RATES = (0.0, 20.0, 40.0)
BASELINE_FEATURES = {
    "enable_failure_detection": False,
    "enable_localization": False,
    "enable_recovery": False,
    "enable_grounding": False,
    "enable_uncertainty": False,
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
) -> dict:
    """Run and persist baseline results using the shared deterministic setup."""
    dataset_version, all_tasks, digest = load_tasks(dataset_path)
    if num_tasks is not None:
        if num_tasks < 1:
            raise ValueError("num_tasks must be at least one.")
        all_tasks = all_tasks[:num_tasks]
    result = run_evaluation(
        tasks=all_tasks,
        dataset_version=dataset_version,
        dataset_sha256=digest,
        system_name="baseline",
        failure_rates=failure_rates,
        seed=seed,
        log_root=log_root,
        feature_flags=BASELINE_FEATURES,
        answer_threshold=answer_threshold,
        abstain_threshold=abstain_threshold,
    )
    result["experiment"]["selected_task_count"] = len(all_tasks)
    write_json(Path(output_dir) / "baseline_results.json", result)
    return result


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
    _print_summary(result)
    print(
        f"Saved baseline results to "
        f"{Path(args.output_dir) / 'baseline_results.json'}"
    )
    return 0


def _print_summary(result: dict) -> None:
    for condition in result["conditions"]:
        metrics = condition["metrics"]
        success = metrics["task_success"]
        print(
            f"failure={condition['failure_rate_percent']:g}% "
            f"success={_format_rate(success['rate'], success['successes'], success['denominator'])} "
            f"hallucination={_format_metric(metrics['hallucination']['rate'])} "
            f"latency_mean={_format_metric(metrics['latency']['average_seconds'])}s "
            "ECE=N/A (baseline has no confidence signal)"
        )


def _format_rate(value: float | None, numerator: int, denominator: int) -> str:
    if value is None:
        return "N/A"
    return f"{value:.3f} ({numerator}/{denominator})"


def _format_metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())