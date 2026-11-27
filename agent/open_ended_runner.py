"""Open-ended execution runner preserving TraceRecover reliability, provenance, and 10-stage events."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

OBSERVABILITY_LOGGER = logging.getLogger("tracerecover.observability")


def _mask_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:10]
    return f"sess_{digest}"


def _emit_structured_log(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a consistent structured JSON log line with secrets scrubbed and zero raw credentials."""
    safe_fields: dict[str, Any] = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for k, v in fields.items():
        if isinstance(v, str):
            safe_fields[k] = redact_secret_text(v)
        else:
            safe_fields[k] = v
    try:
        OBSERVABILITY_LOGGER.log(level, json.dumps(safe_fields, ensure_ascii=False, default=str))
    except Exception:
        pass

from agent.dynamic_planner import DynamicQueryPlanner, QueryAnalysis, sanitize_and_guard_query
from agent.provider_adapter import (
    LLMProviderAdapter,
    ProviderAdapterError,
    classify_provider_exception,
    redact_secret_text,
)
from agent.synthesizer import EvidenceGroundedSynthesizer, SynthesisFailureError
from agent.uncertainty_taxonomy import classify_abstention_and_uncertainty
from config import LLMClient
from connectors.sources.verifier import ClaimEvidenceVerifier, create_llm_entailment_evaluator
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


_QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "between", "by", "can", "compare", "compared",
    "did", "differ", "difference", "do", "does", "during", "explain", "for", "from", "how",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "under",
    "use", "uses", "using", "versus", "vs", "was", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "without", "mode", "form", "reduce", "handle", "handling",
    "convert", "encode", "predict", "eliminate", "approximate", "approximately", "according",
}
_FUTURE_TENSE_RE = re.compile(r"\b(?:will\s+(?:win|release|achieve|assign|record|ship|launch|publish|announce)|next\s+year|upcoming\s+\d{4})\b", re.I)
_FUTURE_YEAR_RE = re.compile(r"\b(20(?:2[89]|[3-9]\d))\b")


def _compact_external_query(query: str) -> str:
    """Distill a verbose natural-language question into concise high-signal search keywords."""
    raw_tokens = re.findall(r"[A-Za-z0-9_+./-]{2,}", query or "")
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for idx, tok in enumerate(raw_tokens):
        low = tok.lower().strip(".,?!:;()")
        if not low or low in _QUERY_STOPWORDS or low in seen:
            continue
        seen.add(low)
        # Prioritize acronyms, proper nouns, hyphenated/underscore technical identifiers, and version/RFC tokens
        priority = 0
        if any(c.isupper() for c in tok[1:]) or tok.isupper():
            priority += 4
        elif tok[0].isupper() and idx > 0:
            priority += 3
        if any(ch in tok for ch in ("_", "-", "/", ".")) or any(ch.isdigit() for ch in tok):
            priority += 3
        if len(tok) >= 6:
            priority += 1
        scored.append((priority, idx, tok.strip(".,?!:;()")))
    scored.sort(key=lambda item: (-item[0], item[1]))
    top = sorted(scored[:5], key=lambda item: item[1])
    return " ".join(tok for _p, _i, tok in top).strip()


def _filter_and_clean_external_output(query: str, output: Any) -> Any:
    """Unescape HTML entities, prune clearly off-topic hits, and enforce honest future-year abstention."""
    if not isinstance(output, Mapping):
        return output
    results = output.get("results")
    if not isinstance(results, list) or not results:
        return output

    cleaned_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        c_item = dict(item)
        if isinstance(c_item.get("title"), str):
            c_item["title"] = html.unescape(c_item["title"])
        if isinstance(c_item.get("snippet"), str):
            c_item["snippet"] = html.unescape(c_item["snippet"])
        cleaned_results.append(c_item)

    # Future-tense queries naming an explicit future year (>=2028) require evidence referencing that year
    future_years = _FUTURE_YEAR_RE.findall(query or "")
    if future_years and _FUTURE_TENSE_RE.search(query or ""):
        year_matched = [
            r for r in cleaned_results
            if any(y in f"{r.get('title', '')} {r.get('snippet', '')}" for y in future_years)
        ]
        if not year_matched:
            return f"{NO_EXTERNAL_EVIDENCE_PREFIX} {query}"
        cleaned_results = year_matched

    # Score query-to-document lexical relevance to drop unrelated search noise (only when >1 doc returned)
    q_toks = {
        t.lower() for t in re.findall(r"[A-Za-z0-9_+-]{3,}", query or "")
        if t.lower() not in _QUERY_STOPWORDS
    }
    if q_toks and len(cleaned_results) > 1:
        kept: list[tuple[float, dict[str, Any]]] = []
        for r in cleaned_results:
            blob = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
            d_toks = set(re.findall(r"[A-Za-z0-9_+-]{3,}", blob))
            ov = len(q_toks & d_toks) / max(len(q_toks), 1)
            if ov >= 0.14:
                kept.append((ov, r))
        if kept:
            cleaned_results = [r for _ov, r in kept]

    # Reconstruct clean summary & sources matching filtered/unescaped results
    parts: list[str] = []
    for idx, item in enumerate(cleaned_results, start=1):
        snip = str(item.get("snippet") or "").strip()
        if snip and snip[-1] not in ".!?":
            snip += "."
        parts.append(f"[{idx}] {item.get('title', '')} ({item.get('url', '')}): {snip}")
    updated = dict(output)
    updated["results"] = cleaned_results
    updated["result_count"] = len(cleaned_results)
    updated["sources"] = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "provider": r.get("provider", "")}
        for r in cleaned_results
    ]
    updated["summary"] = "\n".join(parts)
    return updated


def _wrap_external_search_with_hygiene(
    base_tool: Callable[[dict[str, Any]], dict[str, Any] | str],
    *,
    allow_compact_retry: bool = True,
) -> Callable[[dict[str, Any]], dict[str, Any] | str]:
    """Wrap external_search tool with HTML entity unescaping, noise pruning, and compact-query fallback."""
    def _run(arguments: dict[str, Any]) -> dict[str, Any] | str:
        query = str(arguments.get("query") or "").strip()
        out = base_tool(arguments)
        is_empty = isinstance(out, str) and out.strip().casefold().startswith(NO_EXTERNAL_EVIDENCE_PREFIX.casefold())
        if is_empty and allow_compact_retry and len(query.split()) >= 7 and not (_FUTURE_YEAR_RE.search(query) and _FUTURE_TENSE_RE.search(query)):
            compact_q = _compact_external_query(query)
            if compact_q and len(compact_q.split()) < len(query.split()):
                retry_args = dict(arguments)
                retry_args["query"] = compact_q
                retry_out = base_tool(retry_args)
                if not (isinstance(retry_out, str) and retry_out.strip().casefold().startswith(NO_EXTERNAL_EVIDENCE_PREFIX.casefold())):
                    out = retry_out
        return _filter_and_clean_external_output(query, out)

    return _run


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
        if step.get("output_state") == "discarded" or step.get("error") is not None:
            continue
        if step.get("failure_injected") is True:
            continue
        output = step.get("output")
        if not isinstance(output, Mapping):
            continue
        results = output.get("results") or output.get("documents") or []
        if not isinstance(results, list):
            final_url = output.get("final_url")
            if isinstance(final_url, str) and is_valid_http_url(final_url) and final_url not in seen_urls:
                seen_urls.add(final_url)
                sources.append(
                    {
                        "title": str(output.get("title") or final_url),
                        "url": final_url,
                        "snippet": str(output.get("content_excerpt") or "")[:300],
                        "provider": f"browser:{output.get('engine', 'chromium')}",
                        "trust_tier": "tier3",
                    }
                )
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


def _check_provider_credentials(provider: str, client: Any) -> None:
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
        connector_manager: Any | None = None,
        log_directory: Path | str = "logs",
    ) -> None:
        self.planner = planner or DynamicQueryPlanner()
        self.search_adapter = search_adapter
        self.connector_manager = connector_manager
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
        use_connectors: bool = False,
        connector_manager: Any | None = None,
        approved_connector_actions: Sequence[str] = (),
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
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one open-ended query, emitting real 10-stage events and writing a replayable trace."""
        started_perf = time.perf_counter()
        started_at_iso = datetime.now(timezone.utc).isoformat()
        stage_timings_ms: dict[str, float] = {
            "intent_and_planning_ms": 0.0,
            "tool_and_connector_ms": 0.0,
            "failure_detection_and_recovery_ms": 0.0,
            "synthesis_ms": 0.0,
            "evidence_verification_ms": 0.0,
            "claim_verification_ms": 0.0,
            "uncertainty_ms": 0.0,
        }
        active_qid = query_id or f"qry-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
        masked_sid = _mask_session_id(session_id)
        events: list[dict[str, Any]] = []
        _emit_structured_log(
            "query.started",
            query_id=active_qid,
            session_ref=masked_sid,
            requested_provider=provider,
            enable_external_search=enable_external_search,
            use_connectors=use_connectors,
        )

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
                _emit_structured_log(
                    "query.cancelled",
                    level=logging.WARNING,
                    query_id=active_qid,
                    session_ref=masked_sid,
                    elapsed_ms=round((time.perf_counter() - started_perf) * 1000.0, 2),
                )
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

        provider_adapter = LLMProviderAdapter(
            provider=provider,
            model=model,
            client=client,
            max_llm_calls=max_llm_calls,
            max_cost_usd=max_cost_usd,
        )
        try:
            if client is None:
                provider_adapter.check_credentials()
        except ProviderAdapterError as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        active_client: Any = provider_adapter if client is None else client

        # Configure tool executor with safe registry + optional external_search & connectors
        tool_executor = ToolExecutor.with_default_tools(enable_cache=(failure_rate == 0.0))
        connector_tools_registered: list[str] = []
        if enable_external_search:
            adapter = self.search_adapter or ExternalSearchAdapter(provider=search_provider)
            base_ext_tool = create_external_search_tool(adapter, context_messages=conversation_history)
            is_mock_ext = getattr(adapter, "_provider_fn", None) is not None
            tool_executor.register(
                "external_search",
                _wrap_external_search_with_hygiene(base_ext_tool, allow_compact_retry=not is_mock_ext),
                required_fields={"query": str},
                description=(
                    "Retrieve real-time external search results with verified titles, snippets, and URLs. "
                    "Input: {query: string}."
                ),
            )
        active_conn_mgr = connector_manager or self.connector_manager
        if use_connectors and active_conn_mgr is not None:
            connector_tools_registered = active_conn_mgr.register_agent_tools(
                tool_executor,
                approved_actions=set(approved_connector_actions or ()),
            )

            def _research_sources_tool(arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
                args = dict(arguments) if isinstance(arguments, dict) else {}
                args.update(kwargs)
                return active_conn_mgr.query_research_sources(
                    query=str(args.get("query") or ""),
                    provider_ids=args.get("provider_ids"),
                )

            tool_executor.register(
                "research_sources",
                _research_sources_tool,
                required_fields={"query": str},
                description="Query trust-tiered scholarly & reference sources (arXiv, Crossref, OpenAlex, Wikipedia).",
            )
            connector_tools_registered.append("research_sources")

        # Stage 2: Intent Understood
        _t_intent = time.perf_counter()
        analysis: QueryAnalysis = self.planner.analyze_query(
            cleaned_question,
            conversation_history=conversation_history,
            available_tools=tool_executor.list_tools(),
            provider=active_client.provider,
            enable_external_search=enable_external_search,
        )
        stage_timings_ms["intent_and_planning_ms"] += (time.perf_counter() - _t_intent) * 1000.0
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

        requested_provider = str(provider or "local").strip().lower()
        is_mock_llm = active_client.provider != "local" and client is not None
        is_live_llm = active_client.provider != "local" and client is None
        planning_status = "not_run"
        synthesis_status = "not_run"
        failed_stage: str | None = None
        failure_category: str | None = None
        failure_reason: str | None = None
        answer_origin = "deterministic_synthesizer"
        fallback_used = False
        fallback_reason: str | None = None
        llm_produced_final_answer = False
        synthesis_failed = False

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
            _t_plan_step = time.perf_counter()
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
            except (ProviderAdapterError, RuntimeError, ValueError, TimeoutError, OSError) as exc:
                stage_timings_ms["intent_and_planning_ms"] += (time.perf_counter() - _t_plan_step) * 1000.0
                err_cat, err_msg = classify_provider_exception(exc, provider=active_client.provider)
                logger.log_step(
                    thought="Request next dynamic plan step.",
                    tool="llm",
                    input_data={"question": cleaned_question, "step": step_num},
                    error=err_msg,
                    latency=time.perf_counter() - started_perf,
                    metadata={"failure_category": err_cat, "stage": "synthesis" if observations else "planning"},
                )
                has_committed_evidence = any("output" in o and not o.get("error") for o in observations)
                if active_client.provider != "local" and has_committed_evidence:
                    # Preserve already retrieved tool/connector evidence instead of discarding it or silently falling back.
                    planning_status = "succeeded" if planned_actions else "failed"
                    synthesis_status = "failed"
                    synthesis_failed = True
                    failed_stage = "synthesis"
                    failure_category = err_cat
                    failure_reason = err_msg
                    answer_origin = "none_failed"
                    llm_produced_final_answer = False
                    fallback_used = False
                    fallback_reason = None
                    emit(
                        "synthesis",
                        "Answer Synthesis",
                        "failed",
                        {
                            "step": step_num,
                            "provider": active_client.provider,
                            "failed_stage": failed_stage,
                            "failure_category": failure_category,
                            "failure_reason": failure_reason,
                            "preserved_observations": sum(
                                1 for o in observations if "output" in o and not o.get("error")
                            ),
                            "fallback_used": False,
                        },
                    )
                    final_decision_payload = {
                        "thought": (
                            f"Provider '{active_client.provider}' failed during post-retrieval synthesis ({err_cat}); "
                            "retrieved evidence preserved without silent deterministic fallback."
                        ),
                        "action": "final",
                        "final_answer": "",
                        "synthesis_failed": True,
                    }
                    break
                if active_client.provider != "local":
                    raise ProviderUnavailableError(
                        redact_secret_text(
                            f"Configured provider '{active_client.provider}' failed during planning ({err_cat}): {err_msg}"
                        )
                    ) from exc
                raise

            stage_timings_ms["intent_and_planning_ms"] += (time.perf_counter() - _t_plan_step) * 1000.0
            planning_status = "succeeded"

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
                    "research_plan": analysis.research_plan,
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
            _t_tool = time.perf_counter()
            outcome = tool_executor.execute(
                action,
                arguments,
                failure_injector=injector,
            )
            stage_timings_ms["tool_and_connector_ms"] += (time.perf_counter() - _t_tool) * 1000.0
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
            _t_fdr = time.perf_counter()
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
            stage_timings_ms["failure_detection_and_recovery_ms"] += (time.perf_counter() - _t_fdr) * 1000.0

        _t_synth = time.perf_counter()
        if final_decision_payload is None:
            if observations and any("output" in o and not o.get("error") for o in observations):
                synth_adapter = active_client if active_client.provider != "local" else None
                synth_exec_mode = "live_llm" if is_live_llm else ("mock_llm" if is_mock_llm else "offline_deterministic")
                try:
                    synth_meta = EvidenceGroundedSynthesizer().synthesize_with_metadata(
                        cleaned_question,
                        observations,
                        provider_adapter=synth_adapter,
                        execution_mode=synth_exec_mode,
                        allow_deterministic_fallback=False,
                    )
                    synthesis_status = str(synth_meta["synthesis_status"])
                    answer_origin = str(synth_meta["answer_origin"])
                    fallback_used = bool(synth_meta["fallback_used"])
                    fallback_reason = synth_meta.get("fallback_reason")
                    llm_produced_final_answer = bool(synth_meta["llm_produced_final_answer"])
                    final_decision_payload = {
                        "thought": "Max tool steps reached; synthesize committed tool observations.",
                        "action": "final",
                        "final_answer": str(synth_meta["answer"]),
                    }
                except (SynthesisFailureError, ProviderAdapterError, RuntimeError, ValueError, TimeoutError, OSError) as exc:
                    err_cat, err_msg = classify_provider_exception(exc, provider=active_client.provider)
                    synthesis_failed = True
                    synthesis_status = "failed"
                    failed_stage = "synthesis"
                    failure_category = err_cat
                    failure_reason = err_msg
                    answer_origin = "none_failed"
                    fallback_used = False
                    fallback_reason = None
                    llm_produced_final_answer = False
                    logger.log_step(
                        thought="Synthesize committed tool observations with configured provider.",
                        tool="llm",
                        input_data={"question": cleaned_question, "stage": "synthesis"},
                        error=err_msg,
                        latency=time.perf_counter() - started_perf,
                        metadata={"failure_category": err_cat, "stage": "synthesis"},
                    )
                    emit(
                        "synthesis",
                        "Answer Synthesis",
                        "failed",
                        {
                            "provider": active_client.provider,
                            "failed_stage": failed_stage,
                            "failure_category": failure_category,
                            "failure_reason": failure_reason,
                            "preserved_observations": sum(
                                1 for o in observations if "output" in o and not o.get("error")
                            ),
                            "fallback_used": False,
                        },
                    )
                    final_decision_payload = {
                        "thought": (
                            f"Provider '{active_client.provider}' failed during synthesis ({err_cat}); "
                            "retrieved evidence preserved without silent deterministic fallback."
                        ),
                        "action": "final",
                        "final_answer": "",
                        "synthesis_failed": True,
                    }
            else:
                synthesis_status = "skipped_no_evidence"
                answer_origin = "abstained"
                final_decision_payload = {
                    "thought": "Max steps exhausted without valid committed observations.",
                    "action": "final",
                    "final_answer": "The agent could not complete all steps within the configured step budget.",
                }
        elif not synthesis_failed:
            if bool(final_decision_payload.get("blocked_by_guard") or analysis.prompt_injection_flagged):
                synthesis_status = "skipped_blocked"
                answer_origin = "abstained"
                llm_produced_final_answer = False
            elif is_live_llm:
                synthesis_status = "succeeded"
                answer_origin = "real_llm"
                llm_produced_final_answer = True
            elif is_mock_llm:
                synthesis_status = "succeeded"
                answer_origin = "mock_provider"
                llm_produced_final_answer = False
            else:
                synthesis_status = "succeeded"
                answer_origin = "deterministic_synthesizer"
                llm_produced_final_answer = False

        stage_timings_ms["synthesis_ms"] += (time.perf_counter() - _t_synth) * 1000.0
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
        _t_ev = time.perf_counter()
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
            # Clear distinction: synthesis failure vs safety guard vs external retrieval vs local tools
            if synthesis_failed:
                knowledge_source = (
                    "synthesis_failed_evidence_preserved"
                    if (external_sources or any("output" in o and not o.get("error") for o in observations))
                    else "synthesis_failed"
                )
                grounding_report["grounding_score"] = 0.0
                grounding_report["supported_count"] = 0
                grounding_report["status"] = "SYNTHESIS_FAILED"
            elif blocked_by_guard or offline_scope_limited or no_evidence_only:
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
            knowledge_source = (
                "synthesis_failed_evidence_preserved"
                if (synthesis_failed and external_sources)
                else (
                    "synthesis_failed"
                    if synthesis_failed
                    else (
                        "model_generated_no_tools"
                        if direct_llm_no_tools
                        else ("external_search_grounded" if external_sources else "grounding_disabled")
                    )
                )
            )
            grounding_report = {
                "query": cleaned_question,
                "final_answer": raw_answer,
                "grounded_answer": raw_answer,
                "status": "SYNTHESIS_FAILED" if synthesis_failed else "DISABLED",
                "claims": [],
                "grounding_score": 0.0 if synthesis_failed else None,
                "supported_count": 0,
                "unsupported_count": 0,
                "contradicted_count": 0,
                "total_claim_count": 0,
                "knowledge_source": knowledge_source,
                "external_sources": external_sources,
            }
            grounding_report_path = None

        stage_timings_ms["evidence_verification_ms"] += (time.perf_counter() - _t_ev) * 1000.0
        _t_cv = time.perf_counter()
        llm_entailer = (
            create_llm_entailment_evaluator(provider_adapter)
            if (provider_adapter.is_remote_or_custom and client is None and not synthesis_failed)
            else None
        )
        claim_verifier = ClaimEvidenceVerifier(ClaimExtractor(), llm_evaluator=llm_entailer)
        claim_corpus = ClaimEvidenceVerifier.build_evidence_corpus(trace_entries, external_sources)
        claim_evidence_report = claim_verifier.verify_answer(raw_answer, claim_corpus)
        stage_timings_ms["claim_verification_ms"] += (time.perf_counter() - _t_cv) * 1000.0

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
                "retrieved_evidence": claim_corpus,
                "claims": grounding_report.get("claims", []),
                "claim_evidence_report": claim_evidence_report,
            },
        )

        # Stage 9: Uncertainty Estimation & Phase 7 Abstention Taxonomy
        _t_unc = time.perf_counter()
        abstention_category = "grounded_confident"
        reconciled_by_semantic = False
        uncertainty_breakdown: dict[str, Any] = {}
        if enable_uncertainty:
            uncertainty_signals = uncertainty_estimator.estimate(
                grounding_report,
                trace_entries,
                recovery_report=recovery_report,
                recovery_records=recovery_records,
            )
            if synthesis_failed:
                final_decision = "ABSTAIN"
                abstention_category = "provider_synthesis_failure"
                decision_explanation = (
                    f"LLM synthesis failed ({failure_category or 'provider_error'}): "
                    f"{failure_reason or 'provider request failed'}. "
                    f"Research retrieval preserved {len(external_sources)} external source(s) and "
                    f"{len(claim_corpus)} evidence item(s) without silent deterministic fallback."
                )
                abstention_reason = decision_explanation
                final_response = (
                    f"LLM synthesis failed ({failure_category or 'provider_error'}): "
                    f"{failure_reason or 'provider request failed'}. "
                    f"Retrieved evidence ({len(external_sources)} external source(s), {len(claim_corpus)} evidence item(s)) "
                    "is preserved below; no synthetic or deterministic fallback answer was substituted."
                )
            elif direct_llm_no_tools and not blocked_by_guard:
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

            if not synthesis_failed:
                tax = classify_abstention_and_uncertainty(
                    final_decision=final_decision,
                    decision_explanation=decision_explanation,
                    blocked_by_guard=blocked_by_guard,
                    offline_scope_limited=offline_scope_limited,
                    grounding_report=grounding_report,
                    claim_evidence_report=claim_evidence_report,
                    failure_report=failure_report,
                    recovery_report=recovery_report,
                    uncertainty_signals=uncertainty_signals,
                    external_sources=external_sources,
                    direct_llm_no_tools=direct_llm_no_tools,
                    answer_threshold=answer_threshold,
                    abstain_threshold=abstain_threshold,
                )
                final_decision = tax["final_decision"]
                decision_explanation = tax["decision_explanation"]
                abstention_category = tax["abstention_category"]
                abstention_reason = tax.get("abstention_reason") or decision_explanation
                reconciled_by_semantic = tax["reconciled_by_semantic_verifier"]
                uncertainty_breakdown = tax["uncertainty_breakdown"]
                if final_decision == "ABSTAIN" and not blocked_by_guard:
                    final_response = f"I am abstaining: {decision_explanation}"
                elif reconciled_by_semantic:
                    final_response = raw_answer

            uncertainty_report = {
                "query": cleaned_question,
                "original_answer": raw_answer,
                **uncertainty_signals,
                "final_decision": final_decision,
                "abstention_category": abstention_category,
                "abstention_reason": abstention_reason,
                "reconciled_by_semantic_verifier": reconciled_by_semantic,
                "uncertainty_breakdown": uncertainty_breakdown,
                "decision_explanation": decision_explanation,
                "final_response": final_response,
                "thresholds": {
                    "answer_threshold": answer_threshold,
                    "abstain_threshold": abstain_threshold,
                },
            }
            uncertainty_report_path = logger.write_uncertainty_report(uncertainty_report)
        else:
            if synthesis_failed:
                final_decision = "ABSTAIN"
                abstention_category = "provider_synthesis_failure"
                decision_explanation = (
                    f"LLM synthesis failed ({failure_category or 'provider_error'}): "
                    f"{failure_reason or 'provider request failed'}. Retrieved evidence preserved without silent fallback."
                )
                abstention_reason = decision_explanation
                final_response = (
                    f"LLM synthesis failed ({failure_category or 'provider_error'}): "
                    f"{failure_reason or 'provider request failed'}. "
                    f"Retrieved evidence ({len(external_sources)} external source(s), {len(claim_corpus)} evidence item(s)) "
                    "is preserved below; no synthetic or deterministic fallback answer was substituted."
                )
            else:
                final_decision = "ABSTAIN" if blocked_by_guard else "NOT_APPLICABLE"
                abstention_category = "policy_blocked" if blocked_by_guard else "grounded_confident"
                decision_explanation = (
                    analysis.notes
                    if blocked_by_guard
                    else "Uncertainty estimation was disabled."
                )
                abstention_reason = decision_explanation
                final_response = (
                    f"I am abstaining: {decision_explanation}" if blocked_by_guard else grounding_report["grounded_answer"]
                )
            uncertainty_report = {
                "query": cleaned_question,
                "original_answer": raw_answer,
                "status": "DISABLED",
                "uncertainty_score": None,
                "final_decision": final_decision,
                "abstention_category": abstention_category,
                "abstention_reason": abstention_reason,
                "decision_explanation": decision_explanation,
                "final_response": final_response,
            }
            uncertainty_report_path = None

        # Append verified external source links strictly from retrieved committed results
        if external_sources and final_decision != "ABSTAIN":
            source_lines = "\n".join(f"- [{s['title']}]({s['url']})" for s in external_sources)
            final_response = f"{final_response}\n\nVerified External Sources:\n{source_lines}"

        stage_timings_ms["uncertainty_ms"] += (time.perf_counter() - _t_unc) * 1000.0
        emit(
            "uncertainty_estimation",
            "9. Uncertainty Estimation",
            "ok",
            {
                "uncertainty_score": uncertainty_report.get("uncertainty_score"),
                "tool_success_ratio": uncertainty_report.get("tool_success_ratio"),
                "recovery_reliability": uncertainty_report.get("recovery_reliability"),
                "abstention_category": abstention_category,
                "abstention_reason": abstention_reason,
                "uncertainty_breakdown": uncertainty_breakdown,
            },
        )

        # Stage 10: Final Decision
        emit(
            "final_decision",
            "10. Final Decision",
            "ok" if final_decision == "ANSWER" else ("warning" if final_decision == "WARN" else "abstained"),
            {
                "final_decision": final_decision,
                "abstention_category": abstention_category,
                "abstention_reason": abstention_reason,
                "knowledge_source": knowledge_source,
                "decision_explanation": decision_explanation,
                "final_response": final_response,
            },
        )

        elapsed_total = round(time.perf_counter() - started_perf, 6)
        provenance = _format_step_provenance(trace_entries)
        llm_usage = active_client.usage_summary()

        has_mock_adapter = self.search_adapter is not None and (
            getattr(self.search_adapter, "_provider_fn", None) is not None
            or getattr(self.search_adapter, "provider_fn", None) is not None
        )
        connector_obs = [
            o
            for o in observations
            if str(o.get("tool") or "") in {"external_search", "research_sources", "browser_inspect"}
            or str(o.get("tool") or "").startswith("mcp_")
        ]
        used_external_tool = bool(external_sources) or bool(connector_obs) or any(
            t in {"external_search", "research_sources", "browser_inspect"} or str(t).startswith("mcp_")
            for t in provenance["tools_used"]
        )
        if not used_external_tool:
            connector_status = "not_used"
        else:
            conn_err = sum(1 for o in connector_obs if o.get("error"))
            conn_ok = sum(1 for o in connector_obs if not o.get("error") and "output" in o)
            if external_sources or (conn_ok > 0 and conn_err == 0 and not no_evidence_only):
                connector_status = "partial" if conn_err > 0 else "succeeded"
            elif conn_ok > 0 and conn_err > 0:
                connector_status = "partial"
            elif no_evidence_only:
                connector_status = "no_results"
            else:
                connector_status = "failed"

        is_mock_conn = has_mock_adapter and (enable_external_search or used_external_tool)
        is_live_conn = used_external_tool and not has_mock_adapter

        if active_client.provider != "local":
            execution_mode = "live_llm" if client is None else "mock_llm"
        elif is_mock_conn:
            execution_mode = "mock_connectors"
        elif is_live_conn:
            execution_mode = "live_connectors"
        else:
            execution_mode = "offline_deterministic"

        if synthesis_failed:
            if is_live_llm and is_live_conn:
                execution_mode_label = "LIVE CONNECTOR (LIVE LLM SYNTHESIS FAILED)"
            elif is_live_llm:
                execution_mode_label = "LIVE LLM FAILED"
            elif is_mock_llm and is_live_conn:
                execution_mode_label = "LIVE CONNECTOR (MOCK SYNTHESIS FAILED)"
            elif is_mock_llm:
                execution_mode_label = "MOCK (SYNTHESIS FAILED)"
            else:
                execution_mode_label = "SYNTHESIS FAILED"
        elif is_live_llm and is_live_conn:
            execution_mode_label = "LIVE CONNECTOR + LIVE LLM"
        elif is_live_llm:
            execution_mode_label = "LIVE LLM"
        elif is_live_conn:
            execution_mode_label = "LIVE CONNECTOR"
        elif is_mock_conn or is_mock_llm:
            execution_mode_label = "MOCK"
        else:
            execution_mode_label = "OFFLINE"

        plan_subtasks = list((analysis.research_plan or {}).get("subtasks") or [])
        gap_fills = sum(1 for st in plan_subtasks if st.get("is_gap_fill"))
        failed_obs = sum(1 for o in observations if o.get("error"))
        succeeded_obs = sum(1 for o in observations if not o.get("error") and "output" in o)
        plan_stats = {
            "total_subtasks": len(plan_subtasks),
            "completed_subtasks": min(len(plan_subtasks), succeeded_obs) if plan_subtasks else succeeded_obs,
            "failed_subtasks": failed_obs,
            "gap_fill_attempts": gap_fills,
            "stop_reason": (
                f"synthesis_failed:{failure_category}"
                if synthesis_failed
                else ((analysis.research_plan or {}).get("stop_reason") or "completed")
            ),
        }
        provider_status_base = provider_adapter.provider_status_summary()
        if synthesis_failed:
            provider_status_base["connection_status"] = failure_category or "failed"
            provider_status_base["last_error_category"] = failure_category
        provider_info = {
            **provider_status_base,
            "requested_provider": requested_provider,
            "actual_provider": active_client.provider,
            "execution_mode": execution_mode,
            "execution_mode_label": execution_mode_label,
            "planning_status": planning_status,
            "connector_status": connector_status,
            "synthesis_status": synthesis_status,
            "answer_origin": answer_origin,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "failed_stage": failed_stage,
            "failure_category": failure_category,
            "failure_reason": failure_reason,
            "llm_produced_final_answer": llm_produced_final_answer,
            "call_count": llm_usage.get("call_count", 0),
            "retry_count": llm_usage.get("retry_count", provider_adapter.retry_count),
            "failed_requests": max(
                int(llm_usage.get("failed_requests", provider_adapter.failed_requests) or 0),
                1 if synthesis_failed else 0,
            ),
            "prompt_tokens": llm_usage.get("prompt_tokens", 0),
            "completion_tokens": llm_usage.get("completion_tokens", 0),
            "total_tokens": llm_usage.get("total_tokens", 0),
            "usage_source": llm_usage.get("usage_source", provider_adapter.last_usage_source),
            "total_latency_seconds": llm_usage.get("total_latency_seconds", 0.0),
            "estimated_cost_usd": llm_usage.get("estimated_cost_usd", 0.0),
        }
        structured_answer = EvidenceGroundedSynthesizer.synthesize_structured(
            cleaned_question,
            observations,
            raw_answer=raw_answer,
            external_sources=external_sources,
            claim_evidence_report=claim_evidence_report,
            uncertainty_report=uncertainty_report,
            recovery_report=recovery_report,
            execution_mode=execution_mode,
            synthesis_status=synthesis_status,
            failure_category=failure_category,
            failure_reason=failure_reason,
            answer_origin=answer_origin,
        )
        known_ext_urls = {str(s.get("url") or "") for s in external_sources if s.get("url")}
        struct_cites = structured_answer.get("citations") or []
        integration_audit = {
            "planner_called_model": bool(active_client.provider != "local" and int(llm_usage.get("call_count", 0)) > 0),
            "connectors_executed": [
                t for t in provenance["tools_used"]
                if t in {"external_search", "research_sources", "browser_inspect"} or str(t).startswith("mcp_")
            ],
            "retrieved_evidence_count": len(claim_corpus),
            "retrieved_evidence_reached_synthesis": bool(structured_answer.get("supporting_evidence")),
            "synthesis_succeeded": synthesis_status == "succeeded",
            "llm_produced_final_answer": llm_produced_final_answer,
            "fallback_used": fallback_used,
            "verifier_evaluated_post_synthesis": claim_evidence_report is not None,
            "citations_traceable_to_corpus": all(str(c.get("url") or "") in known_ext_urls for c in struct_cites),
            "unsupported_or_contradicted_flagged": int(claim_evidence_report.get("unsupported_count") or 0) + int(claim_evidence_report.get("contradicted_count") or 0),
        }
        execution_integrity = {
            "requested_provider": requested_provider,
            "actual_provider": active_client.provider,
            "actual_model": active_client.model,
            "execution_mode": execution_mode,
            "execution_mode_label": execution_mode_label,
            "planning_status": planning_status,
            "connector_status": connector_status,
            "synthesis_status": synthesis_status,
            "answer_origin": answer_origin,
            "llm_produced_final_answer": llm_produced_final_answer,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "failed_stage": failed_stage,
            "failure_category": failure_category,
            "failure_reason": failure_reason,
            "preserved_external_sources_count": len(external_sources),
            "preserved_evidence_count": len(claim_corpus),
            "live_llm_succeeded": bool(is_live_llm and synthesis_status == "succeeded" and llm_produced_final_answer),
        }
        completed_at_iso = datetime.now(timezone.utc).isoformat()
        total_duration_ms = round(elapsed_total * 1000.0, 2)
        rounded_timings = {k: round(v, 2) for k, v in stage_timings_ms.items()}
        tool_call_summary = {
            "total_calls": len(observations),
            "succeeded": succeeded_obs,
            "failed": failed_obs,
            "recovered": sum(1 for o in observations if o.get("recovered")),
            "tools": provenance["tools_used"],
        }
        observability = {
            "trace_id": logger.path.stem,
            "query_id": active_qid,
            "conversation_id": conversation_id,
            "session_ref": masked_sid,
            "execution_mode": execution_mode,
            "execution_mode_label": execution_mode_label,
            "started_at": started_at_iso,
            "completed_at": completed_at_iso,
            "total_duration_ms": total_duration_ms,
            "stage_timings_ms": rounded_timings,
            "tool_call_summary": tool_call_summary,
            "retry_count": int(provider_info.get("retry_count", 0) or 0),
            "recovery_outcome": {
                "attempted": bool(recovery_report.get("recovery_attempted")),
                "successful": int(recovery_report.get("successful_recoveries", 0) or 0),
                "failed": int(recovery_report.get("failed_recoveries", 0) or 0),
                "strategies_used": list(recovery_report.get("strategies_used", [])),
            },
            "final_decision": final_decision,
            "abstention_category": abstention_category,
            "abstention_reason": redact_secret_text(str(abstention_reason or "")),
            "provider_status": provider_info.get("connection_status"),
            "token_usage": {
                "prompt_tokens": int(llm_usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(llm_usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(llm_usage.get("total_tokens", 0) or 0),
                "usage_source": llm_usage.get("usage_source", provider_adapter.last_usage_source),
            },
        }
        _emit_structured_log(
            "query.failed" if synthesis_failed else "query.completed",
            level=logging.WARNING if synthesis_failed else logging.INFO,
            trace_id=logger.path.stem,
            query_id=active_qid,
            session_ref=masked_sid,
            execution_mode=execution_mode,
            execution_mode_label=execution_mode_label,
            final_decision=final_decision,
            synthesis_status=synthesis_status,
            failure_category=failure_category,
            total_duration_ms=total_duration_ms,
            tool_calls=len(observations),
            recovery_successes=int(recovery_report.get("successful_recoveries", 0) or 0),
        )
        return {
            "query_id": active_qid,
            "conversation_id": conversation_id,
            "trace_id": logger.path.stem,
            "timestamp": completed_at_iso,
            "observability": observability,
            "question": cleaned_question,
            "resolved_query": analysis.resolved_query,
            "requested_provider": requested_provider,
            "actual_provider": active_client.provider,
            "provider": active_client.provider,
            "provider_scope": "offline_deterministic_local" if active_client.provider == "local" else "live_remote_llm",
            "execution_mode": execution_mode,
            "execution_mode_label": execution_mode_label,
            "planning_status": planning_status,
            "connector_status": connector_status,
            "synthesis_status": synthesis_status,
            "answer_origin": answer_origin,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "failed_stage": failed_stage,
            "failure_category": failure_category,
            "failure_reason": failure_reason,
            "llm_produced_final_answer": llm_produced_final_answer,
            "execution_integrity": execution_integrity,
            "provider_info": provider_info,
            "integration_audit": integration_audit,
            "model": active_client.model,
            "knowledge_source": knowledge_source,
            "external_sources": external_sources,
            "retrieved_evidence": claim_corpus,
            "analysis": analysis.to_dict(),
            "source_selection": analysis.source_selection,
            "research_plan": analysis.research_plan,
            "plan_stats": plan_stats,
            "planned_actions": planned_actions,
            "tools_selected": provenance["tools_used"],
            "connector_tools_registered": connector_tools_registered,
            "answer": raw_answer,
            "structured_answer": structured_answer,
            "grounded_answer": grounding_report["grounded_answer"],
            "final_decision": final_decision,
            "abstention_category": abstention_category,
            "abstention_reason": abstention_reason,
            "reconciled_by_semantic_verifier": reconciled_by_semantic,
            "decision_explanation": decision_explanation,
            "final_response": final_response,
            "provenance": provenance,
            "cache_stats": tool_executor.cache_stats(),
            "llm_usage": llm_usage,
            "elapsed_seconds": elapsed_total,
            "failure_report": failure_report,
            "recovery_report": recovery_report,
            "grounding_report": grounding_report,
            "claim_evidence_report": claim_evidence_report,
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
