"""JSON Lines trace logging for tool-augmented agent runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class TraceLogger:
    """Append each agent step as a structured JSON record."""

    def __init__(self, log_directory: Path | str = "logs") -> None:
        self.log_directory = Path(log_directory)
        self.log_directory.mkdir(parents=True, exist_ok=True)
        run_id = uuid4().hex[:8]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        trace_name = f"trace-{timestamp}-{run_id}"
        self.path = self.log_directory / f"{trace_name}.jsonl"
        self.analysis_path = self.log_directory / f"{trace_name}-analysis.jsonl"
        self.report_path = self.log_directory / f"{trace_name}-failure-report.json"
        self.recovery_path = self.log_directory / f"{trace_name}-recovery.jsonl"
        self.recovery_report_path = (
            self.log_directory / f"{trace_name}-recovery-report.json"
        )
        self.grounding_report_path = (
            self.log_directory / f"{trace_name}-grounding-report.json"
        )
        self.uncertainty_report_path = (
            self.log_directory / f"{trace_name}-uncertainty-report.json"
        )
        self.steps: list[dict[str, Any]] = []

    def log_step(
        self,
        *,
        thought: str,
        tool: str,
        input_data: Any,
        output: Any = None,
        error: str | None = None,
        latency: float,
        failure_metadata: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        output_state: str | None = None,
    ) -> dict[str, Any]:
        """Append a step with the schema required for later analysis."""
        if output_state is not None and output_state not in {"committed", "discarded"}:
            raise ValueError("output_state must be 'committed', 'discarded', or None.")
        step = {
            "step_id": len(self.steps) + 1,
            "thought": thought,
            "tool": tool,
            "input": input_data,
            "output": output,
            "error": error,
            "latency": round(latency, 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if failure_metadata is not None:
            step.update(failure_metadata)
        if metadata is not None:
            step.update(metadata)
        if output_state is not None:
            step["output_state"] = output_state
        with self.path.open("a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps(step, ensure_ascii=True, default=str) + "\n")
        self.steps.append(step)
        return step

    def read_trace(self) -> list[dict[str, Any]]:
        """Read the persisted execution trace for analysis."""
        with self.path.open("r", encoding="utf-8") as trace_file:
            return [json.loads(line) for line in trace_file if line.strip()]

    def log_analysis(
        self,
        detection: dict[str, Any],
        localizations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append detection and localization results to a separate JSONL file."""
        record = {
            "record_type": "failure_analysis",
            "step_id": detection.get("step_id"),
            "tool_name": detection.get("tool_name"),
            "detection": detection,
            "localizations": localizations,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.analysis_path.open("a", encoding="utf-8") as analysis_file:
            analysis_file.write(
                json.dumps(record, ensure_ascii=True, default=str) + "\n"
            )
        return record

    def write_failure_report(self, report: dict[str, Any]) -> Path:
        """Persist the final detector/localizer report without changing the trace."""
        with self.report_path.open("x", encoding="utf-8") as report_file:
            json.dump(report, report_file, ensure_ascii=True, indent=2, default=str)
            report_file.write("\n")
        return self.report_path

    def log_recovery_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        """Append one recovery attempt without altering the execution trace."""
        record = {
            "record_type": "recovery_attempt",
            **attempt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.recovery_path.open("a", encoding="utf-8") as recovery_file:
            recovery_file.write(
                json.dumps(record, ensure_ascii=True, default=str) + "\n"
            )
        return record

    def write_recovery_report(self, report: dict[str, Any]) -> Path:
        """Persist the aggregate recovery report as a separate JSON file."""
        with self.recovery_report_path.open("x", encoding="utf-8") as report_file:
            json.dump(report, report_file, ensure_ascii=True, indent=2, default=str)
            report_file.write("\n")
        return self.recovery_report_path

    def log_recovery_trace_link(
        self, link: dict[str, Any]
    ) -> dict[str, Any]:
        """Record a recovery attempt's accepted evidence step in the recovery log."""
        record = {
            "record_type": "recovery_trace_link",
            **link,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.recovery_path.open("a", encoding="utf-8") as recovery_file:
            recovery_file.write(
                json.dumps(record, ensure_ascii=True, default=str) + "\n"
            )
        return record

    def read_recovery_records(self) -> list[dict[str, Any]]:
        """Read recovery attempts and trace links persisted for this run."""
        if not self.recovery_path.exists():
            return []
        with self.recovery_path.open("r", encoding="utf-8") as recovery_file:
            return [
                json.loads(line)
                for line in recovery_file
                if line.strip()
            ]

    def write_grounding_report(self, report: dict[str, Any]) -> Path:
        """Persist the grounding analysis without modifying execution records."""
        with self.grounding_report_path.open("x", encoding="utf-8") as report_file:
            json.dump(report, report_file, ensure_ascii=True, indent=2, default=str)
            report_file.write("\n")
        return self.grounding_report_path

    def write_uncertainty_report(self, report: dict[str, Any]) -> Path:
        """Persist the confidence and abstention analysis separately."""
        with self.uncertainty_report_path.open("x", encoding="utf-8") as report_file:
            json.dump(report, report_file, ensure_ascii=True, indent=2, default=str)
            report_file.write("\n")
        return self.uncertainty_report_path