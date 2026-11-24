"""Seeded, local-only tool failure injection for reproducible experiments."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable


class FailureType(str, Enum):
    """Failure modes supported by the injector."""

    TIMEOUT = "timeout"
    WRONG_OUTPUT = "wrong_output"
    EMPTY_RESULT = "empty_result"
    BAD_ARGUMENTS = "bad_arguments"


ToolFunction = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolExecution:
    """Result and audit details for one tool invocation."""

    output: Any
    error: str | None
    failure_type: FailureType | None
    failure_injected: bool
    original_arguments: dict[str, Any]
    execution_arguments: dict[str, Any]
    execution_status: str

    def trace_metadata(self, tool_name: str) -> dict[str, Any]:
        """Return the fields to merge into the corresponding JSONL trace step."""
        return {
            "tool_name": tool_name,
            "failure_type": self.failure_type.value if self.failure_type else None,
            "failure_injected": self.failure_injected,
            "original_input": self.original_arguments,
            "modified_input": self.execution_arguments,
            "modified_output": self.output,
            "execution_status": self.execution_status,
        }


class FailureInjector:
    """Apply configured failures to tool calls using a reproducible RNG seed."""

    def __init__(
        self,
        failure_rate: float = 0,
        seed: int | None = 42,
        enabled: bool = True,
        enabled_types: Iterable[FailureType | str] | None = None,
    ) -> None:
        if isinstance(failure_rate, bool) or not isinstance(failure_rate, (int, float)):
            raise TypeError("failure_rate must be a percentage between 0 and 100.")
        if not 0 <= failure_rate <= 100:
            raise ValueError("failure_rate must be between 0 and 100.")
        self.failure_rate = float(failure_rate)
        self.enabled = enabled
        self._random = random.Random(seed)
        configured_types = (
            tuple(FailureType) if enabled_types is None else tuple(enabled_types)
        )
        try:
            self.enabled_types = tuple(
                item if isinstance(item, FailureType) else FailureType(item.lower())
                for item in configured_types
            )
        except (AttributeError, ValueError) as exc:
            valid_types = ", ".join(item.value for item in FailureType)
            raise ValueError(
                f"Unknown failure type; choose from {valid_types}."
            ) from exc

    def execute(
        self,
        tool_name: str,
        tool: ToolFunction,
        arguments: dict[str, Any],
    ) -> ToolExecution:
        """Execute a tool normally or return one deliberately injected failure."""
        original_arguments = dict(arguments)
        failure_type = self._select_failure()
        if failure_type is None:
            try:
                output = tool(arguments)
            except Exception as exc:
                return ToolExecution(
                    output=None,
                    error=f"{type(exc).__name__}: {exc}",
                    failure_type=None,
                    failure_injected=False,
                    original_arguments=original_arguments,
                    execution_arguments=dict(arguments),
                    execution_status="failed",
                )
            return ToolExecution(
                output=output,
                error=None,
                failure_type=None,
                failure_injected=False,
                original_arguments=original_arguments,
                execution_arguments=dict(arguments),
                execution_status="success",
            )

        if failure_type is FailureType.TIMEOUT:
            return self._injected_execution(
                failure_type,
                original_arguments,
                original_arguments,
                error=f"TimeoutError: Simulated timeout for tool {tool_name!r}.",
            )

        if failure_type is FailureType.BAD_ARGUMENTS:
            malformed_arguments: dict[str, Any] = {}
            output = None
            try:
                output = tool(malformed_arguments)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            else:
                error = "ValueError: Tool received intentionally malformed arguments."
            return self._injected_execution(
                failure_type,
                original_arguments,
                malformed_arguments,
                error=error,
                output=output,
            )

        try:
            original_output = tool(arguments)
        except Exception as exc:
            return self._injected_execution(
                failure_type,
                original_arguments,
                original_arguments,
                error=f"{type(exc).__name__}: {exc}",
            )

        if failure_type is FailureType.EMPTY_RESULT:
            return self._injected_execution(
                failure_type,
                original_arguments,
                original_arguments,
                output=None,
            )

        wrong_output = self._wrong_output(original_output)
        return self._injected_execution(
            failure_type,
            original_arguments,
            original_arguments,
            output=wrong_output,
        )

    def _select_failure(self) -> FailureType | None:
        """Select a failure mode according to the configured rate and allowlist."""
        if (
            not self.enabled
            or self.failure_rate == 0
            or not self.enabled_types
            or self._random.random() * 100 >= self.failure_rate
        ):
            return None
        return self._random.choice(self.enabled_types)

    @staticmethod
    def _wrong_output(output: Any) -> Any:
        """Return a visibly corrupted value while retaining common output types."""
        if isinstance(output, bool):
            return not output
        if isinstance(output, int):
            return output + 1
        if isinstance(output, float):
            return output + 1.0
        if isinstance(output, str):
            return "[INJECTED WRONG OUTPUT]"
        if isinstance(output, dict):
            return {"injected_wrong_output": True}
        if isinstance(output, list):
            return ["INJECTED WRONG OUTPUT"]
        return "[INJECTED WRONG OUTPUT]"

    @staticmethod
    def _injected_execution(
        failure_type: FailureType,
        original_arguments: dict[str, Any],
        execution_arguments: dict[str, Any],
        *,
        output: Any = None,
        error: str | None = None,
    ) -> ToolExecution:
        """Build a consistent result for an injected tool failure."""
        return ToolExecution(
            output=output,
            error=error,
            failure_type=failure_type,
            failure_injected=True,
            original_arguments=original_arguments,
            execution_arguments=execution_arguments,
            execution_status="injected_failure",
        )