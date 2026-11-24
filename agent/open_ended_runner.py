"""Open-ended execution runner preserving TraceRecover reliability, provenance, and 10-stage events."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from agent.dynamic_planner import DynamicQueryPlanner, QueryAnalysis, sanitize_and_guard_query
from config import LLMClient
from grounding.claim_extractor import ClaimExtractor
from grounding.evidence_checker import EvidenceChecker
from grounding.nli_verifier import DebertaNLIVerifier
from healing.detector import FailureDetector
from healing.localizer import FailureLocalizer
from healing.recovery import RecoveryEngine
from tools.executor import ToolExecutor
from tools.external_search import (
    NO_EXTERNAL_EVIDENCE_PREFIX,
    ExternalSearchAdapter,
    create_external_search_tool,
    is_valid_http_url,
)
from tools.failure_injector import FailureInjector, FailureType, ToolExecution
from trace.trace_logger import TraceLogger
from uncertainty.abstention import AbstentionPolicy
from uncertainty.estimator import UncertaintyEstimator


_PROVIDER_KEY_ENV: dict[str, str | None] = {
    "local": None,
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class ProviderUnavailableError(RuntimeError):
    """Raised when an explicitly requested live provider cannot serve the request."""


class QueryCancelledError(RuntimeError):
    """Raised when an active open-ended query is cancelled via cancel controls."""


class _QueryCancelRegistry:
    """Thread-safe active query cancellation registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def register(self, query_id: str) -> threading.Event:
        with self._lock:
            ev = threading.Event()
            self._events[query_id] = ev
            return ev

    def cancel(self, query_id: str) -> bool:
        with self._lock:
            ev = self._events.get(query_id)
            if ev is None:
                return False
            ev.set()
            return True

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._events.pop(query_id, None)


QUERY_CANCEL_REGISTRY = _QueryCancelRegistry()


def _extract_verified_external_sources(
    trace_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collect verified external source titles and HTTP(S) URLs strictly from committed steps."""
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for step in trace_entries:
        if step.get("tool") != "external_search":
            continue
        if step.get("output_state") == "discarded" or step.get("error") is not None:
            continue
        if step.get("failure_injected") is True:
            continue
        output = step.get("output")
        if not isinstance(output, Mapping):
            continue
        results = output.get("results", [])
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, Mapping):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            provider = str(item.get("provider", output.get("provider", "external"))).strip()
            if title and is_valid_http_url(url) and url not in seen_urls:
                seen_urls.add(url)
                sources.append(
                    {
                        "step_id": step.get("step_id"),
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "provider": provider,
                    }
                )
    return sources


def _check_provider_credentials(provider: str, client: LLMClient) -> None:
    if provider == "local":
        return
    env_var = _PROVIDER_KEY_ENV.get(provider)
    if env_var and not os.getenv(env_var, "").strip():
        raise ProviderUnavailableError(
            f"Provider '{provider}' requires server-side credential {env_var}, which is not configured. "
            "Configure the credential in the server environment or select 'local' offline mode."
        )


def _format_step_provenance(trace_entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    committed_steps: list[int | str] = []
    discarded_steps: list[int | str] = []
    recovered_steps: list[int | str] = []
    tools_used: list[str] = []
    for step in trace_entries:
        tool = str(step.get("tool", ""))
        sid = step.get("step_id")
        if tool in {"final", "llm"}:
            continue
        if tool and tool not in tools_used:
            tools_used.append(tool)
        if step.get("recovered") is True or step.get("recovery_success") is True:
            if sid is not None:
                recovered_steps.append(sid)
        is_discarded = (
            step.get("output_state") == "discarded"
            or step.get("error") is not None
            or step.get("failure_injected") is True
        )
        if is_discarded:
            if sid is not None:
                discarded_steps.append(sid)
        elif sid is not None:
            committed_steps.append(sid)
    return {
        "committed_steps": committed_steps,
        "discarded_steps": discarded_steps,
        "recovered_steps": recovered_steps,
        "tools_used": tools_used,
    }


class OpenEndedAgentRunner:
    """Execute arbitrary user or unseen queries through dynamic planning and TraceRecover verification."""

    def __init__(
        self,
        *,
        planner: DynamicQueryPlanner | None = None,
        search_adapter: ExternalSearchAdapter | None = None,
        log_directory: Path | str = "logs",
    ) -> None:
        self.planner = planner or DynamicQueryPlanner()
        self.search_adapter = search_adapter
        self.log_directory = Path(log_directory)

    def run(
        self,
        question: str,
        *,
        provider: str = "local",
        model: str | None = None,
        client: LLMClient | None = None,
        conversation_id: str | None = None,
        query_id: str | None = None,
        conversation_history: Sequence[Mapping[str, Any]] = (),
        enable_external_search: bool = False,
        search_provider: str = "auto",
        failure_rate: float = 0.0,
        seed: int = 42,
        enabled_failure_types: Sequence[str] | None = None,
        use_nli: bool = False,
        enable_recovery: bool = True,
        enable_grounding: bool = True,
        enable_uncertainty: bool = True,
        answer_threshold: float = 0.15,
        abstain_threshold: float = 0.55,
        max_steps: int = 6,
        max_llm_calls: int = 6,
        max_cost_usd: float = 0.25,
        recovery_failure_injector: Any | None = None,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run one open-ended query, emitting real 10-stage events and writing a replayable trace."""
        started_perf = time.perf_counter()
        active_qid = query_id or f"qry-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
        events: list[dict[str, Any]] = []

        def emit(stage: str, title: str, status: str, data: Mapping[str, Any]) -> None:
            event = {
                "stage": stage,
                "stage_number": len(events) + 1 if stage not in {e["stage"] for e in events} else next(
                    e["stage_number"] for e in events if e["stage"] == stage
                ),
                "title": title,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.perf_counter() - started_perf) * 1000.0, 2),
                "data": dict(data),
            }
            events.append(event)
            if on_event is not None:
                on_event(event)

        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                emit("cancelled", "Query Cancelled", "cancelled", {"query_id": active_qid})
                raise QueryCancelledError(f"Query {active_qid} was cancelled.")

        guard = sanitize_and_guard_query(question)
        cleaned_question = guard["cleaned_query"]

        # Stage 1: Query Received
        emit(
            "query_received",
            "1. Query Received",
            "ok",
            {
                "query_id": active_qid,
                "conversation_id": conversation_id,
                "question": cleaned_question,
                "provider_requested": provider,
                "enable_external_search": enable_external_search,
                "history_turns_loaded": len(conversation_history),
            },
        )
        check_cancelled()

        active_client = client or LLMClient(provider=provider, model=model)
        if client is None:
            _check_provider_credentials(active_client.provider, active_client)

        # Configure tool executor with safe registry + optional external_search
        tool_executor = ToolExecutor.with_default_tools(enable_cache=(failure_rate == 0.0))
        if enable_external_search:
            adapter = self.search_adapter or ExternalSearchAdapter(provider=search_provider)
            tool_executor.register(
                "external_search",
                create_external_search_tool(adapter, context_messages=conversation_history),
                required_fields={"query": str},
                description=(
                    "Retrieve real-time external search results with verified titles, snippets, and URLs. "
                    "Input: {query: string}."
                ),
            )

        # Stage 2: Intent Understood
        analysis: QueryAnalysis = self.planner.analyze_query(
            cleaned_question,
            conversation_history=conversation_history,
            available_tools=tool_executor.list_tools(),
            provider=active_client.provider,
            enable_external_search=enable_external_search,
        )
        emit("intent_understood", "2. Intent Understood", "blocked" if analysis.prompt_injection_flagged else "ok", analysis.to_dict())
        check_cancelled()

        # Build failure injector, detector, localizer, recovery, grounding & uncertainty stack
        parsed_failure_types: list[FailureType] | None = None
        if enabled_failure_types is not None:
            parsed_failure_types = [FailureType(item.strip().lower()) for item in enabled_failure_types]
        injector = FailureInjector(
            failure_rate=float(failure_rate),
            seed=int(seed),
            enabled=float(failure_rate) > 0.0,
            enabled_types=parsed_failure_types,
        )
        detector = FailureDetector()
        localizer = FailureLocalizer(detector)
        recovery_engine = RecoveryEngine(detector)
        nli_verifier = DebertaNLIVerifier(local_files_only=True, allow_fallback=True) if use_nli else None
        evidence_checker = EvidenceChecker(
            ClaimExtractor(), detector, use_nli=use_nli, nli_verifier=nli_verifier
        )
        uncertainty_estimator = UncertaintyEstimator(detector=detector)
        abstention_policy = AbstentionPolicy(
            answer_threshold=answer_threshold,
            abstain_threshold=abstain_threshold,
            require_grounding=enable_grounding,
        )

        logger = TraceLogger(self.log_directory)
        observations: list[dict[str, Any]] = []
        recovery_reports: list[dict[str, Any]] = []
        planned_actions: list[dict[str, Any]] = []
        selected_tools: list[str] = []
        final_decision_payload: dict[str, Any] | None = None

        tool_map = tool_executor.as_tool_map()

        def _recovery_exec(tool_name: str, tool_fn: Any, args: dict[str, Any]) -> ToolExecution:
            rec_inj = recovery_failure_injector if recovery_failure_injector is not None else injector
            return rec_inj.execute(tool_name, tool_fn, args)

        def _recovery_replan(q: str, obs: list[dict[str, Any]], excluded: set[str]) -> dict[str, Any]:
            return self.planner.next_action(
                q,
                obs,
                analysis=analysis,
                client=active_client,
                tool_executor=tool_executor,
                conversation_history=conversation_history,
                excluded_tools=excluded,
                max_llm_calls=max_llm_calls,
                max_cost_usd=max_cost_usd,
            )

        for step_num in range(1, max_steps + 1):
            check_cancelled()
            try:
                decision = self.planner.next_action(
                    cleaned_question,
                    observations,
                    analysis=analysis,
                    client=active_client,
                    tool_executor=tool_executor,
                    conversation_history=conversation_history,
                    max_llm_calls=max_llm_calls,
                    max_cost_usd=max_cost_usd,
                )
            except (RuntimeError, ValueError, TimeoutError, OSError) as exc:
                logger.log_step(
                    thought="Request next dynamic plan step.",
                    tool="llm",
                    input_data={"question": cleaned_question, "step": step_num},
                    error=str(exc),
                    latency=time.perf_counter() - started_perf,
                )
                if active_client.provider != "local":
                    raise ProviderUnavailableError(
                        f"Configured provider '{active_client.provider}' failed during planning: {exc}"
                    ) from exc
                raise

            action = str(decision["action"])
            thought = str(decision.get("thought", ""))
            arguments = dict(decision.get("arguments", {}))
            planned_actions.append({"step": step_num, "action": action, "arguments": arguments, "thought": thought})

            # Emit Stage 3 & 4 when first plan is produced or updated
            emit(
                "plan_generated",
                "3. Plan Generated",
                "ok",
                {
                    "step": step_num,
                    "action": action,
                    "thought": thought,
                    "is_multi_step": analysis.is_multi_step,
                    "provider": active_client.provider,
                },
            )

            if action == "final":
                if not selected_tools:
                    emit(
                        "tools_selected",
                        "4. Tools Selected",
                        "skipped",
                        {
                            "tools": [],
                            "reason": "Direct response without tool execution."
                            if decision.get("direct_llm_no_tools")
                            else "Blocked or insufficient parameters.",
                        },
                    )
                final_decision_payload = decision
                break

            # Allowlist & argument validation BEFORE execution
            valid, val_err = self.planner.validate_planned_action(action, arguments, tool_executor)
            if not valid:
                raise ValueError(f"Blocked invalid tool call {action!r}: {val_err}")

            selected_tools.append(action)
            emit(
                "tools_selected",
                "4. Tools Selected",
                "ok",
                {
                    "step": step_num,
                    "tool": action,
                    "arguments": arguments,
                    "allowlist_validated": True,
                },
            )
            check_cancelled()

            # Stage 5: Tool Execution (via ToolExecutor + FailureInjector)
            outcome = tool_executor.execute(
                action,
                arguments,
                failure_injector=injector,
            )
            execution = outcome.execution
            failure_meta = execution.trace_metadata(action) if execution.failure_injected else None
            tool_step = logger.log_step(
                thought=thought,
                tool=action,
                input_data=arguments,
                output=None if execution.error is not None else execution.output,
                error=execution.error,
                latency=outcome.latency,
                failure_metadata=failure_meta,
                metadata={"cache_hit": outcome.cache_hit},
                output_state=outcome.output_state,
            )
            emit(
                "tool_execution",
                "5. Tool Execution",
                "failed" if execution.error or execution.failure_injected else "ok",
                {
                    "step_id": tool_step["step_id"],
                    "tool": action,
                    "arguments": arguments,
                    "output": execution.output if execution.error is None else None,
                    "error": execution.error,
                    "output_state": outcome.output_state,
                    "cache_hit": outcome.cache_hit,
                    "failure_injected": execution.failure_injected,
                    "latency_seconds": round(outcome.latency, 6),
                },
            )

            # Stage 6: Failure Detection & Localization
            trace_entries = logger.read_trace()
            detections = detector.detect_trace(trace_entries)
            latest_detections = [d for d in detections if d.step_id == tool_step["step_id"]]
            localized = localizer.localize(trace_entries, latest_detections)
            for det in latest_detections:
                logger.log_analysis(det.to_dict(), localized)
            detected_failure = any(d.failure_detected for d in latest_detections)
            emit(
                "failure_detection",
                "6. Failure Detection",
                "warning" if detected_failure else "ok",
                {
                    "step_id": tool_step["step_id"],
                    "failure_detected": detected_failure,
                    "detections": [d.to_dict() for d in latest_detections],
                    "localized": localized,
                },
            )

            # Stage 7: Recovery
            if localized and enable_recovery:
                recovery = recovery_engine.recover(
                    failure=localized[0],
                    question=cleaned_question,
                    observations=observations,
                    tools=tool_map,
                    executor=_recovery_exec,
                    planner=_recovery_replan,
                    logger=logger,
                )
                recovery_reports.append(recovery.report)
                if recovery.recovery_success:
                    successful_attempt = next(
                        (
                            att
                            for att in reversed(recovery.report["attempts"])
                            if att.get("recovery_success") is True
                            and isinstance(att.get("recovery_execution_result"), dict)
                            and att["recovery_execution_result"].get("detector_failure_detected") is False
                        ),
                        None,
                    )
                    if successful_attempt is not None:
                        exec_res = successful_attempt["recovery_execution_result"]
                        rec_step = logger.log_step(
                            thought=f"Detector-validated {successful_attempt['strategy']} recovery.",
                            tool=str(exec_res["tool_name"]),
                            input_data=exec_res["input"],
                            output=exec_res["output"],
                            latency=float(successful_attempt.get("execution_latency", 0.0)),
                            metadata={
                                "recovered": True,
                                "recovery_success": True,
                                "original_step_id": recovery.report["original_step_id"],
                                "recovery_strategy": successful_attempt["strategy"],
                                "recovery_attempt_number": successful_attempt["attempt_number"],
                                "detector_validation_passed": True,
                                "failure_injected": exec_res.get("failure_injected", False),
                            },
                            output_state="committed",
                        )
                        successful_attempt["recovery_trace_step_id"] = rec_step["step_id"]
                        logger.log_recovery_trace_link(
                            {
                                "original_step_id": recovery.report["original_step_id"],
                                "attempt_number": successful_attempt["attempt_number"],
                                "step_id": rec_step["step_id"],
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
                                "step_id": rec_step["step_id"],
                            }
                        )
                    else:
                        recovery.report["recovery_success"] = False
                        recovery.report["final_error"] = "Recovery result failed detector validation."
                        observations.append(
                            {
                                "tool": action,
                                "input": arguments,
                                "error": recovery.report["final_error"],
                                "recovery_failed": True,
                            }
                        )
                else:
                    observations.append(
                        {
                            "tool": action,
                            "input": arguments,
                            "error": recovery.final_error or "Recovery exhausted.",
                            "recovery_failed": True,
                        }
                    )
                emit(
                    "recovery",
                    "7. Recovery",
                    "ok" if recovery.recovery_success else "failed",
                    {
                        "attempted": True,
                        "recovery_success": recovery.recovery_success,
                        "attempts": recovery.report.get("attempts", []),
                        "recovered_tool": recovery.recovered_tool,
                        "final_error": recovery.final_error,
                    },
                )
            else:
                emit(
                    "recovery",
                    "7. Recovery",
                    "skipped",
                    {"attempted": False, "reason": "No failure detected." if not detected_failure else "Recovery disabled."},
                )
                if detected_failure:
                    fail_obj = next(d for d in latest_detections if d.failure_detected)
                    observations.append({"tool": action, "input": arguments, "error": fail_obj.explanation})
                elif execution.error is not None:
                    observations.append({"tool": action, "input": arguments, "error": execution.error})
                else:
                    observations.append({"tool": action, "input": arguments, "output": execution.output})

        if final_decision_payload is None:
            if observations and any("output" in o and not o.get("error") for o in observations):
                synth = "\n".join(str(o["output"]) for o in observations if "output" in o and not o.get("error"))
                final_decision_payload = {
                    "thought": "Max tool steps reached; synthesize committed tool observations.",
                    "action": "final",
                    "final_answer": synth,
                }
            else:
                final_decision_payload = {
                    "thought": "Max steps exhausted without valid committed observations.",
                    "action": "final",
                    "final_answer": "The agent could not complete all steps within the configured step budget.",
                }

        raw_answer = str(final_decision_payload.get("final_answer", ""))
        blocked_by_guard = bool(final_decision_payload.get("blocked_by_guard") or analysis.prompt_injection_flagged)
        direct_llm_no_tools = bool(final_decision_payload.get("direct_llm_no_tools")) and not selected_tools
        offline_scope_limited = bool(final_decision_payload.get("offline_scope_limited"))

        if direct_llm_no_tools:
            logger.log_step(
                thought=str(final_decision_payload.get("thought", "")),
                tool="llm",
                input_data={"question": cleaned_question, "provider": active_client.provider},
                output=raw_answer,
                latency=time.perf_counter() - started_perf,
                metadata={"knowledge_source": "model_generated_no_tools"},
            )

        logger.log_step(
            thought=str(final_decision_payload.get("thought", "")),
            tool="final",
            input_data={"question": cleaned_question},
            output=raw_answer,
            latency=time.perf_counter() - started_perf,
        )

        # Finalize Failure & Recovery reports on disk for Replay & Evidence Inspector
        trace_entries = logger.read_trace()
        all_detections = detector.detect_trace(trace_entries)
        all_localized = localizer.localize(trace_entries, all_detections)
        failure_report = localizer.build_report(trace_entries, all_detections, all_localized)
        failure_report["trace_file"] = str(logger.path)
        failure_report["analysis_file"] = str(logger.analysis_path)

        attempts = [att for rep in recovery_reports for att in rep.get("attempts", [])]
        recovery_report = {
            "recovery_attempted": any(rep.get("recovery_attempted") for rep in recovery_reports),
            "total_detected_failures": failure_report["total_failures"],
            "total_recovery_attempts": len(attempts),
            "successful_recoveries": sum(bool(rep.get("recovery_success")) for rep in recovery_reports),
            "failed_recoveries": sum(not rep.get("recovery_success") for rep in recovery_reports),
            "strategies_used": list(dict.fromkeys(str(att.get("strategy")) for att in attempts if att.get("strategy"))),
            "failures": recovery_reports,
            "recovery_log_file": str(logger.recovery_path),
        }
        recovery_report_path = logger.write_recovery_report(recovery_report) if enable_recovery else None
        if recovery_report_path is not None:
            failure_report["recovery_report_file"] = str(recovery_report_path)
        failure_report_path = logger.write_failure_report(failure_report)
        recovery_records = logger.read_recovery_records()

        # Check if committed external/local searches returned only negative-evidence markers
        no_evidence_only = bool(selected_tools) and all(
            (
                str(o.get("output", "")).strip().casefold().startswith("no local evidence found for:")
                or str(o.get("output", "")).strip().casefold().startswith(NO_EXTERNAL_EVIDENCE_PREFIX.casefold())
                or bool(o.get("error"))
            )
            for o in observations
        )

        # Ensure stages 5-7 exist on timeline even when direct response / safety guard skipped tools
        seen_stages = {e["stage"] for e in events}
        if "tool_execution" not in seen_stages:
            emit("tool_execution", "5. Tool Execution", "skipped", {"reason": "No tools were invoked."})
        if "failure_detection" not in seen_stages:
            emit("failure_detection", "6. Failure Detection", "ok", {"failure_detected": False, "detections": []})
        if "recovery" not in seen_stages:
            emit("recovery", "7. Recovery", "skipped", {"attempted": False, "reason": "No tool failure occurred."})

        # Stage 8: Evidence Verification
        external_sources = _extract_verified_external_sources(trace_entries)
        if enable_grounding:
            grounding_report = evidence_checker.verify(
                raw_answer,
                trace_entries,
                query=cleaned_question,
                recovery_report=recovery_report,
                recovery_records=recovery_records,
                failure_report=failure_report,
            )
            # Clear distinction: model-generated without tools vs external retrieval vs local tools
            if blocked_by_guard or offline_scope_limited or no_evidence_only:
                knowledge_source = "blocked_safety_guard" if blocked_by_guard else (
                    "offline_scope_limited" if offline_scope_limited else "abstained_insufficient_evidence"
                )
                grounding_report["grounding_score"] = 0.0
                grounding_report["supported_count"] = 0
                grounding_report["unsupported_count"] = max(1, int(grounding_report.get("total_claim_count") or 0))
            elif direct_llm_no_tools:
                knowledge_source = "model_generated_no_tools"
            elif external_sources:
                knowledge_source = "external_search_grounded"
            elif grounding_report.get("supported_count", 0) > 0:
                knowledge_source = "tool_grounded"
            else:
                knowledge_source = "abstained_insufficient_evidence"
            grounding_report["knowledge_source"] = knowledge_source
            grounding_report["external_sources"] = external_sources
            grounding_report_path = logger.write_grounding_report(grounding_report)
        else:
            knowledge_source = "model_generated_no_tools" if direct_llm_no_tools else (
                "external_search_grounded" if external_sources else "grounding_disabled"
            )
            grounding_report = {
                "query": cleaned_question,
                "final_answer": raw_answer,
                "grounded_answer": raw_answer,
                "status": "DISABLED",
                "claims": [],
                "grounding_score": None,
                "supported_count": 0,
                "unsupported_count": 0,
                "contradicted_count": 0,
                "total_claim_count": 0,
                "knowledge_source": knowledge_source,
                "external_sources": external_sources,
            }
            grounding_report_path = None

        emit(
            "evidence_verification",
            "8. Evidence Verification",
            "ok" if knowledge_source in {"tool_grounded", "external_search_grounded"} else "warning",
            {
                "knowledge_source": knowledge_source,
                "grounding_score": grounding_report.get("grounding_score"),
                "supported_count": grounding_report.get("supported_count", 0),
                "unsupported_count": grounding_report.get("unsupported_count", 0),
                "contradicted_count": grounding_report.get("contradicted_count", 0),
                "external_sources": external_sources,
                "claims": grounding_report.get("claims", []),
            },
        )

        # Stage 9: Uncertainty Estimation
        if enable_uncertainty:
            uncertainty_signals = uncertainty_estimator.estimate(
                grounding_report,
                trace_entries,
                recovery_report=recovery_report,
                recovery_records=recovery_records,
            )
            if direct_llm_no_tools and not blocked_by_guard:
                # Transparent WARN decision for model-generated no-tool answers (never pretend tool verification)
                final_decision = "WARN"
                decision_explanation = (
                    "Model-generated response without external/local tool verification; "
                    "review before relying on unverified factual claims."
                )
                final_response = (
                    f"[Model-Generated Response · {active_client.provider} / {active_client.model} · No Tool Evidence Used]\n\n"
                    f"{raw_answer}"
                )
            elif blocked_by_guard:
                final_decision = "ABSTAIN"
                decision_explanation = analysis.notes or "Blocked by safety/injection policy."
                final_response = f"I am abstaining: {decision_explanation}"
            else:
                decision_obj = abstention_policy.decide(grounding_report, uncertainty_signals)
                final_decision = decision_obj.final_decision
                decision_explanation = decision_obj.decision_explanation
                final_response = decision_obj.final_response
            uncertainty_report = {
                "query": cleaned_question,
                "original_answer": raw_answer,
                **uncertainty_signals,
                "final_decision": final_decision,
                "decision_explanation": decision_explanation,
                "final_response": final_response,
                "thresholds": {
                    "answer_threshold": answer_threshold,
                    "abstain_threshold": abstain_threshold,
                },
            }
            uncertainty_report_path = logger.write_uncertainty_report(uncertainty_report)
        else:
            final_decision = "ABSTAIN" if blocked_by_guard else "NOT_APPLICABLE"
            decision_explanation = (
                analysis.notes
                if blocked_by_guard
                else "Uncertainty estimation was disabled."
            )
            final_response = (
                f"I am abstaining: {decision_explanation}" if blocked_by_guard else grounding_report["grounded_answer"]
            )
            uncertainty_report = {
                "query": cleaned_question,
                "original_answer": raw_answer,
                "status": "DISABLED",
                "uncertainty_score": None,
                "final_decision": final_decision,
                "decision_explanation": decision_explanation,
                "final_response": final_response,
            }
            uncertainty_report_path = None

        # Append verified external source links strictly from retrieved committed results
        if external_sources and final_decision != "ABSTAIN":
            source_lines = "\n".join(f"- [{s['title']}]({s['url']})" for s in external_sources)
            final_response = f"{final_response}\n\nVerified External Sources:\n{source_lines}"

        emit(
            "uncertainty_estimation",
            "9. Uncertainty Estimation",
            "ok",
            {
                "uncertainty_score": uncertainty_report.get("uncertainty_score"),
                "tool_success_ratio": uncertainty_report.get("tool_success_ratio"),
                "recovery_reliability": uncertainty_report.get("recovery_reliability"),
            },
        )

        # Stage 10: Final Decision
        emit(
            "final_decision",
            "10. Final Decision",
            "ok" if final_decision == "ANSWER" else ("warning" if final_decision == "WARN" else "abstained"),
            {
                "final_decision": final_decision,
                "knowledge_source": knowledge_source,
                "decision_explanation": decision_explanation,
                "final_response": final_response,
            },
        )

        elapsed_total = round(time.perf_counter() - started_perf, 6)
        provenance = _format_step_provenance(trace_entries)
        llm_usage = active_client.usage_summary()
        return {
            "query_id": active_qid,
            "conversation_id": conversation_id,
            "trace_id": logger.path.stem,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": cleaned_question,
            "resolved_query": analysis.resolved_query,
            "provider": active_client.provider,
            "provider_scope": "offline_deterministic_local" if active_client.provider == "local" else "live_remote_llm",
            "model": active_client.model,
            "knowledge_source": knowledge_source,
            "external_sources": external_sources,
            "analysis": analysis.to_dict(),
            "planned_actions": planned_actions,
            "tools_selected": provenance["tools_used"],
            "answer": raw_answer,
            "grounded_answer": grounding_report["grounded_answer"],
            "final_decision": final_decision,
            "decision_explanation": decision_explanation,
            "final_response": final_response,
            "provenance": provenance,
            "cache_stats": tool_executor.cache_stats(),
            "llm_usage": llm_usage,
            "elapsed_seconds": elapsed_total,
            "failure_report": failure_report,
            "recovery_report": recovery_report,
            "grounding_report": grounding_report,
            "uncertainty_report": uncertainty_report,
            "execution_events": events,
            "trace_steps": trace_entries,
            "artifacts": {
                "trace_path": str(logger.path),
                "analysis_path": str(logger.analysis_path),
                "failure_report_path": str(failure_report_path) if failure_report_path else None,
                "recovery_report_path": str(recovery_report_path) if recovery_report_path else None,
                "grounding_report_path": str(grounding_report_path) if grounding_report_path else None,
                "uncertainty_report_path": str(uncertainty_report_path) if uncertainty_report_path else None,
            },
        }


__all__ = [
    "OpenEndedAgentRunner",
    "ProviderUnavailableError",
    "QUERY_CANCEL_REGISTRY",
    "QueryCancelledError",
]
