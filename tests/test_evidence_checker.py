"""Tests for trace-grounded claim verification and citation handling."""

from typing import Any

from agent.react_agent import ReactAgent
from config import LLMClient
from grounding.evidence_checker import EvidenceChecker
from tools.failure_injector import FailureInjector, FailureType, ToolExecution


def _step(
    step_id: int,
    output: Any,
    *,
    tool: str = "search",
    error: str | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    arguments = (
        {"expression": "19 * 23 + 4"}
        if tool == "calculator"
        else {"ticket_id": "T-104"}
        if tool == "mock_api"
        else {"query": "local deterministic test"}
    )
    return {
        "step_id": step_id,
        "tool": tool,
        "input": arguments,
        "output": output,
        "error": error,
        **metadata,
    }


def test_direct_tool_evidence_supports_claim_and_cites_step() -> None:
    report = EvidenceChecker().verify(
        "The capital of France is Paris.",
        [_step(3, "The capital of France is Paris.")],
        query="What is the capital of France?",
    )

    claim = report["claims"][0]
    assert claim["status"] == "SUPPORTED"
    assert claim["evidence"][0]["step_id"] == 3
    assert claim["evidence"][0]["tool"] == "search"
    assert report["grounding_score"] == 1.0
    assert report["supported_count"] == 1


def test_unrelated_evidence_does_not_support_shared_words() -> None:
    report = EvidenceChecker().verify(
        "The capital of France is Rome.",
        [_step(1, "France and Paris were both mentioned in the report.")],
    )

    assert report["claims"][0]["status"] == "UNSUPPORTED"
    assert report["claims"][0]["evidence"] == []


def test_no_evidence_search_result_cannot_support_a_refusal_claim() -> None:
    report = EvidenceChecker().verify(
        "No local evidence found for: unobtainium",
        [
            {
                "step_id": 1,
                "tool": "search",
                "input": {"query": "unobtainium"},
                "output": "No local evidence found for: unobtainium",
                "error": None,
            }
        ],
    )

    assert report["claims"][0]["status"] == "UNSUPPORTED"
    assert report["claims"][0]["evidence"] == []


def test_conflicting_capital_and_numeric_values_are_contradicted() -> None:
    checker = EvidenceChecker()
    capital = checker.verify(
        "The capital of France is Rome.",
        [_step(1, "The capital of France is Paris.")],
    )
    numeric = checker.verify(
        "The result is 442.",
        [_step(2, 441, tool="calculator")],
    )

    assert capital["claims"][0]["status"] == "CONTRADICTED"
    assert numeric["claims"][0]["status"] == "CONTRADICTED"


def test_numerical_claim_and_ticket_values_are_checked_exactly() -> None:
    checker = EvidenceChecker()
    number = checker.verify(
        "The result is 441.",
        [_step(5, 441, tool="calculator")],
    )
    ticket = checker.verify(
        "Ticket T-104 is closed and owned by Avery.",
        [
            _step(
                6,
                {"ticket_id": "T-104", "status": "closed", "owner": "Avery"},
                tool="mock_api",
            )
        ],
    )

    assert number["claims"][0]["status"] == "SUPPORTED"
    assert ticket["claims"][0]["status"] == "SUPPORTED"


def test_multiple_relevant_sources_are_preserved_and_conflicts_win() -> None:
    report = EvidenceChecker().verify(
        "The capital of France is Paris.",
        [
            _step(1, "The capital of France is Paris."),
            _step(2, "The capital of France is Lyon."),
        ],
    )

    claim = report["claims"][0]
    assert claim["status"] == "CONTRADICTED"
    assert [item["step_id"] for item in claim["evidence"]] == [1, 2]
    assert [item["relation"] for item in claim["evidence"]] == [
        "SUPPORTED",
        "CONTRADICTED",
    ]


def test_unrelated_numeric_tool_outputs_do_not_contradict_each_other() -> None:
    report = EvidenceChecker().verify(
        "sum=7; product=12",
        [
            _step(
                1,
                7,
                tool="calculator",
                input={"expression": "3 + 4"},
            ),
            _step(
                2,
                12,
                tool="python_exec",
                input={"code": "3 * 4"},
            ),
        ],
    )

    assert [claim["status"] for claim in report["claims"]] == [
        "SUPPORTED",
        "SUPPORTED",
    ]
    assert [item["step_id"] for item in report["claims"][0]["evidence"]] == [1]
    assert [item["step_id"] for item in report["claims"][1]["evidence"]] == [2]


def test_failed_tool_outputs_and_failed_recoveries_are_not_evidence() -> None:
    failed_tool = _step(
        1,
        "The capital of France is Paris.",
        error="TimeoutError: simulated timeout",
    )
    failed_recovery = _step(
        2,
        "The capital of France is Paris.",
        recovery_success=False,
        recovery_attempt=True,
    )
    report = EvidenceChecker().verify(
        "The capital of France is Paris.",
        [failed_tool, failed_recovery],
    )

    assert report["claims"][0]["status"] == "UNSUPPORTED"
    assert report["claims"][0]["evidence"] == []


def test_failed_recovery_log_output_is_not_used_as_evidence() -> None:
    failed_attempt = {
        "record_type": "recovery_attempt",
        "original_step_id": 1,
        "attempt_number": 1,
        "recovery_success": False,
        "recovery_execution_result": {
            "output": "The capital of France is Paris.",
            "detector_failure_detected": True,
        },
    }
    report = EvidenceChecker().verify(
        "The capital of France is Paris.",
        [],
        recovery_report={
            "failures": [
                {
                    "attempts": [failed_attempt],
                }
            ]
        },
        recovery_records=[failed_attempt],
    )

    assert report["claims"][0]["status"] == "UNSUPPORTED"
    assert report["claims"][0]["evidence"] == []


def test_injected_failure_output_is_excluded_independently() -> None:
    report = EvidenceChecker().verify(
        "The capital of France is Paris.",
        [
            _step(
                1,
                "The capital of France is Paris.",
                failure_injected=True,
            )
        ],
    )

    assert report["claims"][0]["status"] == "UNSUPPORTED"


def test_grounding_counts_and_no_claims_status() -> None:
    checker = EvidenceChecker()
    report = checker.verify(
        "The capital of France is Paris. The capital of Spain is Madrid.",
        [_step(1, "The capital of France is Paris.")],
    )
    empty = checker.verify("", [])

    assert report["total_claim_count"] == 2
    assert report["supported_count"] == 1
    assert report["unsupported_count"] == 1
    assert report["contradicted_count"] == 0
    assert report["grounding_score"] == 0.5
    assert empty["status"] == "NO_CLAIMS"
    assert empty["grounding_score"] is None


def test_agent_cites_detector_validated_successful_recovery(tmp_path) -> None:
    class FailOnceInjector:
        def __init__(self) -> None:
            self.calls = 0
            self.normal = FailureInjector(enabled=False)

        def execute(self, tool_name, tool, arguments):
            self.calls += 1
            if self.calls == 1:
                copied = dict(arguments)
                return ToolExecution(
                    output=None,
                    error="TimeoutError: first call timed out",
                    failure_type=FailureType.TIMEOUT,
                    failure_injected=True,
                    original_arguments=copied,
                    execution_arguments=copied,
                    execution_status="injected_failure",
                )
            return self.normal.execute(tool_name, tool, arguments)

    result = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailOnceInjector(),
        log_directory=tmp_path,
    ).run("What is the capital of France?")

    claim = result.grounding_report["claims"][0]
    assert claim["status"] == "SUPPORTED"
    assert claim["evidence"][0]["step_id"] == 2
    assert claim["evidence"][0]["source_type"] == "successful_recovery"
    assert "[SUPPORTED; evidence: step 2]" in result.grounded_answer
    assert result.grounding_report_path.exists()


def test_multihop_search_prose_and_calculator_result_do_not_cross_contradict() -> None:
    report = EvidenceChecker().verify(
        "Aerospace Telemetry Metric M-1 reports orbital booster stage-1 chamber pressure at 420 bar. "
        "The derived calculation equals 855.",
        [
            _step(
                1,
                "Aerospace Telemetry Metric M-1 reports orbital booster stage-1 chamber pressure at 420 bar.",
                tool="search",
                input={"query": "aerospace telemetry metric M-1"},
            ),
            _step(
                2,
                855,
                tool="calculator",
                input={"expression": "420 * 2 + 15"},
            ),
        ],
    )

    assert [claim["status"] for claim in report["claims"]] == [
        "SUPPORTED",
        "SUPPORTED",
    ]
    assert report["grounding_score"] == 1.0
    assert [ev["step_id"] for ev in report["claims"][0]["evidence"]] == [1]
    assert [ev["step_id"] for ev in report["claims"][1]["evidence"]] == [2]
