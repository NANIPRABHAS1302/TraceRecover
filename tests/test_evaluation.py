"""Deterministic task schema, runner, and output-generation tests."""

import json
from pathlib import Path

import pytest

from eval.run_baseline import BASELINE_FEATURES
from eval.run_tracerecover import FULL_FEATURES, write_metrics_csv
from eval.runner import DatasetTaskPlanner, load_tasks, run_evaluation, write_json


def _task() -> dict:
    return {
        "task_id": "TEST-01",
        "query": "What is the capital of France?",
        "category": "general_knowledge",
        "expected_answer": "Paris",
        "expected_facts": ["Paris"],
        "contradictory_facts": ["Rome"],
        "expected_tool_category": "search",
        "should_abstain": False,
        "evaluation_criteria": "Paris must appear.",
        "oracle_plan": [
            {
                "tool": "search",
                "arguments": {"query": "capital france"},
            }
        ],
        "final_answer_template": "{0}",
    }


def _write_dataset(path, tasks: list[dict]) -> None:
    path.write_text(
        json.dumps({"dataset_version": "test-1", "tasks": tasks}),
        encoding="utf-8",
    )


def test_default_dataset_has_100_unique_schema_valid_tasks() -> None:
    dataset_path = Path(__file__).resolve().parents[1] / "eval" / "tasks.json"
    version, tasks, digest = load_tasks(dataset_path)

    assert version == "2.0"
    assert len(tasks) == 100
    assert len({task["task_id"] for task in tasks}) == 100
    assert len(digest) == 64
    assert {task["category"] for task in tasks} >= {
        "multi_hop_search_math",
        "multi_record_api_aggregation",
        "argument_repair_tool_switch",
        "missing_evidence_abstention",
        "numeric_contradiction_trap",
    }


def test_dataset_loader_rejects_wrong_count_duplicate_ids_and_bad_plan(
    tmp_path,
) -> None:
    dataset_path = tmp_path / "tasks.json"
    _write_dataset(dataset_path, [_task()])

    with pytest.raises(ValueError, match="Expected 100 tasks"):
        load_tasks(dataset_path)
    _write_dataset(dataset_path, [_task(), _task()])
    with pytest.raises(ValueError, match="Duplicate task_id"):
        load_tasks(dataset_path, expected_count=None)

    invalid = _task()
    invalid["oracle_plan"][0]["tool"] = "unknown"
    _write_dataset(dataset_path, [invalid])
    with pytest.raises(ValueError, match="invalid tool step"):
        load_tasks(dataset_path, expected_count=None)


def test_dataset_planner_executes_steps_then_formats_observations() -> None:
    planner = DatasetTaskPlanner(
        {
            "oracle_plan": [
                {"tool": "calculator", "arguments": {"expression": "2 + 3"}}
            ],
            "final_answer_template": "result={0}",
        }
    )

    action = planner.next_action("ignored", [])
    final = planner.next_action(
        "ignored", [{"output": 5, "tool": "calculator"}]
    )

    assert action["action"] == "calculator"
    assert final["action"] == "final"
    assert final["final_answer"] == "result=5"


def test_baseline_and_tracerecover_run_matched_dataset_and_write_outputs(
    tmp_path,
) -> None:
    dataset_path = tmp_path / "tasks.json"
    _write_dataset(dataset_path, [_task()])
    version, tasks, digest = load_tasks(dataset_path, expected_count=None)
    outputs = []
    for name, flags in (
        ("baseline-test", BASELINE_FEATURES),
        ("trace-test", FULL_FEATURES),
    ):
        result = run_evaluation(
            tasks=tasks,
            dataset_version=version,
            dataset_sha256=digest,
            system_name=name,
            failure_rates=[0],
            seed=42,
            log_root=tmp_path / "logs",
            feature_flags=flags,
        )
        condition = result["conditions"][0]
        outputs.append(condition["results"][0])
        assert condition["task_count"] == 1
        assert condition["metrics"]["task_success"]["denominator"] == 1
        assert (tmp_path / "logs" / name / "failure-0" / "TEST-01").exists()

    assert outputs[0]["task_seed"] == outputs[1]["task_seed"] == 42
    assert outputs[0]["answer"] == outputs[1]["answer"]
    assert outputs[0]["success"] is True
    assert outputs[1]["success"] is True
    assert outputs[0]["confidence"] is None
    assert outputs[1]["confidence"] is not None


def test_runner_is_reproducible_for_same_seed_and_rate(tmp_path) -> None:
    dataset_path = tmp_path / "tasks.json"
    _write_dataset(dataset_path, [_task()])
    version, tasks, digest = load_tasks(dataset_path, expected_count=None)
    outputs = []
    for run_number in range(2):
        result = run_evaluation(
            tasks=tasks,
            dataset_version=version,
            dataset_sha256=digest,
            system_name=f"repeat-{run_number}",
            failure_rates=[20],
            seed=17,
            log_root=tmp_path / "logs",
            feature_flags=BASELINE_FEATURES,
        )
        outputs.append(result["conditions"][0]["results"][0])

    assert outputs[0]["task_seed"] == outputs[1]["task_seed"]
    assert outputs[0]["answer"] == outputs[1]["answer"]
    assert outputs[0]["success"] == outputs[1]["success"]
    assert outputs[0]["claims"] == outputs[1]["claims"]


def test_abstained_original_answer_is_not_scored_as_user_facing_claim(tmp_path) -> None:
    task = _task()
    task.update(
        {
            "task_id": "API-UNKNOWN",
            "query": "What is the status of ticket T-999?",
            "category": "unanswerable_api",
            "expected_answer": None,
            "expected_facts": [],
            "contradictory_facts": [],
            "expected_tool_category": "mock_api",
            "should_abstain": True,
            "evaluation_criteria": "Unknown ticket should be withheld.",
            "oracle_plan": [
                {"tool": "mock_api", "arguments": {"ticket_id": "T-999"}}
            ],
            "final_answer_template": "{0}",
        }
    )
    dataset_path = tmp_path / "tasks.json"
    _write_dataset(dataset_path, [task])
    version, tasks, digest = load_tasks(dataset_path, expected_count=None)
    result = run_evaluation(
        tasks=tasks,
        dataset_version=version,
        dataset_sha256=digest,
        system_name="abstention-test",
        failure_rates=[0],
        seed=42,
        log_root=tmp_path / "logs",
        feature_flags=FULL_FEATURES,
    )

    sample = result["conditions"][0]["results"][0]
    assert sample["final_decision"] == "ABSTAIN"
    assert sample["evaluated_answer"] is None
    assert sample["factual_claim_count"] == 0
    assert sample["detected_natural_failures"] == 1


def test_result_json_and_metrics_csv_are_written(tmp_path) -> None:
    result = {
        "conditions": [
            {
                "failure_rate_percent": 0,
                "metrics": {
                    "task_count": 0,
                    "task_success": {
                        "successes": 0,
                        "denominator": 0,
                        "rate": None,
                        "confidence_interval_95": None,
                    },
                    "recovery": {
                        "detected_failures": 0,
                        "detected_injected_failures": 0,
                        "detected_natural_failures": 0,
                        "injected_tool_failures": 0,
                        "injected_recovery_failures": 0,
                        "successful_recoveries": 0,
                        "rate": None,
                    },
                    "hallucination": {
                        "answers_with_factual_claims": 0,
                        "hallucinating_answers": 0,
                        "rate": None,
                        "unsupported_claims": 0,
                        "contradicted_claims": 0,
                    },
                    "ece": {"ece": None, "sample_count": 0},
                    "abstention": {
                        "correct_abstentions": 0,
                        "incorrect_abstentions": 0,
                        "missed_abstentions": 0,
                        "abstention_precision": None,
                        "abstention_recall": None,
                    },
                    "latency": {
                        "average_seconds": None,
                        "median_seconds": None,
                    },
                },
            }
        ]
    }
    json_path = write_json(tmp_path / "results.json", result)
    csv_path = write_metrics_csv(tmp_path / "metrics.csv", {"test": result})

    assert json.loads(json_path.read_text(encoding="utf-8")) == result
    assert csv_path.read_text(encoding="utf-8").splitlines()[0].startswith("system,")
    assert "test,0,0,0,0,,,,0,0," in csv_path.read_text(encoding="utf-8")
