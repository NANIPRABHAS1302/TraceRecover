"""Tests for configurable, reproducible tool failure injection."""

import json

import pytest

from agent.react_agent import ReactAgent
from config import LLMClient
from tools.failure_injector import FailureInjector, FailureType


def _tool(arguments: dict[str, object]) -> object:
    return arguments.get("value", 41)


def test_failure_injection_can_be_disabled() -> None:
    injector = FailureInjector(failure_rate=100, seed=1, enabled=False)

    result = injector.execute("sample", _tool, {"value": 42})

    assert result.output == 42
    assert result.failure_injected is False
    assert result.execution_status == "success"


def test_zero_percent_keeps_normal_tool_result() -> None:
    injector = FailureInjector(failure_rate=0, seed=1)

    result = injector.execute("sample", _tool, {"value": 42})

    assert result.output == 42
    assert result.failure_injected is False


def test_empty_enabled_types_disables_all_failure_modes() -> None:
    injector = FailureInjector(
        failure_rate=100,
        seed=1,
        enabled_types=[],
    )

    result = injector.execute("sample", _tool, {"value": 42})

    assert result.output == 42
    assert result.failure_injected is False


@pytest.mark.parametrize(
    ("failure_type", "expected_error"),
    [
        (FailureType.TIMEOUT, "TimeoutError"),
        (FailureType.BAD_ARGUMENTS, "ValueError"),
    ],
)
def test_error_failures_are_reported(
    failure_type: FailureType, expected_error: str
) -> None:
    injector = FailureInjector(
        failure_rate=100,
        seed=3,
        enabled_types=[failure_type],
    )

    result = injector.execute("sample", _tool, {"value": 42})

    assert result.failure_type is failure_type
    assert result.failure_injected is True
    assert result.execution_status == "injected_failure"
    assert result.error is not None
    assert expected_error in result.error
    if failure_type is FailureType.BAD_ARGUMENTS:
        assert result.execution_arguments == {}
        assert result.original_arguments == {"value": 42}


def test_wrong_output_is_corrupted() -> None:
    injector = FailureInjector(
        failure_rate=100,
        seed=2,
        enabled_types=[FailureType.WRONG_OUTPUT],
    )

    result = injector.execute("sample", _tool, {"value": 42})

    assert result.output == 43
    assert result.failure_type is FailureType.WRONG_OUTPUT
    assert result.failure_injected is True


def test_empty_result_replaces_tool_output() -> None:
    injector = FailureInjector(
        failure_rate=100,
        seed=2,
        enabled_types=[FailureType.EMPTY_RESULT],
    )

    result = injector.execute("sample", _tool, {"value": 42})

    assert result.output is None
    assert result.error is None
    assert result.failure_type is FailureType.EMPTY_RESULT
    assert result.failure_injected is True


def test_same_seed_reproduces_failure_sequence() -> None:
    def sequence(seed: int) -> list[str | None]:
        injector = FailureInjector(failure_rate=40, seed=seed)
        result: list[str | None] = []
        for _ in range(20):
            execution = injector.execute("sample", _tool, {"value": 42})
            result.append(
                execution.failure_type.value if execution.failure_type else None
            )
        return result

    assert sequence(42) == sequence(42)


def test_higher_failure_rate_injects_more_failures() -> None:
    def count_failures(rate: float) -> int:
        injector = FailureInjector(failure_rate=rate, seed=42)
        return sum(
            injector.execute("sample", _tool, {"value": 42}).failure_injected
            for _ in range(1000)
        )

    failures_at_20 = count_failures(20)
    failures_at_40 = count_failures(40)

    assert 100 <= failures_at_20 <= 300
    assert 300 <= failures_at_40 <= 500
    assert failures_at_40 > failures_at_20


@pytest.mark.parametrize("failure_type", list(FailureType))
def test_injected_failure_is_written_to_jsonl_trace(
    tmp_path, failure_type: FailureType
) -> None:
    injector = FailureInjector(
        failure_rate=100,
        seed=4,
        enabled_types=[failure_type],
    )
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=injector,
        log_directory=tmp_path,
    )

    result = agent.run("What is the capital of France?")
    logged_step = json.loads(result.log_path.read_text(encoding="utf-8").splitlines()[0])

    assert logged_step["tool_name"] == "search"
    assert logged_step["failure_type"] == failure_type.value
    assert logged_step["failure_injected"] is True
    assert logged_step["original_input"] == {
        "query": "What is the capital of France?"
    }
    if failure_type is FailureType.BAD_ARGUMENTS:
        assert logged_step["modified_input"] == {}
    else:
        assert logged_step["modified_input"] == logged_step["original_input"]
    assert logged_step["execution_status"] == "injected_failure"
    assert logged_step["timestamp"]
    if failure_type in {FailureType.TIMEOUT, FailureType.BAD_ARGUMENTS}:
        assert logged_step["error"] is not None
    elif failure_type is FailureType.EMPTY_RESULT:
        assert logged_step["modified_output"] is None
        assert logged_step["error"] is None
    else:
        assert logged_step["modified_output"] is not None


def test_agent_operates_normally_when_injection_is_disabled(tmp_path) -> None:
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailureInjector(enabled=False, failure_rate=100),
        log_directory=tmp_path,
    )

    result = agent.run("What is the capital of France?")

    assert result.answer == "The capital of France is Paris."
    assert result.trace[0]["output"] == "The capital of France is Paris."
    assert "failure_injected" not in result.trace[0]
