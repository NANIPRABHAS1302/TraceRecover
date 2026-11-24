"""Deterministic failure detection from recorded tool trace entries."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from tools.calculator import calculate
from tools.python_exec import execute


class FailureCategory(str, Enum):
    """Failure categories emitted by the rule-based detector."""

    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    MISSING_OUTPUT = "MISSING_OUTPUT"
    UNEXPECTED_OUTPUT_FORMAT = "UNEXPECTED_OUTPUT_FORMAT"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    MISSING_REQUIRED_FIELDS = "MISSING_REQUIRED_FIELDS"
    UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE"


class Severity(str, Enum):
    """Human-readable impact levels for a detected failure."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class DetectionResult:
    """A rule-based prediction and the evidence used to reach it."""

    step_id: int | str | None
    tool_name: str
    failure_detected: bool
    failure_category: FailureCategory
    severity: Severity
    explanation: str
    evidence: dict[str, Any]
    confidence: float
    failure_origin: str
    reported_failure_injected: bool | None
    reported_failure_type: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this prediction."""
        return {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "failure_detected": self.failure_detected,
            "failure_category": self.failure_category.value,
            "severity": self.severity.value,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "failure_origin": self.failure_origin,
            "reported_failure_injected": self.reported_failure_injected,
            "reported_failure_type": self.reported_failure_type,
        }


_REQUIRED_INPUT_FIELDS: dict[str, dict[str, type]] = {
    "search": {"query": str},
    "calculator": {"expression": str},
    "python_exec": {"code": str},
    "mock_api": {"ticket_id": str},
}
_REQUIRED_MOCK_API_FIELDS = {"ticket_id", "status", "owner"}
_WRONG_OUTPUT_MARKER = "[INJECTED WRONG OUTPUT]"


class FailureDetector:
    """Inspect tool inputs, outputs, errors, and status without using injection labels."""

    def validate_tool_input(self, tool_name: str, arguments: Any) -> str | None:
        """Return a deterministic validation error for a known tool's arguments."""
        return self._invalid_input(tool_name, arguments)

    def detect_trace(
        self, trace_entries: Sequence[Mapping[str, Any]]
    ) -> list[DetectionResult]:
        """Return one prediction for every tool step in a trace."""
        results: list[DetectionResult] = []
        for index, entry in enumerate(trace_entries, start=1):
            tool_name = str(entry.get("tool_name") or entry.get("tool") or "")
            if not tool_name or tool_name in {"final", "llm"}:
                continue
            results.append(self.detect_step(entry, fallback_step_id=index))
        return results

    def detect_step(
        self, step: Mapping[str, Any], fallback_step_id: int = 1
    ) -> DetectionResult:
        """Apply deterministic rules to one logged tool invocation."""
        step_id_value = step.get("step_id", fallback_step_id)
        step_id = (
            step_id_value
            if isinstance(step_id_value, (int, str))
            else fallback_step_id
        )
        tool_name = str(step.get("tool_name") or step.get("tool") or "unknown")
        original_input = step.get("original_input", step.get("input"))
        effective_input = step.get("modified_input", step.get("input"))
        output_present = "output" in step
        output = step.get("output")
        error = step.get("error")
        status = step.get("execution_status")
        evidence = {
            "input": original_input,
            "effective_input": effective_input,
            "output_present": output_present,
            "output": output,
            "error": error,
            "execution_status": status,
        }

        timeout = self._is_timeout(error)
        if timeout:
            return self._failure(
                step, step_id, tool_name, FailureCategory.TIMEOUT, Severity.HIGH,
                "The tool error reports a timeout.", evidence, 1.0,
            )

        invalid_input = self._invalid_input(tool_name, effective_input)
        if invalid_input:
            evidence["matched_rule"] = invalid_input
            return self._failure(
                step, step_id, tool_name, FailureCategory.INVALID_ARGUMENTS,
                Severity.HIGH, invalid_input, evidence, 0.99,
            )

        if error:
            category, explanation = self._error_category(tool_name, str(error))
            return self._failure(
                step, step_id, tool_name, category, Severity.HIGH,
                explanation, evidence, 0.98,
            )

        if status in {"failed", "error", "timeout"}:
            evidence["matched_rule"] = f"execution_status={status}"
            return self._failure(
                step, step_id, tool_name, FailureCategory.TOOL_EXECUTION_ERROR,
                Severity.HIGH,
                f"The tool execution status is {status!r}.",
                evidence, 0.97,
            )

        if not output_present:
            return self._failure(
                step, step_id, tool_name, FailureCategory.MISSING_OUTPUT,
                Severity.HIGH, "The tool trace has no output field.",
                evidence, 0.99,
            )

        if output is None:
            python_code = (
                effective_input.get("code")
                if isinstance(effective_input, Mapping)
                else None
            )
            if tool_name == "python_exec" and python_code == "None":
                return self._success(step, step_id, tool_name, evidence)
            return self._failure(
                step, step_id, tool_name, FailureCategory.EMPTY_OUTPUT,
                Severity.MEDIUM, "The tool returned an empty null output.",
                evidence, 0.94,
            )

        if self._is_wrong_output_marker(output):
            return self._failure(
                step, step_id, tool_name, FailureCategory.UNEXPECTED_RESPONSE,
                Severity.MEDIUM,
                "The output contains an explicit unexpected-response marker.",
                evidence, 0.99,
            )

        if isinstance(output, str) and not output.strip():
            return self._failure(
                step, step_id, tool_name, FailureCategory.EMPTY_OUTPUT,
                Severity.MEDIUM, "The tool returned an empty string.",
                evidence, 0.98,
            )

        if tool_name == "search" and not isinstance(output, str):
            return self._failure(
                step, step_id, tool_name,
                FailureCategory.UNEXPECTED_OUTPUT_FORMAT, Severity.MEDIUM,
                "The search tool should return a non-empty string.",
                evidence, 0.99,
            )

        if tool_name == "calculator":
            result = self._check_calculator_output(effective_input, output)
            if result is not None:
                category, explanation = result
                return self._failure(
                    step, step_id, tool_name, category, Severity.MEDIUM,
                    explanation, evidence, 0.99,
                )

        if tool_name == "python_exec":
            result = self._check_python_output(effective_input, output)
            if result is not None:
                category, explanation = result
                return self._failure(
                    step, step_id, tool_name, category, Severity.MEDIUM,
                    explanation, evidence, 0.99,
                )

        if tool_name == "mock_api":
            result = self._check_mock_api_output(effective_input, output)
            if result is not None:
                category, explanation = result
                return self._failure(
                    step, step_id, tool_name, category, Severity.MEDIUM,
                    explanation, evidence, 0.99,
                )

        return self._success(step, step_id, tool_name, evidence)

    @staticmethod
    def _is_timeout(error: Any) -> bool:
        if error is None:
            return False
        message = str(error).lower()
        return "timeouterror" in message or "timed out" in message or "timeout" in message

    @staticmethod
    def _invalid_input(tool_name: str, arguments: Any) -> str | None:
        expected = _REQUIRED_INPUT_FIELDS.get(tool_name)
        if expected is None:
            return None
        if not isinstance(arguments, Mapping):
            return "The tool arguments are not an object."
        for field, expected_type in expected.items():
            value = arguments.get(field)
            if not isinstance(value, expected_type) or not value.strip():
                return f"Required argument {field!r} is missing, empty, or has the wrong type."
        if tool_name == "mock_api" and not re.fullmatch(
            r"T-\d+", arguments["ticket_id"].strip(), flags=re.IGNORECASE
        ):
            return "The ticket_id argument does not match the expected T-number format."
        return None

    @staticmethod
    def _error_category(
        tool_name: str, error: str
    ) -> tuple[FailureCategory, str]:
        error_type = error.split(":", 1)[0].strip()
        if error_type in {"KeyError", "TypeError"}:
            return (
                FailureCategory.INVALID_ARGUMENTS,
                f"The tool rejected its arguments with {error_type}.",
            )
        if error_type == "ValueError" and tool_name in _REQUIRED_INPUT_FIELDS:
            return (
                FailureCategory.INVALID_ARGUMENTS,
                "The tool reported that its input value was invalid.",
            )
        return (
            FailureCategory.TOOL_EXECUTION_ERROR,
            f"The tool returned an execution error: {error}",
        )

    @staticmethod
    def _is_wrong_output_marker(output: Any) -> bool:
        if isinstance(output, str):
            return _WRONG_OUTPUT_MARKER in output
        return isinstance(output, Mapping) and output.get("injected_wrong_output") is True

    @staticmethod
    def _check_calculator_output(
        arguments: Any, output: Any
    ) -> tuple[FailureCategory, str] | None:
        if isinstance(output, bool) or not isinstance(output, (int, float)):
            return (
                FailureCategory.UNEXPECTED_OUTPUT_FORMAT,
                "The calculator should return a finite numeric value.",
            )
        if isinstance(output, float) and not math.isfinite(output):
            return (
                FailureCategory.UNEXPECTED_OUTPUT_FORMAT,
                "The calculator returned a non-finite numeric value.",
            )
        if isinstance(arguments, Mapping):
            expression = arguments.get("expression")
            try:
                expected = calculate(expression) if isinstance(expression, str) else None
            except (ArithmeticError, OverflowError, ValueError):
                expected = None
            if expected is not None and output != expected:
                return (
                    FailureCategory.UNEXPECTED_RESPONSE,
                    "The calculator output does not match its logged expression.",
                )
        return None

    @staticmethod
    def _check_python_output(
        arguments: Any, output: Any
    ) -> tuple[FailureCategory, str] | None:
        if isinstance(arguments, Mapping):
            code = arguments.get("code")
            if isinstance(code, str):
                try:
                    expected = execute(code)
                except (ArithmeticError, IndexError, KeyError, TypeError, ValueError):
                    return None
                if output != expected:
                    return (
                        FailureCategory.UNEXPECTED_RESPONSE,
                        "The Python-tool output does not match its logged expression.",
                    )
        return None

    @staticmethod
    def _check_mock_api_output(
        arguments: Any, output: Any
    ) -> tuple[FailureCategory, str] | None:
        if not isinstance(output, Mapping):
            return (
                FailureCategory.UNEXPECTED_OUTPUT_FORMAT,
                "The mock API should return a ticket object.",
            )
        missing_fields = sorted(_REQUIRED_MOCK_API_FIELDS.difference(output))
        if missing_fields:
            return (
                FailureCategory.MISSING_REQUIRED_FIELDS,
                f"The mock API response is missing required fields: {', '.join(missing_fields)}.",
            )
        if any(
            not isinstance(output[field], str) or not output[field].strip()
            for field in _REQUIRED_MOCK_API_FIELDS
        ):
            return (
                FailureCategory.UNEXPECTED_OUTPUT_FORMAT,
                "Ticket response fields must be non-empty strings.",
            )
        if isinstance(arguments, Mapping) and (
            output.get("ticket_id", "").upper()
            != str(arguments.get("ticket_id", "")).upper()
        ):
            return (
                FailureCategory.UNEXPECTED_RESPONSE,
                "The returned ticket_id does not match the requested ticket.",
            )
        return None

    def _failure(
        self,
        step: Mapping[str, Any],
        step_id: int | str | None,
        tool_name: str,
        category: FailureCategory,
        severity: Severity,
        explanation: str,
        evidence: dict[str, Any],
        confidence: float,
    ) -> DetectionResult:
        return DetectionResult(
            step_id=step_id,
            tool_name=tool_name,
            failure_detected=True,
            failure_category=category,
            severity=severity,
            explanation=explanation,
            evidence=evidence,
            confidence=confidence,
            **self._provenance(step),
        )

    def _success(
        self,
        step: Mapping[str, Any],
        step_id: int | str | None,
        tool_name: str,
        evidence: dict[str, Any],
    ) -> DetectionResult:
        return DetectionResult(
            step_id=step_id,
            tool_name=tool_name,
            failure_detected=False,
            failure_category=FailureCategory.NONE,
            severity=Severity.LOW,
            explanation="No configured failure rule matched this tool step.",
            evidence=evidence,
            confidence=0.95,
            **self._provenance(step),
        )

    @staticmethod
    def _provenance(step: Mapping[str, Any]) -> dict[str, Any]:
        """Keep injection metadata separate from rule-based detector evidence."""
        reported_injected = step.get("failure_injected")
        if reported_injected is True:
            origin = "injected"
        elif reported_injected is False:
            origin = "not_injected"
        else:
            origin = "unknown"
        reported_type = step.get("failure_type")
        return {
            "failure_origin": origin,
            "reported_failure_injected": (
                reported_injected if isinstance(reported_injected, bool) else None
            ),
            "reported_failure_type": (
                str(reported_type) if reported_type is not None else None
            ),
        }