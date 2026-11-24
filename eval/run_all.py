"""Run baseline, TraceRecover, ablations, and plots without clobbering results."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import plot_results, run_tracerecover


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_FILES = (
    "baseline_results.json",
    "tracerecover_results.json",
    "ablation_results.json",
    "comparison_summary.json",
    "metrics_summary.csv",
)


def new_run_id(now: datetime | None = None) -> str:
    """Return a timestamp suitable for unique result and log directory names."""
    timestamp = now or datetime.now(timezone.utc)
    return timestamp.strftime("%Y%m%dT%H%M%S%fZ")


def default_output_dir(run_id: str | None = None) -> Path:
    """Choose a fresh timestamped subdirectory under the existing results folder."""
    return PROJECT_ROOT / "results" / f"phase8-run-{run_id or new_run_id()}"


def check_output_directory(output_dir: Path | str, overwrite: bool = False) -> Path:
    """Reject result collisions unless the user explicitly allows replacement."""
    destination = Path(output_dir).resolve()
    existing = [
        destination / filename
        for filename in RESULT_FILES
        if (destination / filename).exists()
    ]
    plot_dir = destination / "plots"
    if plot_dir.exists():
        existing.extend(
            plot_dir / filename
            for filename in plot_results.PLOT_FILENAMES
            if (plot_dir / filename).exists()
        )
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(
            "Refusing to overwrite existing experiment outputs: "
            f"{names}. Choose a new --output-dir or pass --overwrite."
        )
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {destination}")
    return destination


def execute_workflow(
    *,
    dataset_path: Path | str = PROJECT_ROOT / "eval" / "tasks.json",
    output_dir: Path | str | None = None,
    log_root: Path | str | None = None,
    failure_rates: Sequence[float] = (0.0, 20.0, 40.0),
    seed: int = 42,
    num_tasks: int | None = None,
    answer_threshold: float = 0.15,
    abstain_threshold: float = 0.55,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run all matched evaluation systems and generate their saved-data plots."""
    chosen_dir = check_output_directory(
        output_dir or default_output_dir(), overwrite=overwrite
    )
    chosen_log_root = (
        Path(log_root)
        if log_root is not None
        else PROJECT_ROOT / "logs" / "evaluation" / chosen_dir.name
    )
    chosen_dir.mkdir(parents=True, exist_ok=True)

    evaluation = run_tracerecover.execute_evaluation(
        dataset_path=dataset_path,
        output_dir=chosen_dir,
        log_root=chosen_log_root,
        failure_rates=failure_rates,
        seed=seed,
        num_tasks=num_tasks,
        answer_threshold=answer_threshold,
        abstain_threshold=abstain_threshold,
    )
    plots = plot_results.generate_plots(
        results_dir=chosen_dir,
        output_dir=chosen_dir / "plots",
        overwrite=overwrite,
    )
    return {
        **evaluation,
        "output_dir": str(chosen_dir),
        "log_root": str(chosen_log_root),
        "plots": [str(path) for path in plots],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-path", type=Path, default=PROJECT_ROOT / "eval" / "tasks.json"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory; defaults to a new timestamped results subdirectory.",
    )
    parser.add_argument(
        "--log-root",
        type=Path,
        help="Trace root; defaults to logs/evaluation/<run-directory-name>.",
    )
    parser.add_argument(
        "--failure-rates",
        type=float,
        nargs="+",
        default=[0.0, 20.0, 40.0],
        help="Per-tool-call failure percentages (default: 0 20 40).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-tasks", type=int)
    parser.add_argument("--answer-threshold", type=float, default=0.15)
    parser.add_argument("--abstain-threshold", type=float, default=0.55)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace matching result and plot files in --output-dir.",
    )
    args = parser.parse_args(argv)
    try:
        result = execute_workflow(
            dataset_path=args.dataset_path,
            output_dir=args.output_dir,
            log_root=args.log_root,
            failure_rates=args.failure_rates,
            seed=args.seed,
            num_tasks=args.num_tasks,
            answer_threshold=args.answer_threshold,
            abstain_threshold=args.abstain_threshold,
            overwrite=args.overwrite,
        )
    except (FileExistsError, NotADirectoryError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")

    print(f"Experiment results: {result['output_dir']}")
    print(f"Execution traces: {result['log_root']}")
    print("Generated plots:")
    for path in result["plots"]:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
