"""Bounded recovery strategies for detected and localized tool failures."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from healing.detector import DetectionResult, FailureCategory, FailureDetector
from tools.calculator import calculate
from tools.failure_injector import ToolExecution, ToolFunction
from tools.python_exec import execute
from trace.trace_logger import TraceLogger


class RecoveryStrategy(str, Enum):
    """Supported recovery actions."""

    RETRY = "RETRY"
    ARGUMENT_REPAIR = "ARGUMENT_REPAIR"
    TOOL_SWITCH = "TOOL_SWITCH"
    REPLAN = "REPLAN"


DEFAULT_STRATEGY_MAP: dict[FailureCategory, tuple[RecoveryStrategy, ...]] = {
    FailureCategory.TIMEOUT: (RecoveryStrategy.RETRY,),
    FailureCategory.EMPTY_OUTPUT: (
        RecoveryStrategy.RETRY,
        RecoveryStrategy.TOOL_SWITCH,
    ),
    FailureCategory.INVALID_ARGUMENTS: (RecoveryStrategy.ARGUMENT_REPAIR,),
    FailureCategory.UNEXPECTED_RESPONSE: (
        RecoveryStrategy.TOOL_SWITCH,
        RecoveryStrategy.REPLAN,
    ),
    FailureCategory.UNEXPECTED_OUTPUT_FORMAT: (
        RecoveryStrategy.TOOL_SWITCH,
        RecoveryStrategy.REPLAN,
    ),
    FailureCategory.MISSING_REQUIRED_FIELDS: (
        RecoveryStrategy.TOOL_SWITCH,
        RecoveryStrategy.REPLAN,
    ),
    FailureCategory.MISSING_OUTPUT: (
        RecoveryStrategy.RETRY,
        RecoveryStrategy.TOOL_SWITCH,
    ),
    FailureCategory.TOOL_EXECUTION_ERROR: (
        RecoveryStrategy.RETRY,
        RecoveryStrategy.TOOL_SWITCH,
    ),
}

Executor = Callable[[str, ToolFunction, dict[str, Any]], ToolExecution]
Planner = Callable[
    [str, list[dict[str, Any]], set[str]], dict[str, Any]
]


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of all bounded recovery attempts for one localized failure."""

    report: dict[str, Any]
    recovery_success: bool
    recovered_tool: str | None
    recovered_arguments: dict[str, Any] | None
    recovered_output: Any
    final_error: str | None


class RecoveryEngine:
    """Select and run bounded recovery strategies using Phase 3 predictions."""

    def __init__(
        self,
        detector: FailureDetector | None = None,
        strategy_map: Mapping[
            FailureCategory | str,
            Sequence[RecoveryStrategy | str],
        ] | None = None,
        max_retries: int = 1,
        max_total_attempts: int = 4,
        max_duration_seconds: float = 15.0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        if max_total_attempts < 0:
            raise ValueError("max_total_attempts cannot be negative.")
        if max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive.")
        self.detector = detector or FailureDetector()
        self.max_retries = max_retries
        self.max_total_attempts = max_total_attempts
        self.max_duration_seconds = max_duration_seconds
        configured = DEFAULT_STRATEGY_MAP if strategy_map is None else strategy_map
        self.strategy_map = {
            self._category_name(category): tuple(
                self._strategy(strategy) for strategy in strategies
            )
            for category, strategies in configured.items()
        }

    def recover(
        self,
        failure: Mapping[str, Any],
        question: str,
        observations: list[dict[str, Any]],
        tools: Mapping[str, ToolFunction],
        executor: Executor,
        planner: Planner,
        logger: TraceLogger,
    ) -> RecoveryResult:
        """Attempt configured strategies for one Phase 3 localized failure."""
        category = str(failure.get("failure_category", ""))
        original_tool = str(failure.get("tool_name", "unknown"))
        original_step_id = failure.get("step_id")
        original_arguments = self._as_arguments(failure.get("original_input"))
        if "modified_input" in failure and failure.get("modified_input") is not None:
            attempted_arguments = self._as_arguments(failure.get("modified_input"))
        else:
            attempted_arguments = dict(original_arguments)

        strategies = self.strategy_map.get(category, ())
        started = time.monotonic()
        attempt_records: list[dict[str, Any]] = []
        seen_actions = {
            self._action_key(original_tool, attempted_arguments)
        }
        excluded_tools = {original_tool}
        planning_observations = [
            *observations,
            {
                "tool": original_tool,
                "input": original_arguments,
                "error": failure.get("error_message"),
                "output": failure.get("actual_output"),
                "failure_category": category,
            },
        ]
        retry_count = 0
        successful_execution: ToolExecution | None = None
        recovered_tool: str | None = None
        recovered_arguments: dict[str, Any] | None = None
        selected_strategy: RecoveryStrategy | None = None
        final_error: str | None = None

        for strategy in strategies:
            if not self._has_budget(started, len(attempt_records)):
                final_error = "Recovery attempt or time limit reached."
                break
            selected_strategy = strategy

            if strategy is RecoveryStrategy.RETRY:
                while (
                    retry_count < self.max_retries
                    and self._has_budget(started, len(attempt_records))
                ):
                    retry_count += 1
                    execution, detection, record = self._run_tool_attempt(
                        strategy=strategy,
                        attempt_number=len(attempt_records) + 1,
                        original_step_id=original_step_id,
                        original_tool=original_tool,
                        failure=failure,
                        tool_name=original_tool,
                        arguments=original_arguments,
                        tools=tools,
                        executor=executor,
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    if detection.failure_detected is False:
                        successful_execution = execution
                        recovered_tool = original_tool
                        recovered_arguments = dict(original_arguments)
                        final_error = None
                        break
                    final_error = execution.error or detection.explanation
                if successful_execution is not None:
                    break
                continue

            if strategy is RecoveryStrategy.ARGUMENT_REPAIR:
                repaired, repair_error = self._repair_arguments(
                    original_tool, original_arguments, attempted_arguments
                )
                if repaired is None:
                    record = self._decision_record(
                        strategy,
                        len(attempt_records) + 1,
                        original_step_id,
                        original_tool,
                        category,
                        failure,
                        "Arguments could not be repaired without inventing values: "
                        + str(repair_error),
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    final_error = str(repair_error)
                    continue
                action_key = self._action_key(original_tool, repaired)
                if action_key in seen_actions:
                    record = self._decision_record(
                        strategy,
                        len(attempt_records) + 1,
                        original_step_id,
                        original_tool,
                        category,
                        failure,
                        "Duplicate tool action blocked.",
                        repaired_arguments=repaired,
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    final_error = "Duplicate repaired action was blocked."
                    continue
                seen_actions.add(action_key)
                execution, detection, record = self._run_tool_attempt(
                    strategy=strategy,
                    attempt_number=len(attempt_records) + 1,
                    original_step_id=original_step_id,
                    original_tool=original_tool,
                    failure=failure,
                    tool_name=original_tool,
                    arguments=repaired,
                    tools=tools,
                    executor=executor,
                    repaired_arguments=repaired,
                )
                attempt_records.append(logger.log_recovery_attempt(record))
                if detection.failure_detected is False:
                    successful_execution = execution
                    recovered_tool = original_tool
                    recovered_arguments = repaired
                    final_error = None
                    break
                final_error = execution.error or detection.explanation
                continue

            if strategy is RecoveryStrategy.TOOL_SWITCH:
                candidates = self._tool_switch_candidates(
                    original_tool, original_arguments, tools
                )
                if not candidates:
                    record = self._decision_record(
                        strategy,
                        len(attempt_records) + 1,
                        original_step_id,
                        original_tool,
                        category,
                        failure,
                        "No compatible alternative tool is available.",
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    final_error = "No compatible alternative tool is available."
                    continue
                for alternative_tool, alternative_arguments in candidates:
                    if not self._has_budget(started, len(attempt_records)):
                        final_error = "Recovery attempt or time limit reached."
                        break
                    action_key = self._action_key(
                        alternative_tool, alternative_arguments
                    )
                    if action_key in seen_actions:
                        record = self._decision_record(
                            strategy,
                            len(attempt_records) + 1,
                            original_step_id,
                            original_tool,
                            category,
                            failure,
                            "Duplicate alternative action blocked.",
                            alternative_tool=alternative_tool,
                            repaired_arguments=alternative_arguments,
                        )
                        attempt_records.append(logger.log_recovery_attempt(record))
                        final_error = "Duplicate alternative action was blocked."
                        continue
                    seen_actions.add(action_key)
                    excluded_tools.add(alternative_tool)
                    execution, detection, record = self._run_tool_attempt(
                        strategy=strategy,
                        attempt_number=len(attempt_records) + 1,
                        original_step_id=original_step_id,
                        original_tool=original_tool,
                        failure=failure,
                        tool_name=alternative_tool,
                        arguments=alternative_arguments,
                        tools=tools,
                        executor=executor,
                        alternative_tool=alternative_tool,
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    if detection.failure_detected is False:
                        successful_execution = execution
                        recovered_tool = alternative_tool
                        recovered_arguments = alternative_arguments
                        final_error = None
                        break
                    final_error = execution.error or detection.explanation
                if successful_execution is not None:
                    break
                continue

            if strategy is RecoveryStrategy.REPLAN:
                try:
                    decision = planner(
                        question, planning_observations, set(excluded_tools)
                    )
                except (RuntimeError, ValueError) as exc:
                    record = self._decision_record(
                        strategy,
                        len(attempt_records) + 1,
                        original_step_id,
                        original_tool,
                        category,
                        failure,
                        f"Planner could not produce a revised action: {exc}",
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    final_error = str(exc)
                    continue
                planned_tool = decision.get("action")
                planned_arguments = self._as_arguments(
                    decision.get("arguments")
                )
                if (
                    not isinstance(planned_tool, str)
                    or planned_tool == "final"
                    or planned_tool not in tools
                ):
                    record = self._decision_record(
                        strategy,
                        len(attempt_records) + 1,
                        original_step_id,
                        original_tool,
                        category,
                        failure,
                        "Planner did not return an executable alternative tool action.",
                        planned_action=decision,
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    final_error = "Planner did not return an executable alternative."
                    continue
                action_key = self._action_key(planned_tool, planned_arguments)
                if planned_tool in excluded_tools or action_key in seen_actions:
                    record = self._decision_record(
                        strategy,
                        len(attempt_records) + 1,
                        original_step_id,
                        original_tool,
                        category,
                        failure,
                        "Duplicate planned action blocked.",
                        alternative_tool=planned_tool,
                        repaired_arguments=planned_arguments,
                        planned_action=decision,
                    )
                    attempt_records.append(logger.log_recovery_attempt(record))
                    final_error = "Duplicate planned action was blocked."
                    continue
                seen_actions.add(action_key)
                excluded_tools.add(planned_tool)
                execution, detection, record = self._run_tool_attempt(
                    strategy=strategy,
                    attempt_number=len(attempt_records) + 1,
                    original_step_id=original_step_id,
                    original_tool=original_tool,
                    failure=failure,
                    tool_name=planned_tool,
                    arguments=planned_arguments,
                    tools=tools,
                    executor=executor,
                    alternative_tool=planned_tool,
                    planned_action=decision,
                )
                attempt_records.append(logger.log_recovery_attempt(record))
                if detection.failure_detected is False:
                    successful_execution = execution
                    recovered_tool = planned_tool
                    recovered_arguments = planned_arguments
                    final_error = None
                    break
                final_error = execution.error or detection.explanation

        report = {
            "recovery_attempted": bool(attempt_records),
            "original_step_id": original_step_id,
            "original_tool_name": original_tool,
            "failure_category": category,
            "strategy": selected_strategy.value if selected_strategy else None,
            "strategies_used": list(
                dict.fromkeys(record["strategy"] for record in attempt_records)
            ),
            "attempt_count": len(attempt_records),
            "recovery_success": successful_execution is not None,
            "recovered_tool": recovered_tool,
            "recovered_arguments": recovered_arguments,
            "recovered_output": (
                successful_execution.output if successful_execution else None
            ),
            "final_error": final_error,
            "attempts": attempt_records,
        }
        return RecoveryResult(
            report=report,
            recovery_success=successful_execution is not None,
            recovered_tool=recovered_tool,
            recovered_arguments=recovered_arguments,
            recovered_output=(
                successful_execution.output if successful_execution else None
            ),
            final_error=final_error,
        )

    def _run_tool_attempt(
        self,
        *,
        strategy: RecoveryStrategy,
        attempt_number: int,
        original_step_id: Any,
        original_tool: str,
        failure: Mapping[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        tools: Mapping[str, ToolFunction],
        executor: Executor,
        repaired_arguments: dict[str, Any] | None = None,
        alternative_tool: str | None = None,
        planned_action: Mapping[str, Any] | None = None,
    ) -> tuple[ToolExecution, DetectionResult, dict[str, Any]]:
        started = time.perf_counter()
        tool = tools[tool_name]
        execution = executor(tool_name, tool, arguments)
        latency = time.perf_counter() - started
        synthetic_step = {
            "step_id": original_step_id,
            "tool": tool_name,
            "input": arguments,
            "output": execution.output,
            "error": execution.error,
            "execution_status": execution.execution_status,
            "failure_injected": execution.failure_injected,
            "failure_type": (
                execution.failure_type.value if execution.failure_type else None
            ),
        }
        detection = self.detector.detect_step(synthetic_step)
        success = not detection.failure_detected
        execution_result = {
            "tool_name": tool_name,
            "input": arguments,
            "modified_input": execution.execution_arguments,
            "output": execution.output,
            "error": execution.error,
            "execution_status": execution.execution_status,
            "failure_injected": execution.failure_injected,
            "reported_failure_type": (
                execution.failure_type.value if execution.failure_type else None
            ),
            "detector_failure_detected": detection.failure_detected,
            "detector_failure_category": detection.failure_category.value,
        }
        record = self._base_attempt_record(
            strategy,
            attempt_number,
            original_step_id,
            original_tool,
            str(failure.get("failure_category", "")),
            failure,
        )
        record.update(
            {
                "recovery_tool": tool_name,
                "alternative_tool": alternative_tool,
                "repaired_arguments": repaired_arguments,
                "planned_action": dict(planned_action) if planned_action else None,
                "recovery_execution_result": execution_result,
                "recovery_success": success,
                "execution_latency": latency,
                "explanation": (
                    "Recovery execution passed detector validation."
                    if success
                    else detection.explanation
                ),
            }
        )
        return execution, detection, record

    def _repair_arguments(
        self,
        tool_name: str,
        original_arguments: dict[str, Any],
        attempted_arguments: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Repair only from valid values already present in the original call."""
        candidate = dict(attempted_arguments)
        required_fields = {
            "search": ("query",),
            "calculator": ("expression",),
            "python_exec": ("code",),
            "mock_api": ("ticket_id",),
        }.get(tool_name)
        if required_fields is None:
            return None, f"No argument-repair rule exists for tool {tool_name!r}."
        for field in required_fields:
            value = candidate.get(field)
            if not isinstance(value, str) or not value.strip():
                source_value = original_arguments.get(field)
                if not isinstance(source_value, str) or not source_value.strip():
                    return None, (
                        f"Required value {field!r} is unavailable in the original input."
                    )
                candidate[field] = source_value.strip()
            elif isinstance(value, str):
                candidate[field] = value.strip()
        validation_error = self.detector.validate_tool_input(tool_name, candidate)
        if validation_error:
            return None, validation_error
        try:
            if tool_name == "calculator":
                calculate(candidate["expression"])
            elif tool_name == "python_exec":
                execute(candidate["code"])
        except (ArithmeticError, IndexError, KeyError, TypeError, ValueError) as exc:
            return None, f"Original argument failed safe validation: {exc}"
        return candidate, None

    @staticmethod
    def _tool_switch_candidates(
        original_tool: str,
        original_arguments: dict[str, Any],
        tools: Mapping[str, ToolFunction],
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return only directly compatible local tool substitutions."""
        candidates: list[tuple[str, dict[str, Any]]] = []
        if original_tool == "calculator" and "python_exec" in tools:
            expression = original_arguments.get("expression")
            if isinstance(expression, str) and expression.strip():
                candidates.append(("python_exec", {"code": expression.strip()}))
        elif original_tool == "python_exec" and "calculator" in tools:
            code = original_arguments.get("code")
            if isinstance(code, str) and re.fullmatch(
                r"[\d\s.+\-*/()%]+", code.strip()
            ) and any(operator in code for operator in "+-*/%"):
                candidates.append(("calculator", {"expression": code.strip()}))
        return candidates

    def _decision_record(
        self,
        strategy: RecoveryStrategy,
        attempt_number: int,
        original_step_id: Any,
        original_tool: str,
        failure_category: str,
        failure: Mapping[str, Any],
        explanation: str,
        **details: Any,
    ) -> dict[str, Any]:
        record = self._base_attempt_record(
            strategy,
            attempt_number,
            original_step_id,
            original_tool,
            failure_category,
            failure,
        )
        record.update(
            {
                "recovery_tool": details.get("alternative_tool"),
                "alternative_tool": details.get("alternative_tool"),
                "repaired_arguments": details.get("repaired_arguments"),
                "planned_action": details.get("planned_action"),
                "recovery_execution_result": None,
                "recovery_success": False,
                "execution_latency": 0.0,
                "explanation": explanation,
            }
        )
        return record

    @staticmethod
    def _base_attempt_record(
        strategy: RecoveryStrategy,
        attempt_number: int,
        original_step_id: Any,
        original_tool: str,
        failure_category: str,
        failure: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "original_step_id": original_step_id,
            "original_tool_name": original_tool,
            "failure_category": failure_category,
            "original_input": failure.get("original_input"),
            "original_output": failure.get("actual_output"),
            "original_error": failure.get("error_message"),
            "strategy": strategy.value,
            "attempt_number": attempt_number,
            "reported_failure_injected": failure.get(
                "reported_failure_injected"
            ),
            "reported_failure_type": failure.get("reported_failure_type"),
        }

    def _has_budget(self, started: float, completed_attempts: int) -> bool:
        return (
            completed_attempts < self.max_total_attempts
            and time.monotonic() - started < self.max_duration_seconds
        )

    @staticmethod
    def _as_arguments(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _action_key(tool_name: str, arguments: Mapping[str, Any]) -> tuple[str, str]:
        return (
            tool_name,
            json.dumps(arguments, sort_keys=True, ensure_ascii=True, default=str),
        )

    @staticmethod
    def _category_name(value: FailureCategory | str) -> str:
        return value.value if isinstance(value, FailureCategory) else str(value).upper()

    @staticmethod
    def _strategy(value: RecoveryStrategy | str) -> RecoveryStrategy:
        return value if isinstance(value, RecoveryStrategy) else RecoveryStrategy(value.upper())