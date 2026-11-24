"""Tests for Phase 8 result orchestration and plot generation."""

import csv
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from eval import plot_results, run_all


_METRIC_FIELDS = [
    "system",
    "failure_rate_percent",
    "task_count",
    "task_successes",
    "task_success_denominator",
    "task_success_rate",
    "detected_failures",
    "successful_recoveries",
    "recovery_rate",
    "answers_with_factual_claims",
    "hallucinating_answers",
    "hallucination_rate",
    "average_latency_seconds",
]


def _write_plot_fixture(directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in (
        "baseline_results.json",
        "ablation_results.json",
        "comparison_summary.json",
    ):
        (directory / name).write_text("{}", encoding="utf-8")
    trace_result = {
        "conditions": [
            {
                "failure_rate_percent": 0,
                "results": [
                    {"confidence": 0.9, "success": True},
                    {"confidence": 0.7, "success": False},
                ],
            },
            {
                "failure_rate_percent": 20,
                "results": [
                    {"confidence": 0.6, "success": True},
                    {"confidence": 0.4, "success": False},
                ],
            },
        ]
    }
    (directory / "tracerecover_results.json").write_text(
        json.dumps(trace_result), encoding="utf-8"
    )
    systems = (
        "baseline",
        "tracerecover",
        "without_grounding",
        "without_uncertainty",
        "without_localization",
    )
    with (directory / "metrics_summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=_METRIC_FIELDS)
        writer.writeheader()
        for system in systems:
            for rate in (0, 20):
                denominator = 2
                detected = 0 if rate == 0 else 2
                success_count = 2 if system != "without_localization" else 1
                writer.writerow(
                    {
                        "system": system,
                        "failure_rate_percent": rate,
                        "task_count": denominator,
                        "task_successes": success_count,
                        "task_success_denominator": denominator,
                        "task_success_rate": success_count / denominator,
                        "detected_failures": detected,
                        "successful_recoveries": 1 if detected else 0,
                        "recovery_rate": 0.5 if detected else "",
                        "answers_with_factual_claims": denominator,
                        "hallucinating_answers": 0,
                        "hallucination_rate": 0,
                        "average_latency_seconds": 0.002,
                    }
                )


def test_new_run_directory_uses_supplied_timestamp() -> None:
    timestamp = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)

    assert run_all.new_run_id(timestamp) == "20260102T030405000006Z"
    assert run_all.default_output_dir("fixed").name == "phase8-run-fixed"


def test_run_all_refuses_existing_result_files_unless_overwrite_is_explicit(
    tmp_path,
) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    existing = output_dir / "baseline_results.json"
    existing.write_text("historical", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        run_all.check_output_directory(output_dir)

    assert run_all.check_output_directory(output_dir, overwrite=True) == output_dir
    assert existing.read_text(encoding="utf-8") == "historical"


def test_plot_generation_writes_valid_svg_and_preserves_undefined_metrics(
    tmp_path,
) -> None:
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "charts"
    _write_plot_fixture(results_dir)

    paths = plot_results.generate_plots(results_dir, output_dir)

    assert {path.name for path in paths} == set(plot_results.PLOT_FILENAMES)
    assert len(paths) == 6
    for path in paths:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        assert root.tag.endswith("svg")
    recovery_svg = (output_dir / "recovery_rate.svg").read_text(encoding="utf-8")
    assert "N/A" in recovery_svg
    calibration_svg = (output_dir / "calibration.svg").read_text(encoding="utf-8")
    assert "Samples: 4" in calibration_svg
    assert "not a calibrated probability" in calibration_svg
    ablation_svg = (output_dir / "ablation_success.svg").read_text(encoding="utf-8")
    assert "<rect " in ablation_svg


def test_plot_generation_requires_explicit_overwrite(tmp_path) -> None:
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "charts"
    _write_plot_fixture(results_dir)
    output_dir.mkdir()
    target = output_dir / "task_success.svg"
    target.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        plot_results.generate_plots(results_dir, output_dir)
    assert target.read_text(encoding="utf-8") == "keep"
