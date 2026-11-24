"""Shared deterministic runner for baseline, TraceRecover, and ablations."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.react_agent import ReactAgent
from config import LLMClient
from grounding.claim_extractor import ClaimExtractor
from healing.detector import FailureDetector
from healing.recovery import RecoveryEngine
from tools.failure_injector import FailureInjector
from uncertainty.abstention import AbstentionPolicy

from .metrics import summarize_metrics


_TOOLS = {"search", "calculator", "python_exec", "mock_api"}
_REFUSAL_MARKERS = (
    "no local evidence found",
    "could not complete",
    "cannot complete",
    "cannot answer",
    "i am abstaining",
    "was not found",
    "not found",
)
_TASK_SEED_STRIDE = 1009
_RECOVERY_SEED_OFFSET = 1_000_003


class DatasetTaskPlanner:
    """Execute task-authored tool steps for controlled agent comparisons.

    The oracle plan deliberately removes free-form planning variability. It
    makes the experiments comparable, but does not measure planner quality.
    """

    def __init__(self, task: Mapping[str, Any]) -> None:
        self._steps = task["oracle_plan"]
        self._final_template = task["final_answer_template"]

    def next_action(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        """Return the next authored action or format the final response."""
        del question
        excluded = excluded_tools or set()
        if len(observations) < len(self._steps):
            step = self._steps[len(observations)]
            if step["tool"] not in excluded:
                return {
                    "thought": "Execute the next deterministic dataset tool step.",
                    "action": step["tool"],
                    "arguments": dict(step["arguments"]),
                    "final_answer": None,
                }
            return self._final_action(
                "The planned tool was excluded after a failure; no alternate "
                "dataset action is defined."
            )

        outputs = []
        for observation in observations[: len(self._steps)]:
            if observation.get("error"):
                outputs.append(f"ERROR: {observation['error']}")
            else:
                value = observation.get("output")
                if isinstance(value, (dict, list)):
                    outputs.append(
                        json.dumps(value, sort_keys=True, ensure_ascii=True)
                    )
                else:
                    outputs.append(str(value))
        try:
            answer = self._final_template.format(*outputs)
        except (IndexError, KeyError, ValueError) as exc:
            raise ValueError(
                f"Invalid final answer template for evaluation task: {exc}"
            ) from exc
        return self._final_action(answer)

    @staticmethod
    def _final_action(answer: str) -> dict[str, Any]:
        return {
            "thought": "Return the dataset-authored answer from recorded observations.",
            "action": "final",
            "arguments": {},
            "final_answer": answer,
        }


def load_tasks(
    path: Path | str, expected_count: int | None = 100
) -> tuple[str, list[dict[str, Any]], str]:
    """Load and validate the versioned task dataset and return its SHA-256."""
    dataset_path = Path(path)
    try:
        raw_bytes = dataset_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load evaluation dataset {dataset_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Evaluation dataset must be a JSON object.")
    version = payload.get("dataset_version")
    tasks = payload.get("tasks")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Dataset must provide a non-empty dataset_version.")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Dataset must contain a non-empty tasks list.")
    if expected_count is not None and len(tasks) != expected_count:
        raise ValueError(
            f"Expected {expected_count} tasks, found {len(tasks)}."
        )

    task_ids: set[str] = set()
    for index, task in enumerate(tasks):
        _validate_task(task, index)
        task_id = task["task_id"]
        if task_id in task_ids:
            raise ValueError(f"Duplicate task_id {task_id!r}.")
        task_ids.add(task_id)
    return version, tasks, hashlib.sha256(raw_bytes).hexdigest()


def run_evaluation(
    *,
    tasks: Sequence[Mapping[str, Any]],
    dataset_version: str,
    dataset_sha256: str,
    system_name: str,
    failure_rates: Sequence[float],
    seed: int,
    log_root: Path | str,
    feature_flags: Mapping[str, bool],
    provider: str = "local",
    answer_threshold: float = 0.15,
    abstain_threshold: float = 0.55,
) -> dict[str, Any]:
    """Run a system at each failure rate and preserve per-task execution data."""
    if provider != "local":
        raise ValueError("Reproducible Phase 7 runs require provider='local'.")
    if not failure_rates:
        raise ValueError("At least one failure rate is required.")
    if any(
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 <= rate <= 100
        for rate in failure_rates
    ):
        raise ValueError("Failure rates must be percentages between 0 and 100.")
    if any(not isinstance(value, bool) for value in feature_flags.values()):
        raise TypeError("All Phase feature flags must be booleans.")

    client = LLMClient(provider=provider)
    conditions: list[dict[str, Any]] = []
    root = Path(log_root)
    for rate in failure_rates:
        results = [
            _run_task(
                task=task,
                task_index=index,
                rate=float(rate),
                base_seed=seed,
                client=client,
                feature_flags=feature_flags,
                log_root=root / system_name / f"failure-{_rate_label(float(rate))}",
                answer_threshold=answer_threshold,
                abstain_threshold=abstain_threshold,
            )
            for index, task in enumerate(tasks)
        ]
        conditions.append(
            {
                "failure_rate_percent": float(rate),
                "task_count": len(results),
                "metrics": summarize_metrics(results),
                "results": results,
            }
        )
    return {
        "experiment": {
            "system": system_name,
            "provider": client.provider,
            "model": client.model,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "dataset_version": dataset_version,
            "dataset_sha256": dataset_sha256,
            "random_seed": seed,
            "task_seed_strategy": (
                f"base_seed + task_index * {_TASK_SEED_STRIDE}"
            ),
            "recovery_seed_offset": _RECOVERY_SEED_OFFSET,
            "failure_rates_percent": [float(rate) for rate in failure_rates],
            "task_count": len(tasks),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "configuration": {
                "planner": "dataset-authored deterministic oracle plan",
                "recovery_max_retries": 1,
                "recovery_max_total_attempts": 4,
                "recovery_max_duration_seconds": 15.0,
                "feature_flags": dict(feature_flags),
                "answer_threshold": answer_threshold,
                "abstain_threshold": abstain_threshold,
            },
        },
        "conditions": conditions,
    }


def write_json(path: Path | str, payload: Mapping[str, Any]) -> Path:
    """Write a JSON result file, creating only its required parent directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as output_file:
        json.dump(payload, output_file, ensure_ascii=True, indent=2, default=str)
        output_file.write("\n")
    return destination


def _run_task(
    *,
    task: Mapping[str, Any],
    task_index: int,
    rate: float,
    base_seed: int,
    client: LLMClient,
    feature_flags: Mapping[str, bool],
    log_root: Path,
    answer_threshold: float,
    abstain_threshold: float,
) -> dict[str, Any]:
    task_seed = base_seed + task_index * _TASK_SEED_STRIDE
    task_log = log_root / str(task["task_id"])
    detector = FailureDetector()
    agent = ReactAgent(
        client=client,
        failure_injector=FailureInjector(failure_rate=rate, seed=task_seed),
        recovery_failure_injector=FailureInjector(
            failure_rate=rate, seed=task_seed + _RECOVERY_SEED_OFFSET
        ),
        detector=detector,
        recovery_engine=RecoveryEngine(
            detector=detector,
            max_retries=1,
            max_total_attempts=4,
            max_duration_seconds=15.0,
        ),
        abstention_policy=AbstentionPolicy(
            answer_threshold=answer_threshold,
            abstain_threshold=abstain_threshold,
            require_grounding=feature_flags["enable_grounding"],
        ),
        log_directory=task_log,
        planner=DatasetTaskPlanner(task),
        enable_failure_detection=feature_flags["enable_failure_detection"],
        enable_localization=feature_flags["enable_localization"],
        enable_recovery=feature_flags["enable_recovery"],
        enable_grounding=feature_flags["enable_grounding"],
        enable_uncertainty=feature_flags["enable_uncertainty"],
    )
    usage_before = len(client.call_history)
    started = time.perf_counter()
    agent_result = agent.run(str(task["query"]))
    latency = time.perf_counter() - started
    task_calls = client.call_history[usage_before:]
    prompt_tokens = sum(call.prompt_tokens for call in task_calls)
    completion_tokens = sum(call.completion_tokens for call in task_calls)
    total_tokens = sum(call.total_tokens for call in task_calls)
    estimated_cost_usd = sum(call.estimated_cost_usd for call in task_calls)

    evaluated_answer = (
        None
        if agent_result.final_decision == "ABSTAIN"
        else agent_result.answer
    )
    claims = ClaimExtractor().extract(evaluated_answer)
    claim_results = [
        _classify_claim(
            claim.claim,
            task["expected_facts"],
            task["contradictory_facts"],
            bool(task["should_abstain"]),
        )
        for claim in claims
    ]
    unsupported = sum(item["status"] == "UNSUPPORTED" for item in claim_results)
    contradicted = sum(item["status"] == "CONTRADICTED" for item in claim_results)
    detections = detector.detect_trace(agent_result.trace)
    detected_failures = sum(item.failure_detected for item in detections)
    detected_injected_failures = sum(
        item.failure_detected and item.reported_failure_injected is True
        for item in detections
    )
    recovery_records = _read_json_lines(agent_result.recovery_log_path)
    injected_tool_failures = sum(
        step.get("failure_injected") is True for step in agent_result.trace
    )
    injected_recovery_failures = sum(
        record.get("recovery_execution_result", {}).get("failure_injected") is True
        for record in recovery_records
        if record.get("record_type") == "recovery_attempt"
        and isinstance(record.get("recovery_execution_result"), Mapping)
    )
    recovery_report = agent_result.recovery_report
    expected_facts = task["expected_facts"]
    response = agent_result.final_response
    if task["should_abstain"]:
        task_success = (
            agent_result.final_decision == "ABSTAIN" or _is_explicit_refusal(response)
        )
    else:
        task_success = bool(expected_facts) and all(
            _contains_fact(response, fact) for fact in expected_facts
        )
    uncertainty = agent_result.uncertainty_report.get("uncertainty_score")
    confidence = (
        1.0 - float(uncertainty)
        if isinstance(uncertainty, (int, float))
        and not isinstance(uncertainty, bool)
        and 0 <= uncertainty <= 1
        else None
    )

    return {
        "task_id": task["task_id"],
        "category": task["category"],
        "task_seed": task_seed,
        "failure_rate_percent": rate,
        "query": task["query"],
        "expected_facts": list(expected_facts),
        "should_abstain": task["should_abstain"],
        "answer": agent_result.answer,
        "evaluated_answer": evaluated_answer,
        "final_response": response,
        "final_decision": agent_result.final_decision,
        "success": task_success,
        "confidence": confidence,
        "uncertainty_score": uncertainty,
        "grounding_score": agent_result.grounding_report.get("grounding_score"),
        "tool_success_ratio": agent_result.uncertainty_report.get(
            "tool_success_ratio"
        ),
        "tool_execution_count": agent_result.uncertainty_report.get(
            "tool_execution_count",
            sum(step.get("tool") not in {"final", "llm"} for step in agent_result.trace),
        ),
        "detected_failures": detected_failures,
        "detected_injected_failures": detected_injected_failures,
        "detected_natural_failures": (
            detected_failures - detected_injected_failures
        ),
        "injected_tool_failures": injected_tool_failures,
        "injected_recovery_failures": injected_recovery_failures,
        "recovery_attempts": recovery_report.get("total_recovery_attempts", 0),
        "successful_recoveries": recovery_report.get("successful_recoveries", 0),
        "factual_claim_count": len(claim_results),
        "unsupported_claim_count": unsupported,
        "contradicted_claim_count": contradicted,
        "hallucination": bool(unsupported or contradicted) if claim_results else False,
        "claims": claim_results,
        "latency_seconds": latency,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "trace_path": str(agent_result.log_path),
        "failure_report_path": (
            str(agent_result.report_path) if agent_result.report_path else None
        ),
        "grounding_report_path": (
            str(agent_result.grounding_report_path)
            if agent_result.grounding_report_path
            else None
        ),
        "uncertainty_report_path": (
            str(agent_result.uncertainty_report_path)
            if agent_result.uncertainty_report_path
            else None
        ),
    }


def _validate_task(task: Any, index: int) -> None:
    if not isinstance(task, dict):
        raise ValueError(f"Task at index {index} must be an object.")
    required_strings = (
        "task_id",
        "query",
        "category",
        "evaluation_criteria",
        "final_answer_template",
    )
    for field in required_strings:
        if not isinstance(task.get(field), str) or not task[field].strip():
            raise ValueError(f"Task at index {index} requires non-empty {field}.")
    if task.get("expected_answer") is not None and not isinstance(
        task["expected_answer"], str
    ):
        raise ValueError(f"Task {task['task_id']} expected_answer must be string or null.")
    for field in ("expected_facts", "contradictory_facts"):
        values = task.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"Task {task['task_id']} requires a string list for {field}.")
    if not isinstance(task.get("should_abstain"), bool):
        raise ValueError(f"Task {task['task_id']} should_abstain must be boolean.")
    plan = task.get("oracle_plan")
    if not isinstance(plan, list) or not plan:
        raise ValueError(f"Task {task['task_id']} requires a non-empty oracle_plan.")
    tools: list[str] = []
    for step in plan:
        if not isinstance(step, dict) or step.get("tool") not in _TOOLS:
            raise ValueError(f"Task {task['task_id']} contains an invalid tool step.")
        if not isinstance(step.get("arguments"), dict):
            raise ValueError(f"Task {task['task_id']} tool arguments must be an object.")
        tools.append(step["tool"])
    expected_tools = task.get("expected_tool_category")
    if isinstance(expected_tools, str):
        expected_tools = [expected_tools]
    if not isinstance(expected_tools, list) or expected_tools != tools:
        raise ValueError(
            f"Task {task['task_id']} expected_tool_category must match oracle_plan."
        )
    try:
        task["final_answer_template"].format(*(["observation"] * len(plan)))
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(
            f"Task {task['task_id']} has an invalid final_answer_template: {exc}"
        ) from exc


def _classify_claim(
    claim: str,
    expected_facts: Sequence[str],
    contradictory_facts: Sequence[str],
    should_abstain: bool,
) -> dict[str, str]:
    if should_abstain and _is_explicit_refusal(claim):
        status = "SUPPORTED"
    elif any(_contains_fact(claim, fact) for fact in contradictory_facts):
        status = "CONTRADICTED"
    elif any(_contains_fact(claim, fact) for fact in expected_facts):
        status = "SUPPORTED"
    else:
        status = "UNSUPPORTED"
    return {"claim": claim, "status": status}


def _contains_fact(text: str, fact: str) -> bool:
    normalized_text = _normalize(text)
    normalized_fact = _normalize(fact)
    if not normalized_text or not normalized_fact:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_fact)}(?![a-z0-9])",
        normalized_text,
    ) is not None


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _is_explicit_refusal(text: str) -> bool:
    normalized = _normalize(text)
    return any(_normalize(marker) in normalized for marker in _REFUSAL_MARKERS)


def _rate_label(rate: float) -> str:
    return str(int(rate)) if rate.is_integer() else str(rate).replace(".", "_")


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as input_file:
        return [
            json.loads(line)
            for line in input_file
            if line.strip()
        ]

