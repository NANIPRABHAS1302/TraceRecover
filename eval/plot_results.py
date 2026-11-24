"""Generate accessible, dependency-free SVG plots from saved evaluation data."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLOT_FILENAMES = (
    "task_success.svg",
    "hallucination_rate.svg",
    "recovery_rate.svg",
    "ablation_success.svg",
    "average_latency.svg",
    "calibration.svg",
)
_COLORS = {
    "baseline": "#4c78a8",
    "tracerecover": "#f58518",
    "without_grounding": "#54a24b",
    "without_uncertainty": "#e45756",
    "without_localization": "#b279a2",
}


@dataclass(frozen=True)
class PlotSeries:
    """One named result series displayed in an SVG line chart."""

    key: str
    label: str


def generate_plots(
    results_dir: Path | str = PROJECT_ROOT / "results",
    output_dir: Path | str | None = None,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Read saved JSON/CSV results and write five metric visualizations."""
    source_dir = Path(results_dir)
    destination = Path(output_dir) if output_dir is not None else source_dir / "plots"
    _validate_saved_results(source_dir)
    rows_by_system = _read_metrics(source_dir / "metrics_summary.csv")
    missing_series = {
        key
        for key in ("baseline", "tracerecover", *_ABLATION_KEYS)
        if key not in rows_by_system
    }
    if missing_series:
        raise ValueError(
            "metrics_summary.csv is missing system rows: "
            + ", ".join(sorted(missing_series))
        )
    collisions = [
        destination / name
        for name in PLOT_FILENAMES
        if (destination / name).exists()
    ]
    if collisions and not overwrite:
        raise FileExistsError(
            "Refusing to overwrite plot files: "
            + ", ".join(str(path) for path in collisions)
            + ". Choose another --output-dir or pass --overwrite."
        )
    destination.mkdir(parents=True, exist_ok=True)

    common = _common_failure_rates(rows_by_system)
    chart_definitions = (
        (
            "task_success.svg",
            "Task success rate",
            "Fraction of tasks satisfying the task-specific success criterion.",
            "Task success rate",
            (
                PlotSeries("baseline", "Baseline"),
                PlotSeries("tracerecover", "TraceRecover"),
            ),
            "task_success_rate",
            "task_successes",
            "task_success_denominator",
            1.0,
            False,
        ),
        (
            "hallucination_rate.svg",
            "Answer-level hallucination rate",
            "Fraction of answers with unsupported or contradicted claims; "
            "abstained answers are excluded, and each point shows its denominator.",
            "Hallucination rate",
            (
                PlotSeries("baseline", "Baseline"),
                PlotSeries("tracerecover", "TraceRecover"),
            ),
            "hallucination_rate",
            "hallucinating_answers",
            "answers_with_factual_claims",
            1.0,
            False,
        ),
        (
            "recovery_rate.svg",
            "Recovery rate",
            "Successful recovery incidents divided by detected failure incidents. "
            "Undefined values remain N/A rather than being plotted as zero.",
            "Recovery rate",
            (
                PlotSeries("baseline", "Baseline"),
                PlotSeries("tracerecover", "TraceRecover"),
                PlotSeries("without_localization", "Without localization"),
            ),
            "recovery_rate",
            "successful_recoveries",
            "detected_failures",
            1.0,
            False,
        ),
        (
            "ablation_success.svg",
            "bar",
        ),
        (
            "average_latency.svg",
            "Average task latency",
            "Mean end-to-end ReactAgent.run duration; values are converted "
            "from saved seconds to milliseconds.",
            "Average latency (milliseconds)",
            (
                PlotSeries("baseline", "Baseline"),
                PlotSeries("tracerecover", "TraceRecover"),
            ),
            "average_latency_seconds",
            "task_count",
            None,
            None,
            True,
        ),
        (
            "calibration.svg",
            "calibration",
        ),
    )
    written: list[Path] = []
    for chart in chart_definitions:
        if chart[1] == "bar":
            svg = _render_ablation_chart(rows_by_system, common)
            filename = chart[0]
        elif chart[1] == "calibration":
            svg = _render_calibration_chart(source_dir / "tracerecover_results.json")
            filename = chart[0]
        else:
            (
                filename,
                title,
                description,
                y_label,
                series,
                value_field,
                numerator_field,
                denominator_field,
                fixed_y_max,
                milliseconds,
            ) = chart
            svg = _render_chart(
                title=title,
                description=description,
                y_label=y_label,
                rates=common,
                series=series,
                rows_by_system=rows_by_system,
                value_field=value_field,
                numerator_field=numerator_field,
                denominator_field=denominator_field,
                fixed_y_max=fixed_y_max,
                milliseconds=milliseconds,
            )
        path = destination / filename
        path.write_text(svg, encoding="utf-8", newline="\n")
        written.append(path)
    return written


_ABLATION_KEYS = (
    "without_grounding",
    "without_uncertainty",
    "without_localization",
)


def _validate_saved_results(results_dir: Path) -> None:
    required = (
        "baseline_results.json",
        "tracerecover_results.json",
        "ablation_results.json",
        "metrics_summary.csv",
    )
    missing = [name for name in required if not (results_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing saved evaluation files in {results_dir}: "
            + ", ".join(missing)
        )
    for filename in required[:3]:
        try:
            with (results_dir / filename).open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid result file {filename}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Result file {filename} must contain a JSON object.")


def _read_metrics(path: Path) -> dict[str, list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None:
                raise ValueError("Metrics CSV has no header.")
            rows = list(reader)
    except OSError as exc:
        raise ValueError(f"Could not read metrics CSV {path}: {exc}") from exc
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        system = row.get("system")
        rate = _float(row.get("failure_rate_percent"))
        if not system or rate is None:
            continue
        row["_rate"] = str(rate)
        grouped.setdefault(system, []).append(row)
    for system_rows in grouped.values():
        system_rows.sort(key=lambda item: float(item["_rate"]))
    return grouped


def _common_failure_rates(
    rows_by_system: Mapping[str, Sequence[Mapping[str, str]]]
) -> list[float]:
    rate_sets = [
        {float(row["_rate"]) for row in rows_by_system[system]}
        for system in ("baseline", "tracerecover")
    ]
    rates = sorted(rate_sets[0].intersection(rate_sets[1]))
    if not rates:
        raise ValueError("Baseline and TraceRecover have no common failure-rate rows.")
    return rates


def _render_chart(
    *,
    title: str,
    description: str,
    y_label: str,
    rates: Sequence[float],
    series: Sequence[PlotSeries],
    rows_by_system: Mapping[str, Sequence[Mapping[str, str]]],
    value_field: str,
    numerator_field: str,
    denominator_field: str | None,
    fixed_y_max: float | None,
    milliseconds: bool,
) -> str:
    width, height = 960, 600
    left, top, plot_width, plot_height = 100.0, 88.0, 680.0, 390.0
    bottom = top + plot_height
    point_data: dict[str, list[tuple[float, float | None, str]]] = {}
    values: list[float] = []
    for item in series:
        rows = {float(row["_rate"]): row for row in rows_by_system.get(item.key, ())}
        data: list[tuple[float, float | None, str]] = []
        for rate in rates:
            row = rows.get(rate)
            value = _float(row.get(value_field)) if row is not None else None
            if value is not None and milliseconds:
                value *= 1000.0
            numerator = _integer(row.get(numerator_field)) if row is not None else None
            if denominator_field is None:
                denominator = _integer(row.get("task_count")) if row else None
            else:
                denominator = (
                    _integer(row.get(denominator_field)) if row is not None else None
                )
            point_label = (
                f"{numerator}/{denominator}"
                if numerator is not None and denominator is not None
                else "N/A"
            )
            data.append((rate, value, point_label))
            if value is not None:
                values.append(value)
        point_data[item.key] = data

    y_max = fixed_y_max
    if y_max is None:
        y_max = max(1.0, math.ceil(max(values, default=0.0) * 1.1))
    y_max = max(float(y_max), 1e-9)
    y_ticks = 5

    def x_position(rate: float) -> float:
        if len(rates) == 1 or rates[-1] == rates[0]:
            return left + plot_width / 2
        return left + (rate - rates[0]) / (rates[-1] - rates[0]) * plot_width

    def y_position(value: float) -> float:
        return bottom - value / y_max * plot_height

    title_text = html.escape(title)
    description_text = html.escape(description)
    output = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        'viewBox="0 0 960 600" role="img" aria-labelledby="chart-title chart-desc">',
        f"<title id=\"chart-title\">{title_text}</title>",
        f"<desc id=\"chart-desc\">{description_text}</desc>",
        "<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#222}"
        ".grid{stroke:#d9d9d9;stroke-width:1}.axis{stroke:#444;stroke-width:1.4}"
        ".line{fill:none;stroke-width:2.5}.point{stroke:#fff;stroke-width:1.5}"
        ".label{font-size:12px}.small{font-size:11px}</style>",
        f'<text x="{width/2:.1f}" y="38" text-anchor="middle" '
        'font-size="22" font-weight="600">'
        f"{title_text}</text>",
        f'<text x="{width/2:.1f}" y="62" text-anchor="middle" '
        'font-size="12" fill="#555">'
        f"{description_text}</text>",
    ]

    for tick in range(y_ticks + 1):
        value = y_max * tick / y_ticks
        y = y_position(value)
        output.append(
            f'<line class="grid" x1="{left:.1f}" y1="{y:.1f}" '
            f'x2="{left + plot_width:.1f}" y2="{y:.1f}"/>'
        )
        tick_label = f"{value:.0f}" if milliseconds else f"{value:.1f}"
        output.append(
            f'<text class="label" x="{left - 12:.1f}" y="{y + 4:.1f}" '
            f'text-anchor="end">{tick_label}</text>'
        )
    output.extend(
        [
            f'<line class="axis" x1="{left:.1f}" y1="{top:.1f}" '
            f'x2="{left:.1f}" y2="{bottom:.1f}"/>',
            f'<line class="axis" x1="{left:.1f}" y1="{bottom:.1f}" '
            f'x2="{left + plot_width:.1f}" y2="{bottom:.1f}"/>',
        ]
    )
    for rate in rates:
        x = x_position(rate)
        output.append(
            f'<line class="grid" x1="{x:.1f}" y1="{bottom:.1f}" '
            f'x2="{x:.1f}" y2="{bottom + 5:.1f}"/>'
        )
        output.append(
            f'<text class="label" x="{x:.1f}" y="{bottom + 24:.1f}" '
            f'text-anchor="middle">{rate:g}%</text>'
        )
    output.extend(
        [
            f'<text class="label" x="{left + plot_width/2:.1f}" '
            f'y="{bottom + 52:.1f}" text-anchor="middle">Injected tool failure rate</text>',
            f'<text class="label" transform="translate(28 {top + plot_height/2:.1f}) '
            f'rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>',
        ]
    )

    for series_index, item in enumerate(series):
        color = _COLORS[item.key]
        data = point_data[item.key]
        segment: list[tuple[float, float]] = []
        for rate, value, label in data:
            if value is None:
                if len(segment) > 1:
                    output.append(_polyline(segment, color))
                segment = []
                x = x_position(rate)
                output.append(
                    f'<text class="small" x="{x:.1f}" y="{bottom - 7:.1f}" '
                    f'text-anchor="middle">N/A</text>'
                )
                continue
            x, y = x_position(rate), y_position(value)
            segment.append((x, y))
            output.append(
                f'<circle class="point" cx="{x:.1f}" cy="{y:.1f}" r="5" '
                f'fill="{color}"><title>{html.escape(item.label)} at {rate:g}%: '
                f'{value:.4g}; {html.escape(label)}</title></circle>'
            )
            label_y = max(top + 14, min(bottom - 8, y - 9 - (series_index % 2) * 13))
            output.append(
                f'<text class="small" x="{x + 7:.1f}" y="{label_y:.1f}">'
                f"{html.escape(label)}</text>"
            )
        if len(segment) > 1:
            output.append(_polyline(segment, color))

    legend_y = top + 20
    for index, item in enumerate(series):
        y = legend_y + index * 26
        color = _COLORS[item.key]
        output.extend(
            [
                f'<line x1="806" y1="{y:.1f}" x2="832" y2="{y:.1f}" '
                f'stroke="{color}" stroke-width="3"/>',
                f'<circle cx="819" cy="{y:.1f}" r="4" fill="{color}"/>',
                f'<text class="label" x="840" y="{y + 4:.1f}">'
                f"{html.escape(item.label)}</text>",
            ]
        )
    output.append("</svg>")
    return "\n".join(output) + "\n"


def _render_ablation_chart(
    rows_by_system: Mapping[str, Sequence[Mapping[str, str]]],
    rates: Sequence[float],
) -> str:
    """Draw grouped bars with success counts for full and ablated systems."""
    series = (
        PlotSeries("tracerecover", "Full TraceRecover"),
        PlotSeries("without_grounding", "Without grounding"),
        PlotSeries("without_uncertainty", "Without uncertainty"),
        PlotSeries("without_localization", "Without localization"),
    )
    width, height = 960, 600
    left, top, plot_width, plot_height = 100.0, 88.0, 680.0, 390.0
    bottom = top + plot_height
    group_width = plot_width / len(rates)
    bar_width = min(22.0, group_width / (len(series) + 1))
    output = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" '
        'viewBox="0 0 960 600" role="img" aria-labelledby="chart-title chart-desc">',
        "<title id=\"chart-title\">Ablation task success</title>",
        "<desc id=\"chart-desc\">Grouped bars show task-success rates for "
        "TraceRecover and three single-component ablations at each injected "
        "failure rate. Bar labels show successes over tasks.</desc>",
        "<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#222}"
        ".grid{stroke:#d9d9d9;stroke-width:1}.axis{stroke:#444;stroke-width:1.4}"
        ".label{font-size:12px}.small{font-size:10px}</style>",
        '<text x="480" y="38" text-anchor="middle" font-size="22" '
        'font-weight="600">Ablation task success</text>',
        '<text x="480" y="62" text-anchor="middle" font-size="12">'
        "Task success rate; labels show successes / tasks</text>",
    ]
    for tick in range(6):
        value = tick / 5
        y = bottom - value * plot_height
        output.append(
            f'<line class="grid" x1="{left:.1f}" y1="{y:.1f}" '
            f'x2="{left + plot_width:.1f}" y2="{y:.1f}"/>'
        )
        output.append(
            f'<text class="label" x="{left - 12:.1f}" y="{y + 4:.1f}" '
            f'text-anchor="end">{value:.1f}</text>'
        )
    output.extend(
        [
            f'<line class="axis" x1="{left:.1f}" y1="{top:.1f}" '
            f'x2="{left:.1f}" y2="{bottom:.1f}"/>',
            f'<line class="axis" x1="{left:.1f}" y1="{bottom:.1f}" '
            f'x2="{left + plot_width:.1f}" y2="{bottom:.1f}"/>',
            f'<text class="label" transform="translate(28 {top + plot_height/2:.1f}) '
            'rotate(-90)" text-anchor="middle">Task success rate</text>',
        ]
    )
    grouped = {
        system: {float(row["_rate"]): row for row in rows}
        for system, rows in rows_by_system.items()
    }
    for rate_index, rate in enumerate(rates):
        group_center = left + (rate_index + 0.5) * group_width
        start = group_center - len(series) * bar_width / 2
        for series_index, item in enumerate(series):
            row = grouped.get(item.key, {}).get(rate)
            value = _float(row.get("task_success_rate")) if row else None
            successes = _integer(row.get("task_successes")) if row else None
            count = _integer(row.get("task_success_denominator")) if row else None
            x = start + series_index * bar_width
            if value is None:
                output.append(
                    f'<text class="small" x="{x + bar_width/2:.1f}" '
                    f'y="{bottom - 8:.1f}" text-anchor="middle">N/A</text>'
                )
                continue
            bar_height = value * plot_height
            y = bottom - bar_height
            color = _COLORS[item.key]
            output.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 2:.1f}" '
                f'height="{bar_height:.1f}" fill="{color}">'
                f'<title>{html.escape(item.label)}, {rate:g}% failure: '
                f'{successes}/{count} ({value:.3f})</title></rect>'
            )
            output.append(
                f'<text class="small" x="{x + bar_width/2 - 1:.1f}" '
                f'y="{max(top + 12, y - 5):.1f}" text-anchor="middle">'
                f'{successes}/{count}</text>'
            )
        output.append(
            f'<text class="label" x="{group_center:.1f}" y="{bottom + 24:.1f}" '
            f'text-anchor="middle">{rate:g}%</text>'
        )
    output.append(
        f'<text class="label" x="{left + plot_width/2:.1f}" y="{bottom + 52:.1f}" '
        'text-anchor="middle">Injected tool failure rate</text>'
    )
    for index, item in enumerate(series):
        y = top + 20 + index * 26
        output.extend(
            [
                f'<rect x="806" y="{y - 7:.1f}" width="18" height="14" '
                f'fill="{_COLORS[item.key]}"/>',
                f'<text class="label" x="834" y="{y + 4:.1f}">'
                f"{html.escape(item.label)}</text>",
            ]
        )
    output.append("</svg>")
    return "\n".join(output) + "\n"


def _render_calibration_chart(result_path: Path, bins: int = 10) -> str:
    """Plot observed success against mean confidence in equal-width bins."""
    try:
        with result_path.open("r", encoding="utf-8") as input_file:
            payload = json.load(input_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read calibration data {result_path}: {exc}") from exc
    conditions = payload.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("TraceRecover result JSON must contain conditions.")
    width, height = 960, 600
    left, top, plot_width, plot_height = 100.0, 88.0, 680.0, 390.0
    bottom = top + plot_height
    output = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" '
        'viewBox="0 0 960 600" role="img" aria-labelledby="chart-title chart-desc">',
        "<title id=\"chart-title\">TraceRecover calibration diagram</title>",
        "<desc id=\"chart-desc\">Confidence is one minus the heuristic "
        "uncertainty score. Points show empirical success by equal-width "
        "confidence bins; the diagonal is perfect empirical agreement, not "
        "proof of statistical calibration.</desc>",
        "<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#222}"
        ".grid{stroke:#d9d9d9;stroke-width:1}.axis{stroke:#444;stroke-width:1.4}"
        ".ideal{stroke:#666;stroke-dasharray:6 5;stroke-width:1.5}"
        ".line{fill:none;stroke-width:2}.point{stroke:#fff;stroke-width:1.5}"
        ".label{font-size:12px}.small{font-size:11px}</style>",
        '<text x="480" y="38" text-anchor="middle" font-size="22" '
        'font-weight="600">TraceRecover calibration diagram</text>',
        '<text x="480" y="62" text-anchor="middle" font-size="12">'
        "Empirical diagnostic; heuristic confidence is not a calibrated probability</text>",
    ]
    for tick in range(6):
        value = tick / 5
        x = left + value * plot_width
        y = bottom - value * plot_height
        output.extend(
            [
                f'<line class="grid" x1="{left:.1f}" y1="{y:.1f}" '
                f'x2="{left + plot_width:.1f}" y2="{y:.1f}"/>',
                f'<line class="grid" x1="{x:.1f}" y1="{top:.1f}" '
                f'x2="{x:.1f}" y2="{bottom:.1f}"/>',
                f'<text class="label" x="{left - 12:.1f}" y="{y + 4:.1f}" '
                f'text-anchor="end">{value:.1f}</text>',
                f'<text class="label" x="{x:.1f}" y="{bottom + 22:.1f}" '
                f'text-anchor="middle">{value:.1f}</text>',
            ]
        )
    output.extend(
        [
            f'<line class="axis" x1="{left:.1f}" y1="{top:.1f}" '
            f'x2="{left:.1f}" y2="{bottom:.1f}"/>',
            f'<line class="axis" x1="{left:.1f}" y1="{bottom:.1f}" '
            f'x2="{left + plot_width:.1f}" y2="{bottom:.1f}"/>',
            f'<line class="ideal" x1="{left:.1f}" y1="{bottom:.1f}" '
            f'x2="{left + plot_width:.1f}" y2="{top:.1f}"/>',
            f'<text class="label" x="{left + plot_width/2:.1f}" '
            f'y="{bottom + 52:.1f}" text-anchor="middle">Mean heuristic confidence</text>',
            f'<text class="label" transform="translate(28 {top + plot_height/2:.1f}) '
            'rotate(-90)" text-anchor="middle">Observed task success</text>',
        ]
    )
    rates = sorted(
        {
            _float(condition.get("failure_rate_percent"))
            for condition in conditions
            if isinstance(condition, dict)
        }
        - {None}
    )
    colors = ("#4c78a8", "#f58518", "#54a24b", "#e45756", "#b279a2")
    total_samples = 0
    for rate_index, rate in enumerate(rates):
        condition = next(
            (
                item
                for item in conditions
                if isinstance(item, dict)
                and _float(item.get("failure_rate_percent")) == rate
            ),
            {},
        )
        results = condition.get("results", [])
        if not isinstance(results, list):
            continue
        buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
        for result in results:
            if not isinstance(result, dict):
                continue
            confidence = _float(result.get("confidence"))
            success = result.get("success")
            if confidence is None or not 0 <= confidence <= 1 or not isinstance(success, bool):
                continue
            buckets[min(int(confidence * bins), bins - 1)].append(
                (confidence, success)
            )
        total_samples += sum(map(len, buckets))
        color = colors[rate_index % len(colors)]
        points: list[tuple[float, float]] = []
        for index, bucket in enumerate(buckets):
            if not bucket:
                continue
            confidence = sum(item[0] for item in bucket) / len(bucket)
            accuracy = sum(item[1] for item in bucket) / len(bucket)
            x = left + confidence * plot_width
            y = bottom - accuracy * plot_height
            points.append((x, y))
            output.append(
                f'<circle class="point" cx="{x:.1f}" cy="{y:.1f}" r="5" '
                f'fill="{color}"><title>{rate:g}% failure, bin {index}, '
                f'n={len(bucket)}, confidence={confidence:.3f}, '
                f'accuracy={accuracy:.3f}</title></circle>'
            )
            output.append(
                f'<text class="small" x="{x + 6:.1f}" y="{max(top + 12, y - 7):.1f}">'
                f"n={len(bucket)}</text>"
            )
        if len(points) > 1:
            output.append(_polyline(points, color))
        legend_y = top + 20 + rate_index * 26
        output.extend(
            [
                f'<line x1="806" y1="{legend_y:.1f}" x2="832" '
                f'y2="{legend_y:.1f}" stroke="{color}" stroke-width="3"/>',
                f'<circle cx="819" cy="{legend_y:.1f}" r="4" fill="{color}"/>',
                f'<text class="label" x="840" y="{legend_y + 4:.1f}">'
                f"{rate:g}% failure</text>",
            ]
        )
    output.extend(
        [
            '<line class="ideal" x1="806" y1="190" x2="832" y2="190"/>',
            '<text class="label" x="840" y="194">Ideal agreement</text>',
            f'<text class="small" x="806" y="230">Samples: {total_samples}</text>',
            f'<text class="small" x="806" y="250">Bins: {bins} equal-width</text>',
        ]
    )
    if total_samples == 0:
        output.append(
            '<text class="label" x="440" y="275" text-anchor="middle">'
            "N/A: no valid confidence/correctness samples</text>"
        )
    output.append("</svg>")
    return "\n".join(output) + "\n"


def _polyline(points: Sequence[tuple[float, float]], color: str) -> str:
    values = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline class="line" stroke="{color}" points="{values}"/>'


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    parsed = _float(value)
    return int(parsed) if parsed is not None else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir", type=Path, default=PROJECT_ROOT / "results"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to <results-dir>\\plots.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace plot files that already exist.",
    )
    args = parser.parse_args(argv)
    try:
        paths = generate_plots(
            args.results_dir, args.output_dir, overwrite=args.overwrite
        )
    except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    for path in paths:
        print(f"Created {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
