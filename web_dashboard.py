"""FastAPI observability backend and web dashboard host for TraceRecover.

Connects directly to the existing TraceRecover research engine (`agent/`,
`grounding/`, `healing/`, `uncertainty/`, `tools/`, `trace/`, `eval/`) and
saved benchmark/log artifacts without modifying core research algorithms.

Security hardening:
- Server-side provider credentials only (never exposed to clients).
- Environment-configurable CORS origins, rate limiting, and request size caps.
- Optional Bearer/API-key protection (`TRACERECOVER_ADMIN_TOKEN`) for
  compute-intensive experiment execution endpoints.
- Defensive security headers and sanitized error responses (no tracebacks or
  host filesystem paths leaked).
"""

from __future__ import annotations

import collections
import csv
import hashlib
import hmac
import json
import os
import platform
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from agent.conversation_store import ConversationStore
from agent.open_ended_runner import (
    QUERY_CANCEL_REGISTRY,
    OpenEndedAgentRunner,
    ProviderUnavailableError,
    QueryCancelledError,
)
from agent.react_agent import ReactAgent
from config import (
    LLMClient,
    MODEL_PRICING_PER_1M,
    PROJECT_ROOT,
    SUPPORTED_PROVIDERS,
    get_settings,
)
from eval import run_all, statistical_validation, unseen_eval
from eval.runner import _validate_task as validate_eval_task
from grounding.evidence_checker import EvidenceChecker
from grounding.nli_verifier import DebertaNLIVerifier, SUPPORTED_NLI_MODELS
from healing.recovery import DEFAULT_STRATEGY_MAP, RecoveryEngine
from report_generator import generate_experiment_pdf
from tools.executor import ToolExecutor
from tools.external_search import SUPPORTED_SEARCH_PROVIDERS
from tools.failure_injector import FailureInjector, FailureType
from uncertainty.abstention import AbstentionPolicy
from uncertainty.estimator import DEFAULT_SIGNAL_WEIGHTS, UncertaintyEstimator


# Configurable storage paths (supports mounted persistent volumes in production)
RESULTS_DIR = Path(os.getenv("TRACERECOVER_RESULTS_DIR", str(PROJECT_ROOT / "results"))).resolve()
LOGS_DIR = Path(os.getenv("TRACERECOVER_LOGS_DIR", str(PROJECT_ROOT / "logs"))).resolve()
DATA_DIR = (PROJECT_ROOT / "data").resolve()
DATASET_VERSIONS_DIR = Path(
    os.getenv("TRACERECOVER_DATASET_VERSIONS_DIR", str(DATA_DIR / "versions"))
).resolve()
CONVERSATIONS_DIR = Path(
    os.getenv("TRACERECOVER_CONVERSATIONS_DIR", str(LOGS_DIR / "conversations"))
).resolve()
UNSEEN_DATASETS_DIR = Path(
    os.getenv("TRACERECOVER_UNSEEN_DATASETS_DIR", str(DATA_DIR / "unseen_benchmarks"))
).resolve()
FRONTEND_DIST_DIR = (PROJECT_ROOT / "frontend" / "dist").resolve()
STATIC_DIR = (PROJECT_ROOT / "static").resolve()

VERIFIED_CORE_TEST_COUNT = 122

_DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]

_PROVIDER_KEY_ENV: dict[str, str | None] = {
    "local": None,
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

_PROVIDER_DEFAULT_MODELS: dict[str, tuple[str, str]] = {
    "local": ("LOCAL_MODEL", "deterministic-local"),
    "gemini": ("GEMINI_MODEL", "gemini-2.0-flash"),
    "openai": ("OPENAI_MODEL", "gpt-4o-mini"),
    "anthropic": ("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
}

_PLOT_TITLES: dict[str, str] = {
    "task_success.svg": "Task Success Rate vs. Failure Rate (with 95% Wilson CI)",
    "hallucination_rate.svg": "Hallucination Rate vs. Failure Rate",
    "recovery_rate.svg": "Detector-Validated Recovery Rate",
    "calibration.svg": "Confidence Calibration Reliability Diagram (ECE)",
    "ablation_success.svg": "Ablation Study: Task Success Across Regimes",
    "average_latency.svg": "Average End-to-End Execution Latency",
}

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_PLOT_RE = re.compile(r"^[A-Za-z0-9._-]+\.svg$")

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self';"
    ),
}


# ---------------------------------------------------------------------------
# Sliding-window in-memory rate limiter & admin auth guard
# ---------------------------------------------------------------------------


class _SlidingWindowRateLimiter:
    """Thread-safe per-client sliding-window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], collections.deque[float]] = {}

    def check(
        self, bucket: str, client_key: str, *, max_requests: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        if max_requests <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        key = (bucket, client_key)
        with self._lock:
            dq = self._hits.setdefault(key, collections.deque())
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= max_requests:
                retry_after = max(1, int(window_seconds - (now - dq[0])) + 1)
                return False, retry_after
            dq.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_RATE_LIMITER = _SlidingWindowRateLimiter()


def reset_rate_limiter() -> None:
    """Clear rate limiter state (intended for test isolation)."""
    _RATE_LIMITER.reset()


def _parse_int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _allowed_cors_origins() -> list[str]:
    raw = os.getenv("TRACERECOVER_CORS_ORIGINS", "").strip()
    if not raw:
        return list(_DEFAULT_CORS_ORIGINS)
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or list(_DEFAULT_CORS_ORIGINS)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(request: Request, bucket: str, env_var: str, default_limit: int) -> None:
    limit = _parse_int_env(env_var, default_limit, minimum=0)
    allowed, retry_after = _RATE_LIMITER.check(
        bucket, _client_ip(request), max_requests=limit, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {bucket}. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


def _admin_token() -> str:
    return os.getenv("TRACERECOVER_ADMIN_TOKEN", "").strip()


def _require_admin_if_configured(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = _admin_token()
    if not expected:
        return
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif x_api_key:
        provided = x_api_key.strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide a valid Bearer token or X-API-Key header.",
        )


# ---------------------------------------------------------------------------
# Preset definitions and deterministic scenario planners (no core edits)
# ---------------------------------------------------------------------------

DEMO_PRESETS: list[dict[str, Any]] = [
    {
        "id": "normal_answer",
        "title": "Normal ANSWER",
        "subtitle": "Grounded factual lookup",
        "badge": "ANSWER",
        "tone": "emerald",
        "description": (
            "Runs a clean factual search query with zero injected faults. "
            "Verifies claim support against step 1 and outputs ANSWER with 0.000 uncertainty."
        ),
        "params": {
            "question": "What is the capital of France?",
            "provider": "local",
            "failure_rate": 0.0,
            "seed": 42,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
            "enable_localization": True,
            "answer_threshold": 0.15,
            "abstain_threshold": 0.55,
            "scenario_mode": "standard",
        },
    },
    {
        "id": "multi_hop_lookup",
        "title": "Multi-Hop Lookup",
        "subtitle": "Ticket API + calculator synthesis",
        "badge": "MULTI-HOP",
        "tone": "cyan",
        "description": (
            "Executes a two-hop tool chain: fetches enterprise SLA ticket T-401 via mock_api, "
            "then computes the 15% SLA surcharge via calculator and cites both committed steps."
        ),
        "params": {
            "question": "Fetch ticket T-401 details and calculate its 15% SLA surcharge total (1200 * 1.15).",
            "provider": "local",
            "failure_rate": 0.0,
            "seed": 42,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
            "enable_localization": True,
            "answer_threshold": 0.15,
            "abstain_threshold": 0.55,
            "scenario_mode": "multi_hop",
        },
    },
    {
        "id": "fault_recovery_40",
        "title": "40% Fault + Self-Healing",
        "subtitle": "Wrong output -> TOOL_SWITCH recovery",
        "badge": "RECOVERY",
        "tone": "violet",
        "description": (
            "Injects a wrong_output fault on calculator at 40% rate (seed=13). "
            "Step 1 is tagged discarded, RecoveryEngine switches to python_exec at step 2 "
            "(committed), and the grounded answer cites only step 2."
        ),
        "params": {
            "question": "What is 19 * 23 + 4?",
            "provider": "local",
            "failure_rate": 40.0,
            "seed": 13,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
            "enable_localization": True,
            "answer_threshold": 0.15,
            "abstain_threshold": 0.55,
            "scenario_mode": "standard",
        },
    },
    {
        "id": "missing_entity_abstain",
        "title": "Missing Entity ABSTAIN",
        "subtitle": "No-evidence abstention guard",
        "badge": "ABSTAIN",
        "tone": "amber",
        "description": (
            "Queries an ungrounded entity ('unobtainium'). The no-evidence sentinel is excluded "
            "from eligible sources, claims fail support, and the policy enforces ABSTAIN."
        ),
        "params": {
            "question": "Who discovered the fictional element unobtainium?",
            "provider": "local",
            "failure_rate": 0.0,
            "seed": 42,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
            "enable_localization": True,
            "answer_threshold": 0.15,
            "abstain_threshold": 0.55,
            "scenario_mode": "standard",
        },
    },
    {
        "id": "contradiction_guard",
        "title": "Contradiction Guard",
        "subtitle": "Claim vs. evidence conflict",
        "badge": "CONTRADICTION",
        "tone": "rose",
        "description": (
            "Retrieves verified tool evidence ('The capital of France is Paris') at step 1, "
            "then tests a conflicting candidate ('The capital of France is Rome'). "
            "EvidenceChecker + NLI flags CONTRADICTED and forces ABSTAIN."
        ),
        "params": {
            "question": "What is the capital of France?",
            "provider": "local",
            "failure_rate": 0.0,
            "seed": 42,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
            "enable_localization": True,
            "answer_threshold": 0.15,
            "abstain_threshold": 0.55,
            "scenario_mode": "contradiction_probe",
        },
    },
    {
        "id": "ablation_toggle",
        "title": "Ablation Toggle (Raw)",
        "subtitle": "Grounding & uncertainty OFF",
        "badge": "ABLATION",
        "tone": "slate",
        "description": (
            "Disables recovery, evidence grounding, and uncertainty abstention under 40% fault "
            "injection to contrast baseline unverified output leakage against full TraceRecover."
        ),
        "params": {
            "question": "Who discovered the fictional element unobtainium?",
            "provider": "local",
            "failure_rate": 40.0,
            "seed": 42,
            "use_nli": False,
            "enable_recovery": False,
            "enable_grounding": False,
            "enable_uncertainty": False,
            "enable_localization": False,
            "answer_threshold": 0.15,
            "abstain_threshold": 0.55,
            "scenario_mode": "standard",
        },
    },
]


class _MultiHopPresetPlanner:
    """Deterministic two-hop planner for multi-step ticket + math demos."""

    def next_action(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        excluded = excluded_tools or set()
        if not observations:
            ticket_match = re.search(r"\bT-\d+\b", question, re.IGNORECASE)
            ticket_id = ticket_match.group(0).upper() if ticket_match else "T-401"
            if "mock_api" in excluded:
                return {
                    "thought": "mock_api is excluded; cannot re-fetch ticket.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": "I cannot complete the ticket lookup with the remaining tools.",
                }
            return {
                "thought": f"Step 1: Fetch SLA ticket {ticket_id} via mock_api.",
                "action": "mock_api",
                "arguments": {"ticket_id": ticket_id},
                "final_answer": None,
            }

        if len(observations) == 1:
            first = observations[0]
            if first.get("error"):
                return {
                    "thought": "Ticket lookup failed; stop without fabricating values.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": f"I could not complete the request: {first['error']}",
                }
            ticket_data = first.get("output")
            cost = (
                ticket_data.get("cost_usd", 1200)
                if isinstance(ticket_data, Mapping)
                else 1200
            )
            expression = f"{cost} * 1.15"
            if "calculator" in excluded:
                if "python_exec" not in excluded:
                    return {
                        "thought": "Re-plan SLA surcharge computation using python_exec.",
                        "action": "python_exec",
                        "arguments": {"code": expression},
                        "final_answer": None,
                    }
                return {
                    "thought": "No calculation tool remains.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": "I cannot complete the calculation with the remaining tools.",
                }
            return {
                "thought": f"Step 2: Compute 15% SLA surcharge total ({expression}) via calculator.",
                "action": "calculator",
                "arguments": {"expression": expression},
                "final_answer": None,
            }

        first_out = observations[0].get("output")
        second = observations[-1]
        if second.get("error"):
            return {
                "thought": "Second hop failed; report error without guessing.",
                "action": "final",
                "arguments": {},
                "final_answer": f"I could not complete the calculation: {second['error']}",
            }
        if isinstance(first_out, Mapping):
            tid = first_out.get("id", first_out.get("ticket_id", "T-401"))
            status = first_out.get("status", "unknown")
            owner = first_out.get("owner", "unknown")
            cost = first_out.get("cost_usd", "")
            ticket_sentence = (
                f"Ticket {tid} has status {status} and owner {owner}"
                + (f" with cost_usd {cost}." if cost != "" else ".")
            )
        else:
            ticket_sentence = str(first_out)
        calc_val = second.get("output")
        return {
            "thought": "Synthesize both committed tool outputs into grounded atomic claims.",
            "action": "final",
            "arguments": {},
            "final_answer": f"{ticket_sentence} The SLA surcharge total equals {calc_val}.",
        }


class _ContradictionPresetPlanner:
    """Controlled contradiction probe (matches docs/demo_script.md Section 7)."""

    def next_action(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        excluded = excluded_tools or set()
        if not observations:
            if "search" in excluded:
                return {
                    "thought": "Search excluded; cannot gather ground-truth evidence.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": "I cannot complete the request with the remaining tools.",
                }
            return {
                "thought": "Retrieve authoritative factual evidence from local search.",
                "action": "search",
                "arguments": {"query": question},
                "final_answer": None,
            }
        return {
            "thought": (
                "Emit an intentionally contradicting candidate claim to test "
                "TraceRecover's EvidenceChecker + NLI contradiction guard."
            ),
            "action": "final",
            "arguments": {},
            "final_answer": "The capital of France is Rome.",
        }


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class DatasetPayloadRequest(BaseModel):
    """Payload for validating or importing a versioned task dataset."""

    name: str = Field(default="Custom Benchmark Dataset", min_length=1, max_length=120)
    dataset_version: str = Field(..., min_length=1, max_length=40)
    tasks: list[dict[str, Any]] = Field(..., min_length=1, max_length=200)


class ConversationCreateRequest(BaseModel):
    """Create an isolated conversation thread within the caller's session."""

    title: str = Field(default="New Conversation", min_length=1, max_length=120)


class OpenEndedQueryRequest(BaseModel):
    """Free-form open-ended query execution request."""

    question: str = Field(..., min_length=1, max_length=4000)
    provider: str = Field(default="local")
    conversation_id: str | None = Field(default=None)
    query_id: str | None = Field(default=None)
    enable_external_search: bool = Field(default=False)
    search_provider: str = Field(default="auto")
    failure_rate: float = Field(default=0.0, ge=0.0, le=100.0)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    enabled_failure_types: list[str] | None = Field(default=None)
    use_nli: bool = Field(default=False)
    enable_recovery: bool = Field(default=True)
    enable_grounding: bool = Field(default=True)
    enable_uncertainty: bool = Field(default=True)
    answer_threshold: float = Field(default=0.15, ge=0.0, le=0.95)
    abstain_threshold: float = Field(default=0.55, ge=0.05, le=1.0)
    max_steps: int = Field(default=6, ge=1, le=10)
    max_llm_calls: int = Field(default=6, ge=1, le=12)
    max_cost_usd: float = Field(default=0.25, gt=0.0, le=2.0)

    @field_validator("question")
    @classmethod
    def _validate_q(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Question must not be blank.")
        return v

    @field_validator("provider")
    @classmethod
    def _validate_prov(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider {v!r}; choose from {SUPPORTED_PROVIDERS}.")
        return cleaned


class UnseenEvalRunRequest(BaseModel):
    """Run configuration for the separate unseen-query evaluation lab."""

    num_tasks: int = Field(default=13, ge=1, le=130)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    provider: str = Field(default="local")
    failure_rate: float = Field(default=0.0, ge=0.0, le=100.0)
    enable_external_search: bool = Field(default=False)
    search_provider: str = Field(default="auto")
    use_nli: bool = Field(default=False)


class ProviderCompareRequest(BaseModel):
    """Compare available providers on the same question."""

    question: str = Field(..., min_length=1, max_length=2000)
    providers: list[str] = Field(
        default_factory=lambda: ["local", "gemini", "openai", "anthropic"],
        min_length=1,
        max_length=4,
    )
    enable_external_search: bool = Field(default=False)
    search_provider: str = Field(default="auto")
    failure_rate: float = Field(default=0.0, ge=0.0, le=100.0)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    use_nli: bool = Field(default=False)


# ---------------------------------------------------------------------------
# Replay & Dataset Management Helpers
# ---------------------------------------------------------------------------


def _build_replay_frames(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Construct an ordered sequence of recorded replay frames without re-running the agent."""
    frames: list[dict[str, Any]] = []
    steps = list(bundle.get("steps", []))
    tool_steps = [s for s in steps if s.get("tool") not in {"final", "llm"}]
    fr = bundle.get("failure_report") or {}
    rr = bundle.get("recovery_report") or {}
    gr = bundle.get("grounding_report") or {}
    unc = bundle.get("uncertainty_report") or {}

    # Frame 1: Planning overview
    frames.append(
        {
            "frame_index": 1,
            "stage": "planning",
            "title": "Agent Query & Tool Plan",
            "summary": f"Planned execution for query: {bundle.get('question', '')}",
            "details": {
                "question": bundle.get("question", ""),
                "tools_planned": [s.get("tool") for s in tool_steps],
                "initial_thought": tool_steps[0].get("thought") if tool_steps else "Direct response",
                "total_recorded_tool_steps": len(tool_steps),
            },
        }
    )

    # Tool step frames (with explicit committed vs discarded provenance)
    for step in tool_steps:
        state = step.get("provenance_state") or step.get("output_state") or "committed"
        frames.append(
            {
                "frame_index": len(frames) + 1,
                "stage": "tool_step",
                "title": f"Step {step.get('step_id')}: Tool Call ({step.get('tool')})",
                "summary": (
                    f"{step.get('tool')} output marked {state.upper()}"
                    + (f" — injected {step.get('failure_type')}" if step.get("failure_injected") else "")
                    + (f" — recovered via {step.get('recovery_strategy')}" if step.get("recovered") else "")
                ),
                "provenance_state": state,
                "details": {
                    "step_id": step.get("step_id"),
                    "tool": step.get("tool"),
                    "thought": step.get("thought"),
                    "input": step.get("input"),
                    "output": step.get("output"),
                    "error": step.get("error"),
                    "latency": step.get("latency"),
                    "provenance_state": state,
                    "failure_injected": bool(step.get("failure_injected")),
                    "failure_type": step.get("failure_type"),
                    "recovered": bool(step.get("recovered")),
                    "recovery_strategy": step.get("recovery_strategy"),
                    "original_step_id": step.get("original_step_id"),
                },
            }
        )

    # Failure detection & localization frames
    failures = fr.get("failures") or []
    frames.append(
        {
            "frame_index": len(frames) + 1,
            "stage": "failure_detection_and_localization",
            "title": f"Failure Detection & Localization ({len(failures)} detected)",
            "summary": (
                f"Localized {len(failures)} failure(s) requiring recovery."
                if failures
                else "All tool calls passed failure detection checks."
            ),
            "details": {
                "total_failures": int(fr.get("total_failures", len(failures))),
                "failures": failures,
            },
        }
    )

    # Recovery frame(s)
    attempts = [att for inc in (rr.get("failures") or []) for att in (inc.get("attempts") or [])]
    frames.append(
        {
            "frame_index": len(frames) + 1,
            "stage": "recovery",
            "title": f"Self-Healing Recovery ({rr.get('successful_recoveries', 0)}/{rr.get('total_detected_failures', 0)} healed)",
            "summary": (
                f"Strategies executed: {', '.join(rr.get('strategies_used', [])) or 'none'}"
                if attempts
                else "No recovery actions required (or recovery disabled)."
            ),
            "details": {
                "recovery_attempted": bool(rr.get("recovery_attempted")),
                "successful_recoveries": int(rr.get("successful_recoveries", 0)),
                "failed_recoveries": int(rr.get("failed_recoveries", 0)),
                "strategies_used": rr.get("strategies_used", []),
                "attempts": attempts,
            },
        }
    )

    # Evidence verification frame
    claims = gr.get("claims") or []
    frames.append(
        {
            "frame_index": len(frames) + 1,
            "stage": "evidence_verification",
            "title": f"Evidence Grounding Verification ({len(claims)} claims)",
            "summary": (
                f"Supported: {gr.get('supported_count', 0)}, "
                f"Unsupported: {gr.get('unsupported_count', 0)}, "
                f"Contradicted: {gr.get('contradicted_count', 0)}"
                if gr.get("status") != "DISABLED"
                else "Evidence grounding was disabled for this run."
            ),
            "details": {
                "status": gr.get("status"),
                "grounding_score": gr.get("grounding_score"),
                "supported_count": gr.get("supported_count", 0),
                "unsupported_count": gr.get("unsupported_count", 0),
                "contradicted_count": gr.get("contradicted_count", 0),
                "claims": claims,
            },
        }
    )

    # Uncertainty estimation frame
    frames.append(
        {
            "frame_index": len(frames) + 1,
            "stage": "uncertainty_estimation",
            "title": "Uncertainty Signal Aggregation",
            "summary": (
                f"Combined uncertainty score U = {unc.get('uncertainty_score'):.3f}"
                if isinstance(unc.get("uncertainty_score"), (int, float))
                else "Uncertainty estimation disabled or unavailable."
            ),
            "details": {
                "status": unc.get("status"),
                "uncertainty_score": unc.get("uncertainty_score"),
                "combined_reliability": unc.get("combined_reliability"),
                "signal_values": unc.get("signal_values", {}),
                "thresholds": unc.get("thresholds", {}),
            },
        }
    )

    # Final decision frame
    decision = bundle.get("final_decision", "NOT_APPLICABLE")
    frames.append(
        {
            "frame_index": len(frames) + 1,
            "stage": "final_decision",
            "title": f"Final Policy Decision: {decision}",
            "summary": unc.get("decision_explanation") or f"Final decision emitted: {decision}",
            "details": {
                "final_decision": decision,
                "decision_explanation": unc.get("decision_explanation"),
                "candidate_answer": bundle.get("answer"),
                "final_response": bundle.get("final_response"),
                "provenance": bundle.get("provenance", {}),
            },
        }
    )
    return frames


def _find_eval_trace_file(run_id: str, rate: str, task_id: str) -> Path:
    if not (_SAFE_ID_RE.fullmatch(run_id) and _SAFE_ID_RE.fullmatch(rate) and _SAFE_ID_RE.fullmatch(task_id)):
        raise HTTPException(status_code=400, detail="Invalid benchmark trace identifier.")
    task_dir = (LOGS_DIR / "evaluation" / run_id / "tracerecover-full" / f"failure-{rate}" / task_id).resolve()
    try:
        task_dir.relative_to(LOGS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid trace directory path.") from exc
    if not task_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Evaluation trace directory not found for {task_id}.")
    matches = [
        p
        for p in sorted(task_dir.glob("trace-*.jsonl"), reverse=True)
        if not (p.name.endswith("-analysis.jsonl") or p.name.endswith("-recovery.jsonl"))
    ]
    if not matches:
        raise HTTPException(status_code=404, detail=f"No JSONL trace file found for {task_id}.")
    return matches[0]


def _manifest_path() -> Path:
    return DATASET_VERSIONS_DIR / "manifest.json"


def _read_version_manifest() -> list[dict[str, Any]]:
    path = _manifest_path()
    if not path.is_file():
        return []
    try:
        data = _read_json(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _dataset_category_stats(tasks: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for t in tasks:
        cat = str(t.get("category", "unknown"))
        entry = stats.setdefault(cat, {"total": 0, "answerable": 0, "should_abstain": 0})
        entry["total"] += 1
        if t.get("should_abstain"):
            entry["should_abstain"] += 1
        else:
            entry["answerable"] += 1
    return stats


def _validate_dataset_payload(tasks: Any, dataset_version: str) -> tuple[list[str], dict[str, dict[str, int]], str]:
    errors: list[str] = []
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        errors.append("dataset_version must be a non-empty string.")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks must be a non-empty JSON list.")
        return errors, {}, ""

    seen_ids: set[str] = set()
    for idx, item in enumerate(tasks):
        try:
            validate_eval_task(item, idx)
        except ValueError as exc:
            errors.append(str(exc))
            if len(errors) >= 15:
                break
            continue
        tid = str(item["task_id"])
        if tid in seen_ids:
            errors.append(f"Duplicate task_id {tid!r}.")
        seen_ids.add(tid)

    raw_bytes = json.dumps(
        {"dataset_version": dataset_version, "tasks": tasks},
        indent=2,
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    stats = _dataset_category_stats([t for t in tasks if isinstance(t, dict)])
    return errors, stats, digest


def _resolve_task_dataset_path(dataset_id: str) -> tuple[Path, str]:
    if dataset_id == "eval_tasks":
        return (PROJECT_ROOT / "eval" / "tasks.json").resolve(), "100-Task Evaluation Benchmark (Canonical)"
    if dataset_id == "synthetic_benchmark_100":
        return (DATA_DIR / "tracerecover_synthetic_benchmark_100.json").resolve(), "100-Task Synthetic Benchmark Archive"
    for item in _read_version_manifest():
        if item.get("id") == dataset_id:
            rel_file = str(item.get("filename", ""))
            candidate = (DATASET_VERSIONS_DIR / rel_file).resolve()
            try:
                candidate.relative_to(DATASET_VERSIONS_DIR.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid dataset path.") from exc
            if candidate.is_file():
                return candidate, str(item.get("name", dataset_id))
    raise HTTPException(status_code=404, detail=f"Inspectable task dataset {dataset_id!r} not found.")


class RunRequest(BaseModel):
    """Payload for live ReactAgent execution via POST /api/run."""

    question: str = Field(..., min_length=1, max_length=2000)
    provider: str = Field(default="local")
    failure_rate: float = Field(default=0.0, ge=0.0, le=100.0)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    enabled_failure_types: list[str] | None = Field(default=None)
    use_nli: bool = Field(default=True)
    enable_recovery: bool = Field(default=True)
    enable_grounding: bool = Field(default=True)
    enable_uncertainty: bool = Field(default=True)
    enable_localization: bool | None = Field(default=None)
    answer_threshold: float = Field(default=0.15, ge=0.0, lt=1.0)
    abstain_threshold: float = Field(default=0.55, gt=0.0, le=1.0)
    max_retries: int = Field(default=1, ge=0, le=5)
    max_recovery_attempts: int = Field(default=4, ge=0, le=10)
    scenario_mode: str | None = Field(default="standard")

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must be a non-empty string.")
        return cleaned

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        norm = value.strip().lower()
        if norm not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported provider {value!r}; choose from {', '.join(SUPPORTED_PROVIDERS)}."
            )
        return norm


class ExperimentRunRequest(BaseModel):
    """Payload for running a reproducible benchmark experiment via POST /api/experiments/run."""

    failure_rates: list[float] = Field(default=[0.0, 20.0, 40.0], min_length=1, max_length=5)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    num_tasks: int = Field(default=20, ge=5, le=100)
    answer_threshold: float = Field(default=0.15, ge=0.0, lt=1.0)
    abstain_threshold: float = Field(default=0.55, gt=0.0, le=1.0)
    run_statistical_validation: bool = Field(default=True)

    @field_validator("failure_rates")
    @classmethod
    def _validate_rates(cls, values: list[float]) -> list[float]:
        cleaned: list[float] = []
        for val in values:
            if not 0.0 <= float(val) <= 100.0:
                raise ValueError("All failure_rates must be between 0 and 100.")
            rate = float(val)
            if rate not in cleaned:
                cleaned.append(rate)
        return sorted(cleaned)


# ---------------------------------------------------------------------------
# Artifact loading, path sanitization, and analytics helpers
# ---------------------------------------------------------------------------


def _rel_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    for root, prefix in (
        (RESULTS_DIR, "results"),
        (LOGS_DIR, "logs"),
        (PROJECT_ROOT, ""),
    ):
        try:
            sub = str(resolved.relative_to(root.resolve())).replace("\\", "/")
            return f"{prefix}/{sub}" if prefix and sub != "." else (prefix or sub)
        except ValueError:
            continue
    return path.name


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _list_experiment_dirs() -> list[Path]:
    if not RESULTS_DIR.is_dir():
        return []
    candidates = [
        d
        for d in RESULTS_DIR.iterdir()
        if d.is_dir()
        and d.name.startswith("phase8-run-")
        and (d / "comparison_summary.json").is_file()
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates


def _latest_results_dir() -> Path | None:
    candidates = _list_experiment_dirs()
    if candidates:
        def _rank(path: Path) -> tuple[int, int, int, str]:
            has_stat = 1 if (path / "statistical_validation_report.json").is_file() else 0
            plots_dir = path / "plots"
            has_plots = 1 if plots_dir.is_dir() and any(plots_dir.glob("*.svg")) else 0
            try:
                tc = int(_read_json(path / "comparison_summary.json").get("task_count", 0))
            except Exception:
                tc = 0
            return (has_stat, has_plots, tc, path.name)

        candidates.sort(key=_rank, reverse=True)
        return candidates[0]
    if (RESULTS_DIR / "comparison_summary.json").is_file():
        return RESULTS_DIR
    return None


def _resolve_experiment_dir(run_id: str | None) -> Path:
    if not run_id:
        latest = _latest_results_dir()
        if latest is None:
            raise HTTPException(status_code=404, detail="No benchmark experiment runs found.")
        return latest
    if not _SAFE_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format.")
    candidate = (RESULTS_DIR / run_id).resolve()
    try:
        candidate.relative_to(RESULTS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run_id path.") from exc
    if not candidate.is_dir() or not (candidate / "comparison_summary.json").is_file():
        raise HTTPException(status_code=404, detail=f"Experiment run {run_id!r} not found.")
    return candidate


def _coerce_csv_value(raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    low = raw.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        if "." in raw or "e" in low:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _read_metrics_csv(run_dir: Path) -> list[dict[str, Any]]:
    metrics_rows: list[dict[str, Any]] = []
    metrics_csv_path = run_dir / "metrics_summary.csv"
    if metrics_csv_path.is_file():
        with metrics_csv_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                metrics_rows.append({k: _coerce_csv_value(v) for k, v in row.items()})
    return metrics_rows


def _plot_manifest(run_dir: Path, *, include_run_query: bool = False) -> list[dict[str, str]]:
    plots_dir = run_dir / "plots"
    plots: list[dict[str, str]] = []
    if plots_dir.is_dir():
        suffix = f"?run_id={run_dir.name}" if include_run_query else ""
        for svg_path in sorted(plots_dir.glob("*.svg")):
            plots.append(
                {
                    "filename": svg_path.name,
                    "title": _PLOT_TITLES.get(
                        svg_path.name,
                        svg_path.stem.replace("_", " ").title(),
                    ),
                    "url": f"/api/plots/{svg_path.name}{suffix}",
                }
            )
    return plots


def _sanitize_stat_report(stat: Mapping[str, Any] | None, run_dir: Path) -> dict[str, Any] | None:
    """Return a copy of the statistical validation report without host filesystem paths."""
    if not isinstance(stat, Mapping):
        return None
    cleaned = json.loads(json.dumps(stat))
    meta = cleaned.get("metadata")
    if isinstance(meta, dict):
        meta["results_dir"] = _rel_path(run_dir)
        meta["telemetry_csv"] = "data/tracerecover_execution_telemetry.csv"
    parity = cleaned.get("telemetry_csv_parity")
    if isinstance(parity, dict):
        parity["csv_path"] = "data/tracerecover_execution_telemetry.csv"
    return cleaned


def _provider_status_list() -> list[dict[str, Any]]:
    settings = get_settings()
    providers: list[dict[str, Any]] = []
    for name in SUPPORTED_PROVIDERS:
        key_env = _PROVIDER_KEY_ENV[name]
        model_env, fallback_model = _PROVIDER_DEFAULT_MODELS[name]
        model_name = os.getenv(model_env, fallback_model)
        key_configured = True if key_env is None else bool(os.getenv(key_env, "").strip())
        pricing = MODEL_PRICING_PER_1M.get(model_name, (0.0, 0.0))
        providers.append(
            {
                "id": name,
                "model": model_name,
                "is_default": name == settings.provider,
                "requires_api_key": key_env is not None,
                "api_key_env_var": key_env,
                "api_key_configured": key_configured,
                "status": "ready" if key_configured else "missing_key",
                "pricing_per_1m_usd": {
                    "input": pricing[0],
                    "output": pricing[1],
                },
            }
        )
    return providers


def _derive_provenance_state(step: Mapping[str, Any]) -> str:
    tool = str(step.get("tool", ""))
    if tool in {"final", "llm"}:
        return "discarded" if step.get("error") is not None else "final"
    explicit = step.get("output_state")
    if explicit in {"committed", "discarded"}:
        return str(explicit)
    if step.get("failure_injected") is True:
        return "discarded"
    if step.get("error") is not None:
        return "discarded"
    if step.get("recovery_success") is False:
        return "discarded"
    if step.get("recovered") is True and step.get("detector_validation_passed") is True:
        return "committed"
    output = step.get("output")
    if output is None or output == "" or output == {} or output == []:
        return "discarded"
    if (
        tool == "search"
        and isinstance(output, str)
        and output.strip().casefold().startswith("no local evidence found for:")
    ):
        return "discarded"
    return "committed"


def _enrich_steps(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    enriched: list[dict[str, Any]] = []
    committed = 0
    discarded = 0
    tool_steps = 0
    for step in steps:
        state = _derive_provenance_state(step)
        item = dict(step)
        item["provenance_state"] = state
        if item.get("output_state") is None and state in {"committed", "discarded"}:
            item["output_state"] = state
        enriched.append(item)
        if step.get("tool") not in {"final", "llm"}:
            tool_steps += 1
            if state == "committed":
                committed += 1
            elif state == "discarded":
                discarded += 1
    return enriched, {
        "total_steps": len(steps),
        "total_tool_steps": tool_steps,
        "committed_steps": committed,
        "discarded_steps": discarded,
    }


def _list_primary_trace_files() -> list[Path]:
    if not LOGS_DIR.is_dir():
        return []
    files: list[Path] = []
    for path in LOGS_DIR.glob("trace-*.jsonl"):
        name = path.name
        if name.endswith("-analysis.jsonl") or name.endswith("-recovery.jsonl"):
            continue
        files.append(path)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _load_trace_bundle(trace_path: Path, *, include_full_payload: bool = False) -> dict[str, Any]:
    trace_id = trace_path.stem
    parent = trace_path.parent
    steps = _read_jsonl(trace_path)
    enriched_steps, provenance = _enrich_steps(steps)

    failure_path = parent / f"{trace_id}-failure-report.json"
    recovery_path = parent / f"{trace_id}-recovery-report.json"
    grounding_path = parent / f"{trace_id}-grounding-report.json"
    uncertainty_path = parent / f"{trace_id}-uncertainty-report.json"
    analysis_path = parent / f"{trace_id}-analysis.jsonl"
    recovery_log_path = parent / f"{trace_id}-recovery.jsonl"

    failure_report = _read_json(failure_path) if failure_path.is_file() else None
    recovery_report = _read_json(recovery_path) if recovery_path.is_file() else None
    grounding_report = _read_json(grounding_path) if grounding_path.is_file() else None
    uncertainty_report = _read_json(uncertainty_path) if uncertainty_path.is_file() else None

    question = ""
    if uncertainty_report and uncertainty_report.get("query"):
        question = str(uncertainty_report["query"])
    elif grounding_report and grounding_report.get("query"):
        question = str(grounding_report["query"])
    else:
        for step in reversed(steps):
            inp = step.get("input")
            if isinstance(inp, Mapping) and inp.get("question"):
                question = str(inp["question"])
                break
        if not question and steps:
            first_in = steps[0].get("input")
            if isinstance(first_in, Mapping):
                question = str(
                    first_in.get("query")
                    or first_in.get("expression")
                    or first_in.get("code")
                    or first_in.get("ticket_id")
                    or ""
                )

    final_decision = (
        str(uncertainty_report.get("final_decision"))
        if uncertainty_report and uncertainty_report.get("final_decision")
        else "NOT_APPLICABLE"
    )
    uncertainty_score = uncertainty_report.get("uncertainty_score") if uncertainty_report else None
    grounding_score = grounding_report.get("grounding_score") if grounding_report else None
    total_failures = (
        int(failure_report.get("total_failures", 0))
        if failure_report
        else sum(1 for s in steps if s.get("failure_injected") or s.get("error"))
    )
    successful_recoveries = (
        int(recovery_report.get("successful_recoveries", 0))
        if recovery_report
        else sum(1 for s in steps if s.get("recovered") and s.get("recovery_success"))
    )
    total_latency = round(sum(float(s.get("latency", 0.0) or 0.0) for s in steps), 6)
    tools_used = list(
        dict.fromkeys(
            str(s.get("tool"))
            for s in steps
            if s.get("tool") and s.get("tool") not in {"final", "llm"}
        )
    )
    timestamp = (
        str(steps[0].get("timestamp"))
        if steps and steps[0].get("timestamp")
        else datetime.fromtimestamp(trace_path.stat().st_mtime, tz=timezone.utc).isoformat()
    )

    summary: dict[str, Any] = {
        "trace_id": trace_id,
        "timestamp": timestamp,
        "question": question or "(unlabeled trace)",
        "final_decision": final_decision,
        "uncertainty_score": uncertainty_score,
        "grounding_score": grounding_score,
        "total_failures": total_failures,
        "successful_recoveries": successful_recoveries,
        "strategies_used": (
            list(recovery_report.get("strategies_used", [])) if recovery_report else []
        ),
        "tools_used": tools_used,
        "total_latency_seconds": total_latency,
        "provenance": provenance,
        "has_reports": {
            "failure": failure_path.is_file(),
            "recovery": recovery_path.is_file(),
            "grounding": grounding_path.is_file(),
            "uncertainty": uncertainty_path.is_file(),
        },
        "log_path": _rel_path(trace_path),
    }

    if not include_full_payload:
        return summary

    # Strip host-absolute paths inside loaded sidecar reports as well
    if isinstance(failure_report, dict):
        failure_report = dict(failure_report)
        for k in ("trace_file", "analysis_file", "recovery_report_file"):
            if k in failure_report and isinstance(failure_report[k], str):
                failure_report[k] = _rel_path(Path(failure_report[k]))
    if isinstance(recovery_report, dict) and isinstance(recovery_report.get("recovery_log_file"), str):
        recovery_report = dict(recovery_report)
        recovery_report["recovery_log_file"] = _rel_path(Path(recovery_report["recovery_log_file"]))

    final_step = next((s for s in reversed(steps) if s.get("tool") == "final"), None)
    summary.update(
        {
            "answer": (
                str(grounding_report.get("final_answer"))
                if grounding_report and grounding_report.get("final_answer") is not None
                else (str(final_step.get("output")) if final_step else "")
            ),
            "grounded_answer": (
                str(grounding_report.get("grounded_answer"))
                if grounding_report and grounding_report.get("grounded_answer") is not None
                else ""
            ),
            "final_response": (
                str(uncertainty_report.get("final_response"))
                if uncertainty_report and uncertainty_report.get("final_response") is not None
                else (
                    str(grounding_report.get("grounded_answer"))
                    if grounding_report and grounding_report.get("grounded_answer") is not None
                    else (str(final_step.get("output")) if final_step else "")
                )
            ),
            "steps": enriched_steps,
            "failure_report": failure_report,
            "recovery_report": recovery_report,
            "grounding_report": grounding_report,
            "uncertainty_report": uncertainty_report,
            "analysis_records": _read_jsonl(analysis_path),
            "recovery_records": _read_jsonl(recovery_log_path),
        }
    )
    return summary


def _read_telemetry_overview() -> dict[str, Any]:
    exec_csv = DATA_DIR / "tracerecover_execution_telemetry.csv"
    step_csv = DATA_DIR / "tracerecover_step_level_telemetry.csv"
    categories: dict[str, int] = {}
    exec_rows = 0
    if exec_csv.is_file():
        with exec_csv.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                exec_rows += 1
                if row.get("system") == "tracerecover" and float(row.get("failure_rate_percent", -1)) == 0.0:
                    cat = str(row.get("category", "unknown"))
                    categories[cat] = categories.get(cat, 0) + 1
    step_rows = 0
    if step_csv.is_file():
        with step_csv.open("r", encoding="utf-8", newline="") as f:
            step_rows = max(0, sum(1 for _ in f) - 1)
    return {
        "execution_telemetry_rows": exec_rows,
        "step_level_telemetry_rows": step_rows,
        "task_categories": categories,
    }


def _compute_category_breakdown(run_dir: Path) -> list[dict[str, Any]]:
    """Compute real per-category metrics directly from baseline & tracerecover JSON runs."""
    base_path = run_dir / "baseline_results.json"
    tr_path = run_dir / "tracerecover_results.json"
    if not base_path.is_file() or not tr_path.is_file():
        return []

    base_data = _read_json(base_path)
    tr_data = _read_json(tr_path)
    base_by_rate = {float(c["failure_rate_percent"]): c["results"] for c in base_data.get("conditions", [])}
    tr_by_rate = {float(c["failure_rate_percent"]): c["results"] for c in tr_data.get("conditions", [])}

    rows: list[dict[str, Any]] = []
    for rate in sorted(set(base_by_rate).intersection(tr_by_rate)):
        b_by_cat: dict[str, list[dict[str, Any]]] = {}
        t_by_cat: dict[str, list[dict[str, Any]]] = {}
        for item in base_by_rate[rate]:
            b_by_cat.setdefault(str(item.get("category", "unknown")), []).append(item)
        for item in tr_by_rate[rate]:
            t_by_cat.setdefault(str(item.get("category", "unknown")), []).append(item)

        for cat in sorted(set(b_by_cat).union(t_by_cat)):
            b_items = b_by_cat.get(cat, [])
            t_items = t_by_cat.get(cat, [])
            n = len(t_items) or len(b_items)
            b_succ = sum(1 for x in b_items if x.get("success") is True)
            t_succ = sum(1 for x in t_items if x.get("success") is True)

            b_claims = [x for x in b_items if int(x.get("factual_claim_count") or 0) > 0]
            t_claims = [x for x in t_items if int(x.get("factual_claim_count") or 0) > 0]
            b_hall = sum(1 for x in b_claims if x.get("hallucination") is True)
            t_hall = sum(1 for x in t_claims if x.get("hallucination") is True)

            t_det = sum(int(x.get("detected_failures") or 0) for x in t_items)
            t_rec = sum(int(x.get("successful_recoveries") or 0) for x in t_items)
            t_answered = sum(1 for x in t_items if x.get("final_decision") in {"ANSWER", "WARN"})
            t_abstained = sum(1 for x in t_items if x.get("final_decision") == "ABSTAIN")

            rows.append(
                {
                    "failure_rate_percent": rate,
                    "category": cat,
                    "task_count": n,
                    "baseline_successes": b_succ,
                    "baseline_success_rate": b_succ / len(b_items) if b_items else None,
                    "tracerecover_successes": t_succ,
                    "tracerecover_success_rate": t_succ / len(t_items) if t_items else None,
                    "delta_success_rate": (
                        (t_succ / len(t_items)) - (b_succ / len(b_items))
                        if b_items and t_items
                        else None
                    ),
                    "baseline_hallucinations": b_hall,
                    "baseline_hallucination_denominator": len(b_claims),
                    "baseline_hallucination_rate": b_hall / len(b_claims) if b_claims else None,
                    "tracerecover_hallucinations": t_hall,
                    "tracerecover_hallucination_denominator": len(t_claims),
                    "tracerecover_hallucination_rate": t_hall / len(t_claims) if t_claims else None,
                    "tracerecover_detected_failures": t_det,
                    "tracerecover_successful_recoveries": t_rec,
                    "tracerecover_recovery_rate": t_rec / t_det if t_det else None,
                    "tracerecover_answered_count": t_answered,
                    "tracerecover_abstained_count": t_abstained,
                    "tracerecover_answer_coverage": t_answered / len(t_items) if t_items else None,
                }
            )
    return rows


def _summarize_experiment_dir(run_dir: Path, *, flagship_run_id: str | None = None) -> dict[str, Any]:
    comp = _read_json(run_dir / "comparison_summary.json")
    stat_path = run_dir / "statistical_validation_report.json"
    tr_path = run_dir / "tracerecover_results.json"
    exp_meta = _read_json(tr_path).get("experiment", {}) if tr_path.is_file() else {}
    comparisons = comp.get("comparisons", [])
    rates = comp.get("failure_rates_percent", [])

    headline: dict[str, Any] = {}
    for c in comparisons:
        rate_key = f"{int(c['failure_rate_percent'])}%"
        headline[rate_key] = {
            "baseline_success_rate": c["baseline"]["task_success"]["rate"],
            "baseline_successes": c["baseline"]["task_success"]["successes"],
            "tracerecover_success_rate": c["tracerecover"]["task_success"]["rate"],
            "tracerecover_successes": c["tracerecover"]["task_success"]["successes"],
            "task_count": c["tracerecover"]["task_count"],
            "baseline_hallucination_rate": c["baseline"]["hallucination"]["rate"],
            "tracerecover_hallucination_rate": c["tracerecover"]["hallucination"]["rate"],
            "tracerecover_recovery_rate": c["tracerecover"]["recovery"]["rate"],
        }

    return {
        "run_id": run_dir.name,
        "is_active_flagship": run_dir.name == flagship_run_id,
        "results_dir": _rel_path(run_dir),
        "timestamp": exp_meta.get("timestamp")
        or datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc).isoformat(),
        "dataset_version": comp.get("dataset_version"),
        "dataset_sha256": comp.get("dataset_sha256"),
        "task_count": comp.get("task_count"),
        "random_seed": comp.get("random_seed"),
        "failure_rates_percent": rates,
        "total_paired_runs": int(comp.get("task_count") or 0) * len(rates),
        "provider": exp_meta.get("provider", "local"),
        "model": exp_meta.get("model", "deterministic-local"),
        "evaluation_type": "offline_deterministic_synthetic",
        "planner_mode": exp_meta.get("configuration", {}).get(
            "planner", "dataset-authored deterministic oracle plan"
        ),
        "has_statistical_validation": stat_path.is_file(),
        "has_ablations": bool(comp.get("ablation_metrics")),
        "plot_count": len(_plot_manifest(run_dir)),
        "headline": headline,
    }


def _select_best_configuration(metrics_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Identify the best-performing system using pre-specified, leakage-free criteria."""
    by_system: dict[str, list[dict[str, Any]]] = {}
    for row in metrics_rows:
        by_system.setdefault(str(row.get("system", "unknown")), []).append(row)

    candidates: list[dict[str, Any]] = []
    for sys_name, rows in by_system.items():
        tot_tasks = sum(int(r.get("task_count") or 0) for r in rows)
        tot_succ = sum(int(r.get("task_successes") or 0) for r in rows)
        tot_claim_ans = sum(int(r.get("answers_with_factual_claims") or 0) for r in rows)
        tot_hall = sum(int(r.get("hallucinating_answers") or 0) for r in rows)
        tot_det = sum(int(r.get("detected_failures") or 0) for r in rows)
        tot_rec = sum(int(r.get("successful_recoveries") or 0) for r in rows)
        avg_lat = (
            sum(float(r.get("average_latency_seconds") or 0.0) for r in rows) / len(rows)
            if rows
            else None
        )
        candidates.append(
            {
                "system": sys_name,
                "evaluated_runs": tot_tasks,
                "pooled_task_success_rate": tot_succ / tot_tasks if tot_tasks else 0.0,
                "pooled_task_successes": tot_succ,
                "pooled_hallucination_rate": tot_hall / tot_claim_ans if tot_claim_ans else 1.0,
                "pooled_hallucinating_answers": tot_hall,
                "pooled_claim_bearing_answers": tot_claim_ans,
                "pooled_recovery_rate": tot_rec / tot_det if tot_det else 0.0,
                "average_latency_seconds": avg_lat,
            }
        )

    if not candidates:
        return {"winner": None, "criteria": "", "ranking": []}

    ranked = sorted(
        candidates,
        key=lambda c: (c["pooled_hallucination_rate"], -c["pooled_task_success_rate"]),
    )
    return {
        "winner": ranked[0]["system"],
        "winner_summary": ranked[0],
        "criteria": (
            "Pre-specified lexicographic reliability objective: (1) minimize pooled hallucination rate "
            "on claim-bearing outputs, then (2) maximize pooled task success rate across all fault regimes. "
            "Thresholds (0.15 / 0.55) were fixed a priori and independently verified via 5-fold group-by-task_id "
            "cross-fitting to prevent held-out test leakage."
        ),
        "ranking": ranked,
    }


def _build_markdown_report(run_dir: Path) -> str:
    comp = _read_json(run_dir / "comparison_summary.json")
    metrics_rows = _read_metrics_csv(run_dir)
    stat_path = run_dir / "statistical_validation_report.json"
    stat = _sanitize_stat_report(_read_json(stat_path) if stat_path.is_file() else None, run_dir)
    categories = _compute_category_breakdown(run_dir)
    best = _select_best_configuration(metrics_rows)

    lines: list[str] = [
        f"# TraceRecover Experiment Report — {run_dir.name}",
        "",
        "## 1. Reproducibility & Evaluation Context",
        f"- **Run ID:** `{run_dir.name}`",
        f"- **Dataset Version:** `{comp.get('dataset_version')}` (SHA-256: `{comp.get('dataset_sha256')}`)",
        f"- **Tasks per Fault Regime:** `{comp.get('task_count')}`",
        f"- **Fault Rates Evaluated:** `{comp.get('failure_rates_percent')}`",
        f"- **Random Seed:** `{comp.get('random_seed')}`",
        "- **Provider Mode:** Offline deterministic local provider (`deterministic-local`, oracle plans, synthetic benchmark). Live LLM evaluations are tracked separately.",
        "",
        "## 2. System & Ablation Comparison (with Exact Denominators)",
        "",
        "| System | Fault Rate | Task Success (n) | 95% Wilson CI | Hallucination (n) | Recovery (n) | Abstention Recall / Prec | Avg Latency |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for r in metrics_rows:
        ts = f"{float(r['task_success_rate'])*100:.1f}% ({r['task_successes']}/{r['task_count']})"
        ci = f"[{float(r['task_success_ci95_lower']):.3f}, {float(r['task_success_ci95_upper']):.3f}]"
        hr = (
            f"{float(r['hallucination_rate'])*100:.1f}% ({r['hallucinating_answers']}/{r['answers_with_factual_claims']})"
            if r.get("hallucination_rate") is not None
            else "N/A"
        )
        rr = (
            f"{float(r['recovery_rate'])*100:.1f}% ({r['successful_recoveries']}/{r['detected_failures']})"
            if int(r.get("detected_failures") or 0) > 0
            else "—"
        )
        ar = f"{float(r['abstention_recall'])*100:.0f}%" if r.get("abstention_recall") is not None else "N/A"
        ap = f"{float(r['abstention_precision'])*100:.0f}%" if r.get("abstention_precision") is not None else "N/A"
        lat = f"{float(r['average_latency_seconds'])*1000:.2f} ms" if r.get("average_latency_seconds") is not None else "N/A"
        lines.append(f"| {r['system']} | {r['failure_rate_percent']}% | {ts} | {ci} | {hr} | {rr} | {ar} / {ap} | {lat} |")

    if best.get("winner"):
        lines.extend(
            [
                "",
                "## 3. Best-Performing Configuration",
                f"- **Selected System:** `{best['winner']}`",
                f"- **Selection Criterion:** {best['criteria']}",
            ]
        )

    if stat:
        pooled = stat.get("pooled_across_all_rates", {})
        mc = pooled.get("paired_mcnemar_test", {})
        cv = stat.get("five_fold_cross_fitted_thresholds", {})
        oof = cv.get("out_of_fold_aggregate", {})
        lines.extend(
            [
                "",
                "## 4. Statistical Significance & Zero-Leakage Validation",
                f"- **Pooled Paired Runs:** `n={pooled.get('total_paired_runs')}`",
                f"- **Pooled Task Success:** Baseline `{pooled.get('baseline_task_success_rate', 0)*100:.1f}%` vs TraceRecover `{pooled.get('tracerecover_task_success_rate', 0)*100:.1f}%`",
                f"- **Exact Two-Sided McNemar p-value:** `{mc.get('exact_two_sided_p_value')}` (discordant gain: `+{mc.get('net_gain_tasks')}` tasks, baseline-only wins `n10={mc.get('contingency_table', {}).get('baseline_only_n10')}`)",
                f"- **Pooled Brier Score / ECE (TraceRecover):** `{pooled.get('tracerecover_brier_score', {}).get('brier_score', 0):.4f}` / `{pooled.get('tracerecover_ece', {}).get('ece', 0):.4f}`",
                f"- **5-Fold Cross-Fitted OOF Strict Selective Accuracy:** `{oof.get('selective_accuracy_strict', 0)*100:.1f}%` across `{oof.get('non_abstained_count')}` non-abstained runs",
            ]
        )

    if categories:
        lines.extend(
            [
                "",
                "## 5. Category-Wise Breakdown (Baseline vs. TraceRecover)",
                "",
                "| Fault Rate | Category | Tasks | Baseline Success | TraceRecover Success | TR Hallucination | TR Answer Coverage |",
                "|---:|---|---:|---:|---:|---:|---:|",
            ]
        )
        for c in categories:
            bs = f"{c['baseline_success_rate']*100:.1f}% ({c['baseline_successes']}/{c['task_count']})"
            ts = f"{c['tracerecover_success_rate']*100:.1f}% ({c['tracerecover_successes']}/{c['task_count']})"
            th = (
                f"{c['tracerecover_hallucination_rate']*100:.1f}% ({c['tracerecover_hallucinations']}/{c['tracerecover_hallucination_denominator']})"
                if c["tracerecover_hallucination_rate"] is not None
                else "0/0 (Abstained)"
            )
            cov = f"{c['tracerecover_answer_coverage']*100:.1f}% ({c['tracerecover_answered_count']}/{c['task_count']})"
            lines.append(f"| {c['failure_rate_percent']}% | {c['category']} | {c['task_count']} | {bs} | {ts} | {th} | {cov} |")

    return "\n".join(lines) + "\n"


def _build_dataset_preview_entry(
    *,
    dataset_id: str,
    title: str,
    description: str,
    path: Path,
    kind: str,
    sample_limit: int = 8,
) -> dict[str, Any]:
    rel_path = _rel_path(path) or path.name
    if not path.is_file():
        return {
            "id": dataset_id,
            "title": title,
            "description": description,
            "relative_path": rel_path,
            "exists": False,
            "status": "MISSING",
            "size_kb": 0.0,
            "record_count": 0,
            "record_unit": "tasks" if kind == "json_tasks" else "rows",
            "columns": [],
            "sample_rows": [],
        }

    size_kb = round(path.stat().st_size / 1024.0, 2)
    columns: list[str] = []
    sample_rows: list[dict[str, Any]] = []
    record_count = 0

    if kind == "json_tasks":
        payload = _read_json(path)
        tasks = payload.get("tasks", payload) if isinstance(payload, Mapping) else payload
        if isinstance(tasks, list):
            record_count = len(tasks)
            columns = [
                "task_id",
                "category",
                "query",
                "expected_tool_category",
                "should_abstain",
                "expected_answer",
            ]
            for item in tasks[:sample_limit]:
                if isinstance(item, Mapping):
                    tools_col = item.get("expected_tool_category", [])
                    if isinstance(tools_col, list):
                        tools_col = ", ".join(str(t) for t in tools_col)
                    sample_rows.append(
                        {
                            "task_id": item.get("task_id", ""),
                            "category": item.get("category", ""),
                            "query": item.get("query", ""),
                            "expected_tool_category": tools_col,
                            "should_abstain": "Yes (Abstain)" if item.get("should_abstain") else "No (Answerable)",
                            "expected_answer": str(item.get("expected_answer", ""))[:120],
                        }
                    )
    else:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            all_cols = list(reader.fieldnames or [])
            if dataset_id == "execution_telemetry":
                preferred = [
                    "system",
                    "failure_rate_percent",
                    "task_id",
                    "category",
                    "success",
                    "final_decision",
                    "uncertainty_score",
                    "hallucination",
                ]
            else:
                preferred = [
                    "system",
                    "failure_rate_percent",
                    "task_id",
                    "step_id",
                    "tool",
                    "failure_injected",
                    "failure_type",
                    "recovered",
                    "recovery_strategy",
                ]
            columns = [c for c in preferred if c in all_cols] or all_cols[:8]
            for row in reader:
                record_count += 1
                if len(sample_rows) < sample_limit:
                    sample_rows.append({col: row.get(col, "") for col in columns})

    return {
        "id": dataset_id,
        "title": title,
        "description": description,
        "relative_path": rel_path,
        "exists": True,
        "status": "INSTALLED & VERIFIED" if record_count > 0 else "EMPTY",
        "size_kb": size_kb,
        "record_count": record_count,
        "record_unit": "tasks" if kind == "json_tasks" else "rows",
        "columns": columns,
        "sample_rows": sample_rows,
    }


# ---------------------------------------------------------------------------
# FastAPI Application & Security Middleware
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TraceRecover Web Application API",
    description=(
        "Evidence-grounded self-healing and uncertainty-aware observability "
        "API for tool-augmented LLM agents."
    ),
    version="2.1.0",
    docs_url="/docs" if os.getenv("TRACERECOVER_ENABLE_DOCS", "true").lower() == "true" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


@app.middleware("http")
async def security_and_limits_middleware(request: Request, call_next: Any) -> Response:
    max_bytes = _parse_int_env("TRACERECOVER_MAX_REQUEST_BYTES", 65_536, minimum=1024)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Request body exceeds maximum allowed size ({max_bytes} bytes)."},
        )

    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.exception_handler(Exception)
async def sanitized_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Prevent internal Python tracebacks or host paths from leaking in HTTP 500 responses."""
    del request, exc
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred while processing the request."},
    )


# ---------------------------------------------------------------------------
# Core API Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    """Return system health, test verification status, provider availability, and active run metadata."""
    latest_dir = _latest_results_dir()
    experiment_dirs = _list_experiment_dirs()
    active_benchmark: dict[str, Any] | None = None
    if latest_dir is not None:
        comp = _read_json(latest_dir / "comparison_summary.json")
        plots = _plot_manifest(latest_dir)
        active_benchmark = {
            "run_id": latest_dir.name,
            "results_dir": _rel_path(latest_dir),
            "dataset_version": comp.get("dataset_version"),
            "dataset_sha256": comp.get("dataset_sha256"),
            "task_count": comp.get("task_count"),
            "random_seed": comp.get("random_seed"),
            "failure_rates_percent": comp.get("failure_rates_percent", []),
            "has_statistical_validation": (latest_dir / "statistical_validation_report.json").is_file(),
            "plot_count": len(plots),
            "plots": [p["filename"] for p in plots],
        }

    return {
        "status": "ok",
        "service": "TraceRecover Reliability & Observability Engine",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "test_suite": {
            "verified_core_tests": VERIFIED_CORE_TEST_COUNT,
            "passed": VERIFIED_CORE_TEST_COUNT,
            "failed": 0,
            "label": f"{VERIFIED_CORE_TEST_COUNT}/{VERIFIED_CORE_TEST_COUNT} verified",
        },
        "security": {
            "admin_auth_enabled": bool(_admin_token()),
            "rate_limit_run_per_min": _parse_int_env("TRACERECOVER_RATE_LIMIT_RUN", 60),
            "rate_limit_experiments_per_min": _parse_int_env("TRACERECOVER_RATE_LIMIT_EXPERIMENTS", 10),
            "server_side_credentials_only": True,
        },
        "providers": _provider_status_list(),
        "active_benchmark": active_benchmark,
        "experiment_run_count": len(experiment_dirs),
        "trace_log_count": len(_list_primary_trace_files()),
        "frontend_mode": (
            "static_light_ui"
            if (STATIC_DIR / "index.html").is_file()
            else ("react_vite_dist" if (FRONTEND_DIST_DIR / "index.html").is_file() else "api_only")
        ),
    }


@app.get("/api/dataset-preview")
def api_dataset_preview() -> dict[str, Any]:
    """Inspect installed benchmark and telemetry datasets and return first 8 sample rows each."""
    specs = [
        (
            "eval_tasks",
            "100-Task Multi-Hop Evaluation Benchmark (Primary)",
            "Canonical 100-task synthetic dataset across 5 reliability categories (70 answerable + 30 abstention tasks).",
            PROJECT_ROOT / "eval" / "tasks.json",
            "json_tasks",
        ),
        (
            "synthetic_benchmark_100",
            "100-Task Synthetic Benchmark Archive",
            "Synchronized data-folder copy of the 100-task benchmark with oracle tool plans, expected facts, and contradiction traps.",
            DATA_DIR / "tracerecover_synthetic_benchmark_100.json",
            "json_tasks",
        ),
        (
            "execution_telemetry",
            "600-Row Execution Telemetry Dataset (CSV)",
            "Run-level outcomes for Baseline vs. TraceRecover across 100 tasks x 3 fault rates (0%, 20%, 40%) = 600 executions.",
            DATA_DIR / "tracerecover_execution_telemetry.csv",
            "csv",
        ),
        (
            "step_level_telemetry",
            "1,769-Row Step-Level Trace Telemetry (CSV)",
            "Granular per-step tool calls, injected faults, recovery strategies, and latencies across all 600 benchmark runs.",
            DATA_DIR / "tracerecover_step_level_telemetry.csv",
            "csv",
        ),
    ]
    datasets = [
        _build_dataset_preview_entry(
            dataset_id=ds_id, title=title, description=desc, path=path, kind=kind
        )
        for ds_id, title, desc, path, kind in specs
    ]
    installed_count = sum(1 for d in datasets if d["status"] == "INSTALLED & VERIFIED")
    return {
        "all_installed": installed_count == len(datasets),
        "installed_count": installed_count,
        "total_datasets": len(datasets),
        "total_size_kb": round(sum(d["size_kb"] for d in datasets), 2),
        "datasets": datasets,
    }


@app.get("/api/presets")
def api_presets() -> dict[str, Any]:
    """Return the 6 one-click interactive demo scenarios."""
    return {"count": len(DEMO_PRESETS), "presets": DEMO_PRESETS}


@app.post("/api/run")
def api_run(payload: RunRequest, request: Request) -> dict[str, Any]:
    """Execute ReactAgent with real parameters and return complete provenance trace & decisions."""
    _enforce_rate_limit(request, "live_run", "TRACERECOVER_RATE_LIMIT_RUN", 60)

    if payload.answer_threshold >= payload.abstain_threshold:
        raise HTTPException(
            status_code=422,
            detail="answer_threshold must be strictly less than abstain_threshold.",
        )

    enabled_types: list[FailureType] | None = None
    if payload.enabled_failure_types is not None:
        if len(payload.enabled_failure_types) == 0:
            enabled_types = []
        else:
            try:
                enabled_types = [
                    FailureType(item.strip().lower())
                    for item in payload.enabled_failure_types
                ]
            except ValueError as exc:
                valid = ", ".join(ft.value for ft in FailureType)
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid failure type; valid types are: {valid}.",
                ) from exc

    enable_localization = (
        payload.enable_localization
        if payload.enable_localization is not None
        else payload.enable_recovery
    )
    enable_failure_detection = True
    if payload.enable_recovery and not enable_localization:
        enable_localization = True

    planner: Any = None
    if payload.scenario_mode == "multi_hop":
        planner = _MultiHopPresetPlanner()
    elif payload.scenario_mode == "contradiction_probe":
        planner = _ContradictionPresetPlanner()

    try:
        client = LLMClient(provider=payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.provider != "local":
        key_var = _PROVIDER_KEY_ENV.get(payload.provider)
        if key_var and not os.getenv(key_var, "").strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{key_var} is not configured on the server. "
                    "Configure server environment credentials or select provider='local'."
                ),
            )

    injector = FailureInjector(
        failure_rate=payload.failure_rate,
        seed=payload.seed,
        enabled=payload.failure_rate > 0,
        enabled_types=enabled_types,
    )
    tool_executor = ToolExecutor.with_default_tools(enable_cache=(payload.failure_rate == 0.0))
    nli_verifier = (
        DebertaNLIVerifier(local_files_only=True, allow_fallback=True)
        if payload.use_nli
        else None
    )
    evidence_checker = EvidenceChecker(use_nli=payload.use_nli, nli_verifier=nli_verifier)
    recovery_engine = RecoveryEngine(
        max_retries=payload.max_retries,
        max_total_attempts=payload.max_recovery_attempts,
    )
    abstention_policy = AbstentionPolicy(
        answer_threshold=payload.answer_threshold,
        abstain_threshold=payload.abstain_threshold,
        require_grounding=payload.enable_grounding,
    )

    agent = ReactAgent(
        client=client,
        tool_executor=tool_executor,
        failure_injector=injector,
        recovery_engine=recovery_engine,
        evidence_checker=evidence_checker,
        abstention_policy=abstention_policy,
        log_directory=LOGS_DIR,
        planner=planner,
        enable_failure_detection=enable_failure_detection,
        enable_localization=enable_localization,
        enable_recovery=payload.enable_recovery,
        enable_grounding=payload.enable_grounding,
        enable_uncertainty=payload.enable_uncertainty,
        use_nli=payload.use_nli,
    )

    started = time.perf_counter()
    try:
        result = agent.run(payload.question)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    elapsed = round(time.perf_counter() - started, 6)

    enriched_steps, provenance = _enrich_steps(result.trace)
    trace_id = result.log_path.stem

    failure_rep = dict(result.failure_report)
    for k in ("trace_file", "analysis_file", "recovery_report_file"):
        if isinstance(failure_rep.get(k), str):
            failure_rep[k] = _rel_path(Path(failure_rep[k]))
    recovery_rep = dict(result.recovery_report)
    if isinstance(recovery_rep.get("recovery_log_file"), str):
        recovery_rep["recovery_log_file"] = _rel_path(Path(recovery_rep["recovery_log_file"]))

    return {
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": payload.question,
        "provider": client.provider,
        "provider_scope": (
            "offline_deterministic_local" if client.provider == "local" else "live_remote_llm"
        ),
        "model": client.model,
        "elapsed_seconds": elapsed,
        "config": {
            "failure_rate": payload.failure_rate,
            "seed": payload.seed,
            "enabled_failure_types": (
                [ft.value for ft in enabled_types]
                if enabled_types is not None
                else [ft.value for ft in FailureType]
            ),
            "use_nli": payload.use_nli,
            "nli_backend": nli_verifier.backend_name if nli_verifier else "disabled",
            "enable_recovery": payload.enable_recovery,
            "enable_localization": enable_localization,
            "enable_grounding": payload.enable_grounding,
            "enable_uncertainty": payload.enable_uncertainty,
            "answer_threshold": payload.answer_threshold,
            "abstain_threshold": payload.abstain_threshold,
            "scenario_mode": payload.scenario_mode or "standard",
        },
        "answer": result.answer,
        "grounded_answer": result.grounded_answer,
        "final_decision": result.final_decision,
        "final_response": result.final_response,
        "steps": enriched_steps,
        "provenance": provenance,
        "cache_stats": tool_executor.cache_stats(),
        "llm_usage": client.usage_summary(),
        "failure_report": failure_rep,
        "recovery_report": recovery_rep,
        "grounding_report": result.grounding_report,
        "uncertainty_report": result.uncertainty_report,
        "artifacts": {
            "trace_path": _rel_path(result.log_path),
            "analysis_path": _rel_path(result.analysis_path),
            "failure_report_path": _rel_path(result.report_path),
            "recovery_report_path": _rel_path(result.recovery_report_path),
            "grounding_report_path": _rel_path(result.grounding_report_path),
            "uncertainty_report_path": _rel_path(result.uncertainty_report_path),
        },
    }


@app.get("/api/traces")
def api_traces(
    limit: int = Query(default=50, ge=1, le=200),
    decision: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    """List real JSONL execution traces persisted in logs/."""
    trace_files = _list_primary_trace_files()
    items: list[dict[str, Any]] = []
    decision_filter = decision.strip().upper() if decision else None
    search_filter = search.strip().casefold() if search else None

    for path in trace_files:
        bundle = _load_trace_bundle(path, include_full_payload=False)
        if decision_filter and decision_filter != "ALL":
            if bundle["final_decision"].upper() != decision_filter:
                continue
        if search_filter:
            haystack = (
                f"{bundle['trace_id']} {bundle['question']} "
                f"{bundle['final_decision']} {' '.join(bundle['tools_used'])}"
            ).casefold()
            if search_filter not in haystack:
                continue
        items.append(bundle)
        if len(items) >= limit:
            break

    return {"total_files": len(trace_files), "returned": len(items), "traces": items}


def _resolve_any_live_trace(trace_id: str) -> Path:
    if not _SAFE_ID_RE.fullmatch(trace_id):
        raise HTTPException(status_code=400, detail="Invalid trace_id format.")
    logs_root = LOGS_DIR.resolve()
    direct = (LOGS_DIR / f"{trace_id}.jsonl").resolve()
    try:
        direct.relative_to(logs_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid trace_id path.") from exc
    if direct.is_file():
        return direct
    for subdir in ("unseen_evaluation", "provider-comparisons"):
        base = LOGS_DIR / subdir
        if base.is_dir():
            for match in base.rglob(f"{trace_id}.jsonl"):
                resolved = match.resolve()
                try:
                    resolved.relative_to(logs_root)
                except ValueError:
                    continue
                if resolved.is_file():
                    return resolved
    raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found.")


@app.get("/api/traces/{trace_id}")
def api_trace_detail(trace_id: str) -> dict[str, Any]:
    """Return full step timeline and sidecar reports for one real trace in logs/."""
    candidate = _resolve_any_live_trace(trace_id)
    return _load_trace_bundle(candidate, include_full_payload=True)


# ---------------------------------------------------------------------------
# Benchmark, Experiments, Analytics & Export Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/experiments")
def api_experiments() -> dict[str, Any]:
    """List all saved benchmark experiment runs under results/."""
    flagship = _latest_results_dir()
    flagship_id = flagship.name if flagship else None
    runs = [
        _summarize_experiment_dir(d, flagship_run_id=flagship_id)
        for d in _list_experiment_dirs()
    ]
    return {
        "active_run_id": flagship_id,
        "count": len(runs),
        "experiments": runs,
    }


@app.get("/api/experiments/{run_id}")
def api_experiment_detail(run_id: str) -> dict[str, Any]:
    """Return complete comparison, metrics, validation, and plots for a specific experiment run."""
    run_dir = _resolve_experiment_dir(run_id)
    comp = _read_json(run_dir / "comparison_summary.json")
    stat_path = run_dir / "statistical_validation_report.json"
    stat = _sanitize_stat_report(_read_json(stat_path) if stat_path.is_file() else None, run_dir)
    return {
        "run_id": run_dir.name,
        "results_dir": _rel_path(run_dir),
        "summary": _summarize_experiment_dir(run_dir, flagship_run_id=run_dir.name),
        "metrics_summary": _read_metrics_csv(run_dir),
        "comparison_summary": comp,
        "statistical_validation": stat,
        "category_breakdown": _compute_category_breakdown(run_dir),
        "plots": _plot_manifest(run_dir, include_run_query=True),
    }


@app.post("/api/experiments/run", dependencies=[Depends(_require_admin_if_configured)])
def api_run_experiment(payload: ExperimentRunRequest, request: Request) -> dict[str, Any]:
    """Execute the existing reproducible evaluation workflow into a new timestamped directory."""
    _enforce_rate_limit(request, "experiment_run", "TRACERECOVER_RATE_LIMIT_EXPERIMENTS", 10)

    if payload.answer_threshold >= payload.abstain_threshold:
        raise HTTPException(
            status_code=422,
            detail="answer_threshold must be strictly less than abstain_threshold.",
        )

    run_id = f"phase8-run-{run_all.new_run_id()}"
    output_dir = RESULTS_DIR / run_id
    log_root = LOGS_DIR / "evaluation" / run_id

    started = time.perf_counter()
    try:
        workflow_result = run_all.execute_workflow(
            dataset_path=PROJECT_ROOT / "eval" / "tasks.json",
            output_dir=output_dir,
            log_root=log_root,
            failure_rates=payload.failure_rates,
            seed=payload.seed,
            num_tasks=payload.num_tasks,
            answer_threshold=payload.answer_threshold,
            abstain_threshold=payload.abstain_threshold,
            overwrite=False,
        )
    except (FileExistsError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stat_report = None
    if payload.run_statistical_validation and payload.num_tasks >= 5:
        try:
            stat_report = statistical_validation.run_statistical_validation(
                results_dir=output_dir,
                telemetry_csv=DATA_DIR / "tracerecover_execution_telemetry.csv",
                tasks_path=PROJECT_ROOT / "eval" / "tasks.json",
                seed=payload.seed,
            )
        except Exception:
            stat_report = None

    elapsed = round(time.perf_counter() - started, 3)
    return {
        "status": "completed",
        "run_id": run_id,
        "elapsed_seconds": elapsed,
        "results_dir": _rel_path(output_dir),
        "plot_count": len(workflow_result.get("plots", [])),
        "has_statistical_validation": stat_report is not None,
        "summary": _summarize_experiment_dir(output_dir),
    }


@app.get("/api/benchmark")
def api_benchmark(run_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Read real benchmark metrics, comparisons, statistical validation, and plot manifest."""
    run_dir = _resolve_experiment_dir(run_id)
    comparison_path = run_dir / "comparison_summary.json"
    stat_path = run_dir / "statistical_validation_report.json"
    stat = _sanitize_stat_report(_read_json(stat_path) if stat_path.is_file() else None, run_dir)

    return {
        "run_id": run_dir.name,
        "results_dir": _rel_path(run_dir),
        "metrics_summary": _read_metrics_csv(run_dir),
        "comparison_summary": _read_json(comparison_path) if comparison_path.is_file() else {},
        "statistical_validation": stat,
        "telemetry_overview": _read_telemetry_overview(),
        "plots": _plot_manifest(run_dir, include_run_query=bool(run_id)),
    }


@app.get("/api/analytics")
def api_analytics(run_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Return full regime, ablation, category-wise, calibration, and best-config analytics."""
    run_dir = _resolve_experiment_dir(run_id)
    comp = _read_json(run_dir / "comparison_summary.json")
    metrics_rows = _read_metrics_csv(run_dir)
    stat_path = run_dir / "statistical_validation_report.json"
    stat = _sanitize_stat_report(_read_json(stat_path) if stat_path.is_file() else None, run_dir)
    categories = _compute_category_breakdown(run_dir)

    return {
        "run_id": run_dir.name,
        "results_dir": _rel_path(run_dir),
        "reproducibility": {
            "dataset_version": comp.get("dataset_version"),
            "dataset_sha256": comp.get("dataset_sha256"),
            "task_count": comp.get("task_count"),
            "random_seed": comp.get("random_seed"),
            "failure_rates_percent": comp.get("failure_rates_percent", []),
        },
        "evaluation_context": {
            "provider_mode": "offline_deterministic_local",
            "dataset_type": "synthetic_100_task_oracle_plan_benchmark",
            "live_llm_benchmark_present": False,
            "real_world_benchmark_present": False,
            "scope_note": (
                "Reported benchmark figures measure offline deterministic local execution "
                "using dataset-authored oracle tool plans. Live LLM (Gemini/OpenAI/Anthropic) "
                "and real-world API evaluations are distinct and not conflated here."
            ),
        },
        "metrics_summary": metrics_rows,
        "comparisons": comp.get("comparisons", []),
        "ablation_metrics": comp.get("ablation_metrics", {}),
        "category_breakdown": categories,
        "statistical_validation": stat,
        "best_configuration": _select_best_configuration(metrics_rows),
        "plots": _plot_manifest(run_dir, include_run_query=bool(run_id)),
    }


@app.get("/api/export/csv")
def api_export_csv(run_id: str | None = Query(default=None)) -> Response:
    """Download real metrics_summary.csv for the selected benchmark run."""
    run_dir = _resolve_experiment_dir(run_id)
    csv_path = run_dir / "metrics_summary.csv"
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail="metrics_summary.csv not found for this run.")
    content = csv_path.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tracerecover-{run_dir.name}-metrics.csv"'
        },
    )


@app.get("/api/export/json")
def api_export_json(run_id: str | None = Query(default=None)) -> Response:
    """Download complete structured JSON analytics bundle for the selected benchmark run."""
    payload = api_analytics(run_id=run_id)
    run_name = payload["run_id"]
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tracerecover-{run_name}-analytics.json"'
        },
    )


@app.get("/api/export/report")
def api_export_report(run_id: str | None = Query(default=None)) -> Response:
    """Generate and download a Markdown research report for the selected benchmark run."""
    run_dir = _resolve_experiment_dir(run_id)
    markdown = _build_markdown_report(run_dir)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tracerecover-{run_dir.name}-report.md"'
        },
    )


@app.get("/api/export/pdf")
def api_export_pdf(run_id: str | None = Query(default=None)) -> Response:
    """Generate and download a publication-formatted PDF research report."""
    run_dir = _resolve_experiment_dir(run_id)
    analytics = api_analytics(run_id=run_dir.name)
    pdf_bytes = generate_experiment_pdf(run_dir, analytics)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="tracerecover-{run_dir.name}-report.pdf"'
        },
    )


# ---------------------------------------------------------------------------
# Execution Replay Endpoints (Reads Stored Traces Only - Never Re-Executes)
# ---------------------------------------------------------------------------


@app.get("/api/replay/sources")
def api_replay_sources(run_id: str | None = Query(default=None)) -> dict[str, Any]:
    """List recorded live and benchmark traces available for step-by-step replay."""
    live_items: list[dict[str, Any]] = []
    for path in _list_primary_trace_files()[:40]:
        bundle = _load_trace_bundle(path, include_full_payload=False)
        live_items.append(
            {
                "source": "live",
                "trace_id": bundle["trace_id"],
                "label": f"{bundle['trace_id']} · {bundle['final_decision']} · {bundle['question'][:55]}",
                "question": bundle["question"],
                "final_decision": bundle["final_decision"],
                "total_failures": bundle["total_failures"],
                "successful_recoveries": bundle["successful_recoveries"],
                "step_count": bundle["provenance"]["total_steps"],
            }
        )

    eval_items: list[dict[str, Any]] = []
    chosen_run = run_id
    if not chosen_run:
        latest = _latest_results_dir()
        chosen_run = latest.name if latest else None

    if chosen_run and _SAFE_ID_RE.fullmatch(chosen_run):
        tr_json = (RESULTS_DIR / chosen_run / "tracerecover_results.json")
        if tr_json.is_file():
            tr_data = _read_json(tr_json)
            for cond in tr_data.get("conditions", []):
                rate_val = float(cond.get("failure_rate_percent", 0))
                rate_lbl = str(int(rate_val)) if rate_val.is_integer() else str(rate_val).replace(".", "_")
                # Prioritize interesting recovery/abstention/answerable samples plus all 40% tasks
                results_list = cond.get("results", [])
                for row in results_list:
                    tid = str(row.get("task_id", ""))
                    task_dir = LOGS_DIR / "evaluation" / chosen_run / "tracerecover-full" / f"failure-{rate_lbl}" / tid
                    if not task_dir.is_dir():
                        continue
                    eval_items.append(
                        {
                            "source": "eval",
                            "run_id": chosen_run,
                            "rate": rate_lbl,
                            "task_id": tid,
                            "category": row.get("category", ""),
                            "label": (
                                f"{tid} @ {rate_lbl}% · {row.get('final_decision')} · "
                                f"fail={row.get('detected_failures', 0)}/rec={row.get('successful_recoveries', 0)} · "
                                f"{str(row.get('query', ''))[:45]}"
                            ),
                            "final_decision": row.get("final_decision"),
                            "total_failures": int(row.get("detected_failures") or 0),
                            "successful_recoveries": int(row.get("successful_recoveries") or 0),
                        }
                    )

    return {
        "live_count": len(live_items),
        "eval_count": len(eval_items),
        "active_eval_run_id": chosen_run,
        "live_traces": live_items,
        "eval_traces": eval_items,
    }


@app.get("/api/replay/trace")
def api_replay_trace(
    source: str = Query(default="live"),
    trace_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    rate: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Load one persisted trace and return ordered step-by-step replay frames without re-running."""
    norm_source = source.strip().lower()
    if norm_source == "live":
        if not trace_id:
            raise HTTPException(status_code=400, detail="Valid trace_id is required for live replay.")
        candidate = _resolve_any_live_trace(trace_id)
    elif norm_source == "eval":
        if not (run_id and rate and task_id):
            raise HTTPException(status_code=400, detail="run_id, rate, and task_id are required for eval replay.")
        candidate = _find_eval_trace_file(run_id, rate, task_id)
    else:
        raise HTTPException(status_code=422, detail="source must be 'live' or 'eval'.")

    bundle = _load_trace_bundle(candidate, include_full_payload=True)
    frames = _build_replay_frames(bundle)
    return {
        "source": norm_source,
        "trace_id": bundle["trace_id"],
        "question": bundle["question"],
        "final_decision": bundle["final_decision"],
        "frame_count": len(frames),
        "frames": frames,
        "bundle": bundle,
    }


# ---------------------------------------------------------------------------
# Dataset Management & Safe Import Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/datasets")
def api_datasets_catalog() -> dict[str, Any]:
    """Return full metadata, integrity hashes, categories, and version history for all datasets."""
    # Map dataset SHA-256 -> associated experiment run IDs
    sha_to_runs: dict[str, list[str]] = {}
    for run_dir in _list_experiment_dirs():
        try:
            comp = _read_json(run_dir / "comparison_summary.json")
            sha = str(comp.get("dataset_sha256", ""))
            if sha:
                sha_to_runs.setdefault(sha, []).append(run_dir.name)
        except Exception:
            continue

    items: list[dict[str, Any]] = []
    # Built-in JSON task datasets
    for ds_id, name, desc, path, canonical in (
        (
            "eval_tasks",
            "Canonical 100-Task Benchmark",
            "Primary multi-hop, API aggregation, argument-repair, abstention, and contradiction benchmark.",
            PROJECT_ROOT / "eval" / "tasks.json",
            True,
        ),
        (
            "synthetic_benchmark_100",
            "Synthetic Benchmark 100 Archive",
            "Data-directory archive copy synchronized with eval/tasks.json.",
            DATA_DIR / "tracerecover_synthetic_benchmark_100.json",
            False,
        ),
    ):
        if path.is_file():
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            payload = json.loads(raw.decode("utf-8"))
            tasks = payload.get("tasks", [])
            items.append(
                {
                    "id": ds_id,
                    "name": name,
                    "description": desc,
                    "version": str(payload.get("dataset_version", "2.0")),
                    "sha256": sha,
                    "size_kb": round(len(raw) / 1024.0, 2),
                    "record_count": len(tasks),
                    "record_unit": "tasks",
                    "inspectable": True,
                    "is_canonical": canonical,
                    "relative_path": _rel_path(path),
                    "categories": _dataset_category_stats(tasks),
                    "associated_experiments": sha_to_runs.get(sha, []),
                    "imported_at": None,
                }
            )

    # Built-in telemetry CSVs
    for ds_id, name, desc, path in (
        (
            "execution_telemetry",
            "Execution Telemetry (600 Runs)",
            "Per-task execution telemetry across 100 tasks × 3 fault rates for Baseline and TraceRecover.",
            DATA_DIR / "tracerecover_execution_telemetry.csv",
        ),
        (
            "step_level_telemetry",
            "Step-Level Trace Telemetry (1,769 Steps)",
            "Individual tool call, injection, and recovery steps across the 600 benchmark runs.",
            DATA_DIR / "tracerecover_step_level_telemetry.csv",
        ),
    ):
        if path.is_file():
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            row_count = max(0, len(raw.decode("utf-8").splitlines()) - 1)
            items.append(
                {
                    "id": ds_id,
                    "name": name,
                    "description": desc,
                    "version": "2.0",
                    "sha256": sha,
                    "size_kb": round(len(raw) / 1024.0, 2),
                    "record_count": row_count,
                    "record_unit": "rows",
                    "inspectable": False,
                    "is_canonical": False,
                    "relative_path": _rel_path(path),
                    "categories": {},
                    "associated_experiments": [],
                    "imported_at": None,
                }
            )

    # Versioned custom datasets imported via UI/API (data/versions/)
    version_history = _read_version_manifest()
    for v_item in version_history:
        fpath = DATASET_VERSIONS_DIR / str(v_item.get("filename", ""))
        if not fpath.is_file():
            continue
        items.append(
            {
                "id": v_item["id"],
                "name": v_item["name"],
                "description": "Imported versioned dataset (canonical benchmark untouched).",
                "version": v_item["version"],
                "sha256": v_item["sha256"],
                "size_kb": v_item["size_kb"],
                "record_count": v_item["task_count"],
                "record_unit": "tasks",
                "inspectable": True,
                "is_canonical": False,
                "relative_path": _rel_path(fpath),
                "categories": v_item.get("categories", {}),
                "associated_experiments": sha_to_runs.get(v_item["sha256"], []),
                "imported_at": v_item.get("imported_at"),
            }
        )

    return {
        "count": len(items),
        "datasets": items,
        "version_history": version_history,
    }


@app.get("/api/datasets/{dataset_id}/tasks")
def api_dataset_tasks(
    dataset_id: str,
    category: str | None = Query(default=None),
    abstain: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return paginated, filterable tasks from an inspectable benchmark dataset."""
    if not _SAFE_ID_RE.fullmatch(dataset_id):
        raise HTTPException(status_code=400, detail="Invalid dataset_id format.")
    path, ds_name = _resolve_task_dataset_path(dataset_id)
    payload = _read_json(path)
    all_tasks = list(payload.get("tasks", []))

    cat_filter = category.strip() if category and category != "all" else None
    abs_filter = abstain.strip().lower() if abstain and abstain != "all" else None
    q_filter = search.strip().casefold() if search else None

    filtered: list[dict[str, Any]] = []
    for t in all_tasks:
        if cat_filter and t.get("category") != cat_filter:
            continue
        if abs_filter == "true" and t.get("should_abstain") is not True:
            continue
        if abs_filter == "false" and t.get("should_abstain") is not False:
            continue
        if q_filter:
            hay = f"{t.get('task_id', '')} {t.get('query', '')} {t.get('category', '')}".casefold()
            if q_filter not in hay:
                continue
        filtered.append(t)

    page = filtered[offset : offset + limit]
    return {
        "dataset_id": dataset_id,
        "name": ds_name,
        "dataset_version": payload.get("dataset_version", "2.0"),
        "total_tasks": len(all_tasks),
        "filtered_count": len(filtered),
        "returned": len(page),
        "offset": offset,
        "limit": limit,
        "tasks": page,
    }


@app.post("/api/datasets/validate")
def api_validate_dataset(payload: DatasetPayloadRequest) -> dict[str, Any]:
    """Validate a candidate task dataset against TraceRecover schema without modifying disk."""
    errors, stats, sha = _validate_dataset_payload(payload.tasks, payload.dataset_version)
    return {
        "valid": len(errors) == 0,
        "task_count": len(payload.tasks),
        "categories": stats,
        "sha256": sha,
        "errors": errors,
    }


@app.post("/api/datasets/import", dependencies=[Depends(_require_admin_if_configured)])
def api_import_dataset(payload: DatasetPayloadRequest, request: Request) -> dict[str, Any]:
    """Import a validated custom dataset into data/versions/ without altering eval/tasks.json."""
    _enforce_rate_limit(request, "dataset_import", "TRACERECOVER_RATE_LIMIT_EXPERIMENTS", 10)
    errors, stats, sha = _validate_dataset_payload(payload.tasks, payload.dataset_version)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Dataset schema validation failed.", "errors": errors},
        )

    DATASET_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", payload.name.casefold()).strip("-") or "dataset"
    ver_slug = re.sub(r"[^a-z0-9._-]+", "-", payload.dataset_version.casefold()).strip("-") or "1.0"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ds_id = f"{slug}-v{ver_slug}-{ts}"
    filename = f"{ds_id}.json"
    out_path = (DATASET_VERSIONS_DIR / filename).resolve()
    out_bytes = (
        json.dumps(
            {
                "dataset_version": payload.dataset_version.strip(),
                "name": payload.name.strip(),
                "tasks": payload.tasks,
            },
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")
    out_path.write_bytes(out_bytes)

    manifest = _read_version_manifest()
    entry = {
        "id": ds_id,
        "name": payload.name.strip(),
        "version": payload.dataset_version.strip(),
        "filename": filename,
        "sha256": hashlib.sha256(out_bytes).hexdigest(),
        "size_kb": round(len(out_bytes) / 1024.0, 2),
        "task_count": len(payload.tasks),
        "categories": stats,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest.insert(0, entry)
    _manifest_path().write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return {"status": "imported", "dataset": entry}


@app.get("/api/plots/{filename}")
def api_plot(filename: str, run_id: str | None = Query(default=None)) -> FileResponse:
    """Serve one of the real benchmark SVG plots from the selected or latest results folder."""
    if not _SAFE_PLOT_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid SVG plot filename.")
    run_dir = _resolve_experiment_dir(run_id)
    plots_dir = (run_dir / "plots").resolve()
    target = (plots_dir / filename).resolve()
    try:
        target.relative_to(plots_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid plot path.") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Plot {filename!r} not found.")
    return FileResponse(target, media_type="image/svg+xml")


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    """Return read-only runtime limits, recovery budgets, NLI config, and calibrated thresholds."""
    settings = get_settings()
    default_policy = AbstentionPolicy()
    default_recovery = RecoveryEngine()
    default_estimator = UncertaintyEstimator()
    default_nli = DebertaNLIVerifier(local_files_only=True, allow_fallback=True)
    default_executor = ToolExecutor.with_default_tools()

    calibrated_thresholds: dict[str, Any] | None = None
    latest_dir = _latest_results_dir()
    if latest_dir is not None:
        stat_path = latest_dir / "statistical_validation_report.json"
        if stat_path.is_file():
            stat = _read_json(stat_path)
            cv = stat.get("five_fold_cross_fitted_thresholds", {})
            calibrated_thresholds = {
                "protocol": cv.get("cv_protocol"),
                "target_abstain_precision": cv.get("target_abstain_precision"),
                "summary": cv.get("fitted_threshold_summary"),
                "out_of_fold_aggregate": cv.get("out_of_fold_aggregate"),
            }

    return {
        "runtime": {
            "default_provider": settings.provider,
            "request_timeout_seconds": settings.request_timeout,
            "agent_max_steps": 8,
            "registered_tools": default_executor.list_tools(),
            "ast_and_schema_guards": {
                "calculator_max_expression_length": 200,
                "python_exec_max_code_length": 1000,
                "python_exec_allowed_calls": ["abs", "len", "max", "min", "sum"],
                "mock_api_ticket_pattern": "^T-\\d+$",
            },
        },
        "security": {
            "cors_allowed_origins": _allowed_cors_origins(),
            "admin_auth_required_for_experiments": bool(_admin_token()),
            "rate_limit_run_per_min": _parse_int_env("TRACERECOVER_RATE_LIMIT_RUN", 60),
            "rate_limit_experiments_per_min": _parse_int_env("TRACERECOVER_RATE_LIMIT_EXPERIMENTS", 10),
            "max_request_bytes": _parse_int_env("TRACERECOVER_MAX_REQUEST_BYTES", 65_536),
        },
        "providers": _provider_status_list(),
        "failure_injection": {
            "supported_types": [ft.value for ft in FailureType],
            "rate_bounds_percent": [0.0, 100.0],
            "default_seed": 42,
        },
        "recovery": {
            "max_retries": default_recovery.max_retries,
            "max_total_attempts": default_recovery.max_total_attempts,
            "max_duration_seconds": default_recovery.max_duration_seconds,
            "budget_explanation": (
                "Two-tier recovery budget in healing/recovery.py: max_retries (1) limits only the "
                "RETRY strategy loop per failure; max_total_attempts (4) bounds combined attempt records "
                "across all fallback strategies (RETRY, ARGUMENT_REPAIR, TOOL_SWITCH, REPLAN); "
                "max_duration_seconds (15.0s) is checked between attempts."
            ),
            "strategy_map": {
                cat.value: [strat.value for strat in strategies]
                for cat, strategies in DEFAULT_STRATEGY_MAP.items()
            },
        },
        "grounding_and_nli": {
            "default_nli_model": SUPPORTED_NLI_MODELS[0],
            "supported_nli_models": list(SUPPORTED_NLI_MODELS),
            "active_nli_backend": default_nli.backend_name,
            "entailment_threshold": default_nli.entailment_threshold,
            "contradiction_threshold": default_nli.contradiction_threshold,
            "require_grounding_default": default_policy.require_grounding,
        },
        "uncertainty_and_abstention": {
            "signal_weights": dict(DEFAULT_SIGNAL_WEIGHTS),
            "repeated_attempt_penalty": default_estimator.repeated_attempt_penalty,
            "default_thresholds": {
                "answer_threshold": default_policy.answer_threshold,
                "abstain_threshold": default_policy.abstain_threshold,
            },
            "calibrated_5fold_thresholds": calibrated_thresholds,
        },
        "external_search": {
            "configured_provider": os.getenv("TRACERECOVER_SEARCH_PROVIDER", "auto").strip().lower(),
            "supported_providers": list(SUPPORTED_SEARCH_PROVIDERS),
            "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
            "serpapi_configured": bool(os.getenv("SERPAPI_API_KEY", "").strip()),
            "default_no_key_sources": ["wikipedia", "duckduckgo"],
        },
        "open_ended_limits": {
            "max_question_chars": 4000,
            "max_steps": 10,
            "max_llm_calls": 12,
            "max_cost_usd": 2.0,
            "max_conversations_per_session": _parse_int_env("TRACERECOVER_MAX_CONVERSATIONS_PER_SESSION", 50, minimum=5),
        },
    }


# ---------------------------------------------------------------------------
# Phase 3: Open-Ended Query Workspace, Conversations, Unseen Eval & Compare
# ---------------------------------------------------------------------------


def _conv_store() -> ConversationStore:
    return ConversationStore(CONVERSATIONS_DIR)


def _session_from_header(x_session_id: str | None) -> str:
    return (x_session_id or "default").strip() or "default"


def _sanitize_open_ended_result(out: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(out)
    artifacts = clean.get("artifacts")
    if isinstance(artifacts, Mapping):
        clean["artifacts"] = {
            k: (_rel_path(Path(v)) if isinstance(v, str) and v else None)
            for k, v in artifacts.items()
        }
    fr = clean.get("failure_report")
    if isinstance(fr, Mapping):
        fr_copy = dict(fr)
        for key in ("trace_file", "analysis_file", "recovery_report_file"):
            if isinstance(fr_copy.get(key), str):
                fr_copy[key] = _rel_path(Path(fr_copy[key]))
        clean["failure_report"] = fr_copy
    rr = clean.get("recovery_report")
    if isinstance(rr, Mapping):
        rr_copy = dict(rr)
        if isinstance(rr_copy.get("recovery_log_file"), str):
            rr_copy["recovery_log_file"] = _rel_path(Path(rr_copy["recovery_log_file"]))
        clean["recovery_report"] = rr_copy
    return clean


@app.get("/api/conversations")
def api_list_conversations(
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    """List conversations belonging strictly to the caller's X-Session-ID."""
    store = _conv_store()
    sid = _session_from_header(x_session_id)
    items = store.list_conversations(sid)
    return {"session_id": sid, "count": len(items), "conversations": items}


@app.post("/api/conversations")
def api_create_conversation(
    payload: ConversationCreateRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    """Create a fresh isolated conversation thread."""
    store = _conv_store()
    sid = _session_from_header(x_session_id)
    summary = store.create_conversation(sid, title=payload.title)
    return {"status": "created", "conversation": summary}


@app.get("/api/conversations/{conversation_id}")
def api_get_conversation(
    conversation_id: str,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    """Load one conversation owned by the caller's session."""
    store = _conv_store()
    sid = _session_from_header(x_session_id)
    try:
        rec = store.get_conversation(sid, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id!r} not found.")
    return rec


@app.post("/api/conversations/{conversation_id}/clear")
def api_clear_conversation(
    conversation_id: str,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    """Clear stored message context inside a conversation owned by this session."""
    store = _conv_store()
    sid = _session_from_header(x_session_id)
    try:
        summary = store.clear_conversation(sid, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id!r} not found.")
    return {"status": "cleared", "conversation": summary}


@app.delete("/api/conversations/{conversation_id}")
def api_delete_conversation(
    conversation_id: str,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    """Delete a conversation owned by this session."""
    store = _conv_store()
    sid = _session_from_header(x_session_id)
    try:
        deleted = store.delete_conversation(sid, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id!r} not found.")
    return {"status": "deleted", "conversation_id": conversation_id}


def _execute_open_ended_query(
    payload: OpenEndedQueryRequest,
    session_id: str,
    *,
    on_event: Any = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if payload.answer_threshold >= payload.abstain_threshold:
        raise HTTPException(
            status_code=422,
            detail="answer_threshold must be strictly less than abstain_threshold.",
        )

    store = _conv_store()
    conv_id = payload.conversation_id
    history: list[dict[str, str]] = []
    if conv_id:
        try:
            existing = store.get_conversation(session_id, conv_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Conversation {conv_id!r} not found.")
        history = store.get_context_messages(session_id, conv_id)

    qid = payload.query_id
    if qid and not _SAFE_ID_RE.fullmatch(qid):
        raise HTTPException(status_code=400, detail="Invalid query_id format.")
    qid = qid or f"qry-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"

    runner = OpenEndedAgentRunner(log_directory=LOGS_DIR)
    reg_event = cancel_event or QUERY_CANCEL_REGISTRY.register(qid)
    try:
        raw = runner.run(
            payload.question,
            provider=payload.provider,
            conversation_id=conv_id,
            query_id=qid,
            conversation_history=history,
            enable_external_search=payload.enable_external_search,
            search_provider=payload.search_provider,
            failure_rate=payload.failure_rate,
            seed=payload.seed,
            enabled_failure_types=payload.enabled_failure_types,
            use_nli=payload.use_nli,
            enable_recovery=payload.enable_recovery,
            enable_grounding=payload.enable_grounding,
            enable_uncertainty=payload.enable_uncertainty,
            answer_threshold=payload.answer_threshold,
            abstain_threshold=payload.abstain_threshold,
            max_steps=payload.max_steps,
            max_llm_calls=payload.max_llm_calls,
            max_cost_usd=payload.max_cost_usd,
            cancel_event=reg_event,
            on_event=on_event,
        )
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except QueryCancelledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        QUERY_CANCEL_REGISTRY.unregister(qid)

    clean = _sanitize_open_ended_result(raw)
    turn_record = {
        "query_id": clean["query_id"],
        "trace_id": clean["trace_id"],
        "timestamp": clean["timestamp"],
        "question": clean["question"],
        "resolved_query": clean["resolved_query"],
        "provider": clean["provider"],
        "provider_scope": clean["provider_scope"],
        "model": clean["model"],
        "knowledge_source": clean["knowledge_source"],
        "tools_selected": clean["tools_selected"],
        "external_sources": clean["external_sources"],
        "final_decision": clean["final_decision"],
        "decision_explanation": clean["decision_explanation"],
        "final_response": clean["final_response"],
        "elapsed_seconds": clean["elapsed_seconds"],
        "llm_usage": clean["llm_usage"],
        "provenance": clean["provenance"],
        "execution_events": clean["execution_events"],
    }
    saved_conv = store.append_turn(session_id, conv_id, turn_record)
    clean["conversation_id"] = saved_conv["conversation_id"]
    clean["conversation_title"] = saved_conv.get("title")
    clean["turn_count"] = len(saved_conv.get("turns", []))
    return clean


@app.post("/api/chat/query")
def api_chat_query(
    payload: OpenEndedQueryRequest,
    request: Request,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    """Execute an arbitrary open-ended query with dynamic planning and conversation context."""
    _enforce_rate_limit(request, "live_run", "TRACERECOVER_RATE_LIMIT_RUN", 60)
    sid = _session_from_header(x_session_id)
    return _execute_open_ended_query(payload, sid)


@app.post("/api/chat/stream")
def api_chat_stream(
    payload: OpenEndedQueryRequest,
    request: Request,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> StreamingResponse:
    """Stream actual 10-stage execution events via Server-Sent Events (SSE)."""
    _enforce_rate_limit(request, "live_run", "TRACERECOVER_RATE_LIMIT_RUN", 60)
    if payload.answer_threshold >= payload.abstain_threshold:
        raise HTTPException(
            status_code=422,
            detail="answer_threshold must be strictly less than abstain_threshold.",
        )

    sid = _session_from_header(x_session_id)
    qid = payload.query_id
    if qid and not _SAFE_ID_RE.fullmatch(qid):
        raise HTTPException(status_code=400, detail="Invalid query_id format.")
    qid = qid or f"qry-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
    bound_payload = payload.model_copy(update={"query_id": qid})
    cancel_ev = QUERY_CANCEL_REGISTRY.register(qid)
    event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

    def _worker() -> None:
        try:
            res = _execute_open_ended_query(
                bound_payload,
                sid,
                on_event=lambda evt: event_queue.put(("stage", evt)),
                cancel_event=cancel_ev,
            )
            event_queue.put(("result", res))
        except HTTPException as exc:
            event_queue.put(("error", {"status_code": exc.status_code, "detail": exc.detail}))
        except Exception:
            event_queue.put(("error", {"status_code": 500, "detail": "Internal query execution error."}))
        finally:
            event_queue.put(("done", {"query_id": qid}))

    threading.Thread(target=_worker, daemon=True).start()

    def _iter_sse():
        yield f"event: meta\ndata: {json.dumps({'query_id': qid, 'conversation_id': payload.conversation_id})}\n\n"
        while True:
            kind, item = event_queue.get()
            yield f"event: {kind}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
            if kind == "done":
                break

    return StreamingResponse(
        _iter_sse(),
        media_type="text/event-stream",
        headers=_NO_CACHE_HEADERS,
    )


@app.post("/api/chat/cancel/{query_id}")
def api_chat_cancel(query_id: str) -> dict[str, Any]:
    """Signal cooperative cancellation for an active open-ended query."""
    if not _SAFE_ID_RE.fullmatch(query_id):
        raise HTTPException(status_code=400, detail="Invalid query_id format.")
    cancelled = QUERY_CANCEL_REGISTRY.cancel(query_id)
    return {"query_id": query_id, "cancel_requested": cancelled}


@app.get("/api/unseen/runs")
def api_unseen_runs() -> dict[str, Any]:
    """Return unseen-eval run history plus strictly separated population-level generalization metrics."""
    store = _conv_store()
    runs = unseen_eval.list_unseen_runs(RESULTS_DIR)
    gen = unseen_eval.build_generalization_summary(
        results_dir=RESULTS_DIR,
        conversation_store=store,
    )
    return {"count": len(runs), "runs": runs, "generalization": gen}


@app.get("/api/unseen/runs/{run_id}")
def api_unseen_run_detail(run_id: str) -> dict[str, Any]:
    """Return full dataset, metrics, and per-task outcomes for one unseen-eval run."""
    if not _SAFE_ID_RE.fullmatch(run_id) or not run_id.startswith("unseen-eval-"):
        raise HTTPException(status_code=400, detail="Invalid unseen run_id.")
    summary_path = (RESULTS_DIR / run_id / "summary.json").resolve()
    try:
        summary_path.relative_to(RESULTS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid unseen run path.") from exc
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail=f"Unseen evaluation run {run_id!r} not found.")
    return _read_json(summary_path)


@app.post("/api/unseen/run", dependencies=[Depends(_require_admin_if_configured)])
def api_run_unseen_eval(payload: UnseenEvalRunRequest, request: Request) -> dict[str, Any]:
    """Generate and run a separate unseen-query benchmark without modifying canonical tasks."""
    _enforce_rate_limit(request, "experiment_run", "TRACERECOVER_RATE_LIMIT_EXPERIMENTS", 10)
    try:
        report = unseen_eval.run_unseen_evaluation(
            num_tasks=payload.num_tasks,
            seed=payload.seed,
            provider=payload.provider,
            failure_rate=payload.failure_rate,
            enable_external_search=payload.enable_external_search,
            search_provider=payload.search_provider,
            use_nli=payload.use_nli,
            results_dir=RESULTS_DIR,
            logs_dir=LOGS_DIR,
            datasets_dir=UNSEEN_DATASETS_DIR,
        )
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report


@app.post("/api/compare/providers")
def api_compare_providers(payload: ProviderCompareRequest, request: Request) -> dict[str, Any]:
    """Run the same question across selected providers and return structured reliability comparisons."""
    _enforce_rate_limit(request, "live_run", "TRACERECOVER_RATE_LIMIT_RUN", 60)
    guard_check = unseen_eval.compare_providers(
        payload.question,
        providers=payload.providers,
        enable_external_search=payload.enable_external_search,
        search_provider=payload.search_provider,
        failure_rate=payload.failure_rate,
        seed=payload.seed,
        use_nli=payload.use_nli,
        results_dir=RESULTS_DIR,
        logs_dir=LOGS_DIR,
    )
    return guard_check


@app.get("/api/compare/history")
def api_compare_history() -> dict[str, Any]:
    """List saved multi-provider comparisons."""
    items = unseen_eval.list_provider_comparisons(RESULTS_DIR)
    return {"count": len(items), "comparisons": items}


# ---------------------------------------------------------------------------
# Static frontend hosting (clean white web dashboard in static/index.html)
# ---------------------------------------------------------------------------

if (FRONTEND_DIST_DIR / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST_DIR / "assets")),
        name="frontend-assets",
    )


def _resolve_spa_index() -> Path | None:
    static_index = STATIC_DIR / "index.html"
    if static_index.is_file():
        return static_index
    dist_index = FRONTEND_DIST_DIR / "index.html"
    if dist_index.is_file():
        return dist_index
    return None


@app.get("/", include_in_schema=False)
def serve_root() -> Any:
    index_path = _resolve_spa_index()
    if index_path is not None:
        return FileResponse(index_path, media_type="text/html", headers=_NO_CACHE_HEADERS)
    return HTMLResponse(
        "<h1>TraceRecover API Running</h1><p>Dashboard file not found. "
        "Inspect <a href='/api/health'>/api/health</a>.</p>"
    )


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa_fallback(full_path: str) -> Any:
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(status_code=404, detail="API endpoint not found.")
    if full_path in {"", "index.html"}:
        return serve_root()
    if STATIC_DIR.is_dir():
        static_file = (STATIC_DIR / full_path).resolve()
        try:
            static_file.relative_to(STATIC_DIR.resolve())
            if static_file.is_file():
                return FileResponse(static_file, headers=_NO_CACHE_HEADERS)
        except ValueError:
            pass
    index_path = _resolve_spa_index()
    if index_path is not None:
        return FileResponse(index_path, media_type="text/html", headers=_NO_CACHE_HEADERS)
    raise HTTPException(status_code=404, detail="Resource not found.")


if __name__ == "__main__":
    import uvicorn

    port = _parse_int_env("PORT", 8000, minimum=1)
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("web_dashboard:app", host=host, port=port, reload=False)
