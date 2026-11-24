"""Baseline ReAct agent with tool execution and per-step trace logging."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from config import LLMClient
from grounding.claim_extractor import ClaimExtractor
from grounding.evidence_checker import EvidenceChecker
from healing.detector import FailureDetector
from healing.localizer import FailureLocalizer
from healing.recovery import RecoveryEngine
from tools.calculator import calculate
from tools.executor import ToolExecutor
from tools.failure_injector import FailureInjector, ToolExecution
from tools.mock_api import get_ticket
from tools.python_exec import execute
from tools.search_tool import search
from trace.trace_logger import TraceLogger
from uncertainty.abstention import AbstentionPolicy
from uncertainty.estimator import UncertaintyEstimator

from .planner import RuleBasedPlanner, build_prompt, parse_decision


ToolFunction = Callable[[dict[str, Any]], Any]


class ActionPlanner(Protocol):
    """Interface for deterministic or provider-backed ReAct planners."""

    def next_action(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str] | None = None,
    ) -> dict[str, Any]: ...


class FailureInjectorProtocol(Protocol):
    """Structural interface for normal or test-specific tool injectors."""

    def execute(
        self, tool_name: str, tool: ToolFunction, arguments: dict[str, Any]
    ) -> ToolExecution: ...


def _default_tools() -> dict[str, ToolFunction]:
    return {
        "search": lambda arguments: search(arguments["query"]),
        "calculator": lambda arguments: calculate(arguments["expression"]),
        "python_exec": lambda arguments: execute(arguments["code"]),
        "mock_api": lambda arguments: get_ticket(arguments["ticket_id"]),
    }


@dataclass(frozen=True)
class AgentResult:
    """Answer and trace information produced by one agent run."""

    answer: str
    trace: list[dict[str, Any]]
    log_path: Path
    failure_report: dict[str, Any]
    analysis_path: Path
    report_path: Path | None
    recovery_report: dict[str, Any]
    recovery_log_path: Path
    recovery_report_path: Path | None
    grounding_report: dict[str, Any]
    grounding_report_path: Path | None
    grounded_answer: str
    uncertainty_report: dict[str, Any]
    uncertainty_report_path: Path | None
    final_decision: str
    final_response: str


class ReactAgent:
    """Run a bounded Thought -> Action -> Observation -> Final Answer loop."""

    def __init__(
        self,
        client: LLMClient | None = None,
        tools: dict[str, ToolFunction] | None = None,
        failure_injector: FailureInjectorProtocol | None = None,
        detector: FailureDetector | None = None,
        localizer: FailureLocalizer | None = None,
        recovery_engine: RecoveryEngine | None = None,
        claim_extractor: ClaimExtractor | None = None,
        evidence_checker: EvidenceChecker | None = None,
        uncertainty_estimator: UncertaintyEstimator | None = None,
        abstention_policy: AbstentionPolicy | None = None,
        log_directory: Path | str = "logs",
        max_steps: int = 8,
        planner: ActionPlanner | None = None,
        enable_failure_detection: bool = True,
        enable_localization: bool = True,
        enable_recovery: bool = True,
        enable_grounding: bool = True,
        enable_uncertainty: bool = True,
        recovery_failure_injector: FailureInjectorProtocol | None = None,
        tool_executor: ToolExecutor | None = None,
        use_nli: bool = False,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least one.")
        feature_flags = (
            enable_failure_detection,
            enable_localization,
            enable_recovery,
            enable_grounding,
            enable_uncertainty,
        )
        if any(not isinstance(flag, bool) for flag in feature_flags):
            raise TypeError("Phase feature flags must be booleans.")
        if not enable_failure_detection and (enable_localization or enable_recovery):
            raise ValueError(
                "Localization and recovery require failure detection to be enabled."
            )
        self.client = client or LLMClient()
        self.tool_executor = tool_executor
        self.tools = (
            tools
            or (tool_executor.as_tool_map() if tool_executor is not None else _default_tools())
        )
        self.failure_injector: FailureInjectorProtocol = (
            failure_injector or FailureInjector()
        )
        self.recovery_failure_injector = recovery_failure_injector
        self.detector = detector or FailureDetector()
        self.localizer = localizer or FailureLocalizer(self.detector)
        self.recovery_engine = recovery_engine or RecoveryEngine(self.detector)
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.evidence_checker = evidence_checker or EvidenceChecker(
            self.claim_extractor, self.detector, use_nli=use_nli
        )
        self.uncertainty_estimator = uncertainty_estimator or UncertaintyEstimator(
            detector=self.detector
        )
        self.abstention_policy = abstention_policy or AbstentionPolicy(
            require_grounding=enable_grounding
        )
        self.enable_failure_detection = enable_failure_detection
        self.enable_localization = enable_localization
        self.enable_recovery = enable_recovery
        self.enable_grounding = enable_grounding
        self.enable_uncertainty = enable_uncertainty
        self.log_directory = Path(log_directory)
        self.max_steps = max_steps
        self.local_planner = planner or RuleBasedPlanner()

    def run(self, question: str) -> AgentResult:
        """Answer one question, logging every tool action and final response."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question must be a non-empty string.")
        logger = TraceLogger(self.log_directory)
        observations: list[dict[str, Any]] = []
        recovery_reports: list[dict[str, Any]] = []

        for _ in range(self.max_steps):
            started = time.perf_counter()
            if observations and observations[-1].get("recovery_failed"):
                return self._finish(
                    question,
                    "The operation could not be completed reliably after the "
                    "configured recovery attempts.",
                    "Recovery strategies were exhausted; do not make an unsupported claim.",
                    logger,
                    recovery_reports,
                    time.perf_counter() - started,
                )
            try:
                decision = self._plan_action(question, observations)
            except (RuntimeError, ValueError) as exc:
                logger.log_step(
                    thought="Request the next ReAct step from the configured LLM.",
                    tool="llm",
                    input_data={"question": question},
                    error=str(exc),
                    latency=time.perf_counter() - started,
                )
                raise

            action = decision["action"]
            thought = decision.get("thought", "")
            arguments = decision.get("arguments", {})
            if action == "final":
                return self._finish(
                    question,
                    decision["final_answer"],
                    thought,
                    logger,
                    recovery_reports,
                    time.perf_counter() - started,
                )
            if action not in self.tools:
                error = f"Unknown tool action: {action}"
                logger.log_step(
                    thought=thought,
                    tool=action,
                    input_data=arguments,
                    error=error,
                    latency=time.perf_counter() - started,
                )
                raise ValueError(error)

            if self.tool_executor is not None:
                outcome = self.tool_executor.execute(
                    action,
                    arguments,
                    failure_injector=self.failure_injector,
                )
                execution = outcome.execution
                step_output_state: str | None = outcome.output_state
            else:
                execution = self.failure_injector.execute(
                    action, self.tools[action], arguments
                )
                step_output_state = None
            failure_metadata = (
                execution.trace_metadata(action)
                if execution.failure_injected
                else None
            )
            if execution.error is not None:
                tool_step = logger.log_step(
                    thought=thought,
                    tool=action,
                    input_data=arguments,
                    error=execution.error,
                    latency=time.perf_counter() - started,
                    failure_metadata=failure_metadata,
                    output_state=step_output_state,
                )
            else:
                tool_step = logger.log_step(
                    thought=thought,
                    tool=action,
                    input_data=arguments,
                    output=execution.output,
                    latency=time.perf_counter() - started,
                    failure_metadata=failure_metadata,
                    output_state=step_output_state,
                )
            latest_detections = []
            localized: list[dict[str, Any]] = []
            if self.enable_failure_detection:
                trace_entries = logger.read_trace()
                detections = self.detector.detect_trace(trace_entries)
                latest_detections = [
                    detection
                    for detection in detections
                    if detection.step_id == tool_step["step_id"]
                ]
                if self.enable_localization:
                    localized = self.localizer.localize(
                        trace_entries, latest_detections
                    )
                for detection in latest_detections:
                    logger.log_analysis(detection.to_dict(), localized)
            detected_failure = any(
                detection.failure_detected for detection in latest_detections
            )
            if localized and self.enable_recovery:
                recovery = self.recovery_engine.recover(
                    failure=localized[0],
                    question=question,
                    observations=observations,
                    tools=self.tools,
                    executor=self._execute_tool,
                    planner=self._plan_for_recovery,
                    logger=logger,
                )
                recovery_reports.append(recovery.report)
                if recovery.recovery_success:
                    successful_attempt = next(
                        (
                            attempt
                            for attempt in reversed(recovery.report["attempts"])
                            if attempt.get("recovery_success") is True
                            and isinstance(
                                attempt.get("recovery_execution_result"), dict
                            )
                            and attempt["recovery_execution_result"].get(
                                "detector_failure_detected"
                            )
                            is False
                        ),
                        None,
                    )
                    if successful_attempt is None:
                        recovery.report["recovery_success"] = False
                        recovery.report["final_error"] = (
                            "Recovery result did not pass evidence validation."
                        )
                        observations.append(
                            {
                                "tool": action,
                                "input": arguments,
                                "error": recovery.report["final_error"],
                                "recovery_failed": True,
                            }
                        )
                    else:
                        execution_result = successful_attempt[
                            "recovery_execution_result"
                        ]
                        recovery_step = logger.log_step(
                            thought=(
                                "Use the result of the detector-validated "
                                f"{successful_attempt['strategy']} recovery."
                            ),
                            tool=str(execution_result["tool_name"]),
                            input_data=execution_result["input"],
                            output=execution_result["output"],
                            latency=float(
                                successful_attempt.get("execution_latency", 0.0)
                            ),
                            metadata={
                                "recovered": True,
                                "recovery_success": True,
                                "original_step_id": recovery.report[
                                    "original_step_id"
                                ],
                                "recovery_strategy": successful_attempt["strategy"],
                                "recovery_attempt_number": successful_attempt[
                                    "attempt_number"
                                ],
                                "detector_validation_passed": True,
                                "failure_injected": execution_result.get(
                                    "failure_injected", False
                                ),
                            },
                        )
                        successful_attempt["recovery_trace_step_id"] = recovery_step[
                            "step_id"
                        ]
                        logger.log_recovery_trace_link(
                            {
                                "original_step_id": recovery.report[
                                    "original_step_id"
                                ],
                                "attempt_number": successful_attempt[
                                    "attempt_number"
                                ],
                                "step_id": recovery_step["step_id"],
                                "recovery_success": True,
                                "detector_validation_passed": True,
                            }
                        )
                        observations.append(
                            {
                                "tool": recovery.recovered_tool,
                                "input": recovery.recovered_arguments,
                                "output": recovery.recovered_output,
                                "recovered": True,
                                "step_id": recovery_step["step_id"],
                            }
                        )
                else:
                    observations.append(
                        {
                            "tool": action,
                            "input": arguments,
                            "error": (
                                "Operation could not be completed reliably. "
                                + (recovery.final_error or "No recovery strategy succeeded.")
                            ),
                            "recovery_failed": True,
                        }
                    )
            elif detected_failure:
                failure = next(
                    detection
                    for detection in latest_detections
                    if detection.failure_detected
                )
                observations.append(
                    {
                        "tool": action,
                        "input": arguments,
                        "error": (
                            "Failure detected but no recovery was available. "
                            + failure.explanation
                        ),
                    }
                )
            elif execution.error is not None:
                observations.append(
                    {"tool": action, "input": arguments, "error": execution.error}
                )
            else:
                observations.append(
                    {"tool": action, "input": arguments, "output": execution.output}
                )

        raise RuntimeError(f"Agent exceeded its {self.max_steps}-step limit.")

    def _plan_action(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        """Ask the configured local or remote planner for one next action."""
        excluded = excluded_tools or set()
        if self.client.provider == "local":
            plan_started = time.perf_counter()
            decision = self.local_planner.next_action(
                question, observations, excluded_tools=excluded
            )
            self.client.record_local_call(
                latency_seconds=time.perf_counter() - plan_started
            )
            return decision
        prompt = build_prompt(question, observations, sorted(excluded))
        return parse_decision(self.client.complete(prompt))

    def _plan_for_recovery(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str],
    ) -> dict[str, Any]:
        """Re-plan with the original query and failed tools explicitly excluded."""
        return self._plan_action(question, observations, excluded_tools)

    def _execute_tool(
        self, tool_name: str, tool: ToolFunction, arguments: dict[str, Any]
    ) -> ToolExecution:
        """Run an ordinary or failure-injected tool call for the recovery engine."""
        injector: FailureInjectorProtocol = (
            self.recovery_failure_injector or self.failure_injector
        )
        return injector.execute(tool_name, tool, arguments)

    def _finish(
        self,
        question: str,
        answer: str,
        thought: str,
        logger: TraceLogger,
        recovery_reports: list[dict[str, Any]],
        latency: float,
    ) -> AgentResult:
        """Write final failure, recovery, and evidence analyses."""
        logger.log_step(
            thought=thought,
            tool="final",
            input_data={"question": question},
            output=answer,
            latency=latency,
        )
        trace_entries = logger.read_trace()
        detections = (
            self.detector.detect_trace(trace_entries)
            if self.enable_failure_detection
            else []
        )
        localized = (
            self.localizer.localize(trace_entries, detections)
            if self.enable_localization
            else []
        )
        failure_report = self.localizer.build_report(
            trace_entries, detections, localized
        )
        if self.enable_failure_detection and not self.enable_localization:
            unlocalized_failures = [
                detection.to_dict()
                for detection in detections
                if detection.failure_detected
            ]
            failure_report["failure_detected"] = bool(unlocalized_failures)
            failure_report["total_failures"] = len(unlocalized_failures)
            failure_report["failures"] = unlocalized_failures
        failure_report["trace_file"] = str(logger.path)
        failure_report["analysis_file"] = str(logger.analysis_path)

        attempts = [
            attempt
            for recovery in recovery_reports
            for attempt in recovery["attempts"]
        ]
        strategies = list(dict.fromkeys(attempt["strategy"] for attempt in attempts))
        recovery_report = {
            "recovery_attempted": any(
                recovery["recovery_attempted"] for recovery in recovery_reports
            ),
            "total_detected_failures": failure_report["total_failures"],
            "total_recovery_attempts": len(attempts),
            "successful_recoveries": sum(
                recovery["recovery_success"] for recovery in recovery_reports
            ),
            "failed_recoveries": sum(
                not recovery["recovery_success"] for recovery in recovery_reports
            ),
            "strategies_used": strategies,
            "failures": recovery_reports,
            "recovery_log_file": str(logger.recovery_path),
        }
        recovery_report_path = (
            logger.write_recovery_report(recovery_report)
            if self.enable_recovery
            else None
        )
        if recovery_report_path is not None:
            failure_report["recovery_report_file"] = str(recovery_report_path)
        failure_report_path = (
            logger.write_failure_report(failure_report)
            if self.enable_failure_detection
            else None
        )
        recovery_records = logger.read_recovery_records()
        if self.enable_grounding:
            grounding_report = self.evidence_checker.verify(
                answer,
                trace_entries,
                query=question,
                recovery_report=recovery_report,
                recovery_records=recovery_records,
                failure_report=failure_report,
            )
            grounding_report_path = logger.write_grounding_report(grounding_report)
        else:
            grounding_report = {
                "query": question,
                "final_answer": answer,
                "grounded_answer": answer,
                "status": "DISABLED",
                "claims": [],
                "grounding_score": None,
                "supported_count": 0,
                "unsupported_count": 0,
                "contradicted_count": 0,
                "total_claim_count": 0,
            }
            grounding_report_path = None
        if self.enable_uncertainty:
            uncertainty_signals = self.uncertainty_estimator.estimate(
                grounding_report,
                trace_entries,
                recovery_report=recovery_report,
                recovery_records=recovery_records,
            )
            decision = self.abstention_policy.decide(
                grounding_report,
                uncertainty_signals,
            )
            uncertainty_report = {
                "query": question,
                "original_answer": answer,
                **uncertainty_signals,
                **decision.to_dict(),
                "thresholds": {
                    "answer_threshold": self.abstention_policy.answer_threshold,
                    "abstain_threshold": self.abstention_policy.abstain_threshold,
                },
            }
            uncertainty_report_path = logger.write_uncertainty_report(
                uncertainty_report
            )
            final_decision = decision.final_decision
            final_response = decision.final_response
        else:
            uncertainty_report = {
                "query": question,
                "original_answer": answer,
                "status": "DISABLED",
                "uncertainty_score": None,
                "final_decision": "NOT_APPLICABLE",
                "decision_explanation": "Uncertainty estimation was disabled.",
                "final_response": grounding_report["grounded_answer"],
            }
            uncertainty_report_path = None
            final_decision = "NOT_APPLICABLE"
            final_response = grounding_report["grounded_answer"]
        return AgentResult(
            answer=answer,
            trace=trace_entries,
            log_path=logger.path,
            failure_report=failure_report,
            analysis_path=logger.analysis_path,
            report_path=failure_report_path,
            recovery_report=recovery_report,
            recovery_log_path=logger.recovery_path,
            recovery_report_path=recovery_report_path,
            grounding_report=grounding_report,
            grounding_report_path=grounding_report_path,
            grounded_answer=grounding_report["grounded_answer"],
            uncertainty_report=uncertainty_report,
            uncertainty_report_path=uncertainty_report_path,
            final_decision=final_decision,
            final_response=final_response,
        )