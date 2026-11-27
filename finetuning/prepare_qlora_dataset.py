"""Prepare task-grouped 80/20 QLoRA fine-tuning JSONL datasets from TraceRecover step telemetry.

Splits `data/tracerecover_step_level_telemetry.csv` strictly by `task_id` (stratified by
task category, seed=42) into `data/qlora_train.jsonl` and `data/qlora_val.jsonl` with
zero task-ID leakage between splits. Each training example teaches a compact policy model
to classify tool-execution failure types and predict the optimal recovery action.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_CSV = PROJECT_ROOT / "data" / "tracerecover_step_level_telemetry.csv"
DEFAULT_TRAIN_OUT = PROJECT_ROOT / "data" / "qlora_train.jsonl"
DEFAULT_VAL_OUT = PROJECT_ROOT / "data" / "qlora_val.jsonl"

SYSTEM_PROMPT = (
    "You are TraceRecover's tool-execution reliability and recovery policy model. "
    "Given a single agent step telemetry record, return ONLY a JSON object with keys "
    "`failure_type` (one of: none, timeout, empty_result, bad_arguments, wrong_output) "
    "and `recovery_action` (one of: PROCEED, RETRY, ARGUMENT_REPAIR, TOOL_SWITCH, REPLAN, ABSTAIN)."
)

_DEFAULT_ACTION_BY_FAILURE: dict[str, str] = {
    "none": "PROCEED",
    "timeout": "RETRY",
    "empty_result": "RETRY",
    "bad_arguments": "ARGUMENT_REPAIR",
    "wrong_output": "TOOL_SWITCH",
}


def _normalize_label(row: dict[str, str]) -> tuple[str, str]:
    failure_type = (row.get("failure_type") or "").strip().lower() or "none"
    raw_strategy = (row.get("recovery_strategy") or "").strip().upper()
    has_error = str(row.get("has_error", "")).strip().lower() == "true"

    if raw_strategy in {"RETRY", "ARGUMENT_REPAIR", "TOOL_SWITCH", "REPLAN"}:
        recovery_action = raw_strategy
    elif failure_type != "none":
        recovery_action = _DEFAULT_ACTION_BY_FAILURE.get(failure_type, "RETRY")
    elif has_error:
        recovery_action = "ABSTAIN"
    else:
        recovery_action = "PROCEED"
    return failure_type, recovery_action


def _row_to_example(row: dict[str, str]) -> dict[str, Any]:
    failure_type, recovery_action = _normalize_label(row)
    user_payload = {
        "task_id": row["task_id"],
        "category": row["category"],
        "system": row["system"],
        "failure_rate_percent": float(row.get("failure_rate_percent") or 0.0),
        "step_id": int(row.get("step_id") or 0),
        "tool": row["tool"],
        "has_error": str(row.get("has_error", "")).strip().lower() == "true",
        "latency_seconds": round(float(row.get("latency") or 0.0), 6),
    }
    assistant_payload = {
        "failure_type": failure_type,
        "recovery_action": recovery_action,
    }
    return {
        "task_id": row["task_id"],
        "category": row["category"],
        "failure_type": failure_type,
        "recovery_action": recovery_action,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Classify the tool step failure type and recommend the bounded recovery action:\n"
                    + json.dumps(user_payload, ensure_ascii=False)
                ),
            },
            {"role": "assistant", "content": json.dumps(assistant_payload, ensure_ascii=False)},
        ],
    }


def build_task_grouped_split(
    input_csv: Path = DEFAULT_INPUT_CSV,
    train_out: Path = DEFAULT_TRAIN_OUT,
    val_out: Path = DEFAULT_VAL_OUT,
    *,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> dict[str, Any]:
    """Create a stratified 80/20 task-grouped split with zero task_id overlap."""
    if not input_csv.is_file():
        raise FileNotFoundError(f"Telemetry CSV not found: {input_csv}")

    with input_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    # Exclude non-tool `final` summary steps so the model trains strictly on tool-execution steps
    tool_rows = [r for r in rows if (r.get("tool") or "").strip().lower() != "final"]

    tasks_by_cat: dict[str, list[str]] = collections.defaultdict(list)
    seen_tasks: set[str] = set()
    for r in tool_rows:
        tid = r["task_id"]
        if tid not in seen_tasks:
            seen_tasks.add(tid)
            tasks_by_cat[r["category"]].append(tid)

    rng = random.Random(seed)
    train_tasks: set[str] = set()
    val_tasks: set[str] = set()
    for cat in sorted(tasks_by_cat):
        tids = sorted(tasks_by_cat[cat])
        rng.shuffle(tids)
        n_val = max(1, round(len(tids) * val_ratio))
        val_tasks.update(tids[:n_val])
        train_tasks.update(tids[n_val:])

    assert train_tasks.isdisjoint(val_tasks), "Task leakage detected between train and val splits!"

    train_examples = [_row_to_example(r) for r in tool_rows if r["task_id"] in train_tasks]
    val_examples = [_row_to_example(r) for r in tool_rows if r["task_id"] in val_tasks]

    train_out.parent.mkdir(parents=True, exist_ok=True)
    val_out.parent.mkdir(parents=True, exist_ok=True)
    train_out.write_text(
        "\n".join(json.dumps(ex, ensure_ascii=False) for ex in train_examples) + "\n",
        encoding="utf-8",
    )
    val_out.write_text(
        "\n".join(json.dumps(ex, ensure_ascii=False) for ex in val_examples) + "\n",
        encoding="utf-8",
    )

    return {
        "input_csv": str(input_csv),
        "total_csv_rows": len(rows),
        "tool_step_rows": len(tool_rows),
        "seed": seed,
        "train_task_count": len(train_tasks),
        "val_task_count": len(val_tasks),
        "task_overlap": len(train_tasks.intersection(val_tasks)),
        "train_examples": len(train_examples),
        "val_examples": len(val_examples),
        "train_failure_distribution": dict(collections.Counter(e["failure_type"] for e in train_examples)),
        "val_failure_distribution": dict(collections.Counter(e["failure_type"] for e in val_examples)),
        "train_action_distribution": dict(collections.Counter(e["recovery_action"] for e in train_examples)),
        "val_action_distribution": dict(collections.Counter(e["recovery_action"] for e in val_examples)),
        "train_path": str(train_out),
        "val_path": str(val_out),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare 80/20 task-grouped QLoRA JSONL datasets.")
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT_CSV)
    parser.add_argument("--train-out", type=Path, default=DEFAULT_TRAIN_OUT)
    parser.add_argument("--val-out", type=Path, default=DEFAULT_VAL_OUT)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    summary = build_task_grouped_split(
        input_csv=args.input_csv,
        train_out=args.train_out,
        val_out=args.val_out,
        val_ratio=args.val_ratio,
        seed=args.seed,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
