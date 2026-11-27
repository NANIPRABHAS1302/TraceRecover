"""Standalone 4-bit QLoRA fine-tuning script for TraceRecover's failure-recovery policy model.

Target Base Model: Qwen/Qwen2.5-7B-Instruct
QLoRA Hyperparameters:
  - 4-bit NF4 quantization with double quantization (bfloat16 compute)
  - LoRA rank r = 16, lora_alpha = 32, lora_dropout = 0.05
  - Target modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
  - Learning rate: 2e-4, cosine scheduler, 3 epochs, seed = 42

Supports `--dry-run` on CPU without requiring GPU hardware or heavy ML packages to validate
dataset integrity, zero task_id leakage, and exact trainable LoRA parameter counts.
"""

from __future__ import annotations

import argparse
import collections
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAIN_JSONL = PROJECT_ROOT / "data" / "qlora_train.jsonl"
DEFAULT_VAL_JSONL = PROJECT_ROOT / "data" / "qlora_val.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "finetuning" / "checkpoints" / "qwen2.5-7b-tracerecover-qlora"


@dataclass(frozen=True)
class QLoRAConfig:
    model_name: str = "Qwen/Qwen2.5-7B-Instruct"
    load_in_4bit: bool = True
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_use_double_quant: bool = True
    bnb_4bit_compute_dtype: str = "bfloat16"
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple[str, ...] = (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    )
    learning_rate: float = 2e-4
    lr_scheduler_type: str = "cosine"
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 4
    gradient_accumulation_steps: int = 4
    warmup_ratio: float = 0.05
    max_seq_length: int = 512
    seed: int = 42


# Official Qwen/Qwen2.5-7B-Instruct published transformer dimensions
_QWEN25_7B_ARCH: dict[str, int] = {
    "num_hidden_layers": 28,
    "hidden_size": 3584,
    "num_attention_heads": 28,
    "num_key_value_heads": 4,
    "head_dim": 128,
    "intermediate_size": 18944,
    "base_total_parameters": 7_615_616_512,
}


def compute_exact_lora_parameter_counts(cfg: QLoRAConfig) -> dict[str, Any]:
    """Compute exact trainable LoRA adapter parameter counts for Qwen/Qwen2.5-7B-Instruct."""
    h = _QWEN25_7B_ARCH["hidden_size"]
    kv_dim = _QWEN25_7B_ARCH["num_key_value_heads"] * _QWEN25_7B_ARCH["head_dim"]
    ffn = _QWEN25_7B_ARCH["intermediate_size"]
    layers = _QWEN25_7B_ARCH["num_hidden_layers"]
    r = cfg.lora_r

    module_shapes: dict[str, tuple[int, int]] = {
        "q_proj": (h, h),
        "k_proj": (h, kv_dim),
        "v_proj": (h, kv_dim),
        "o_proj": (h, h),
        "gate_proj": (h, ffn),
        "up_proj": (h, ffn),
        "down_proj": (ffn, h),
    }

    per_layer_breakdown: dict[str, int] = {}
    for mod in cfg.target_modules:
        if mod in module_shapes:
            in_dim, out_dim = module_shapes[mod]
            per_layer_breakdown[mod] = r * (in_dim + out_dim)

    trainable_per_layer = sum(per_layer_breakdown.values())
    trainable_params = trainable_per_layer * layers
    base_params = _QWEN25_7B_ARCH["base_total_parameters"]
    total_params = base_params + trainable_params
    trainable_pct = (trainable_params / total_params) * 100.0

    return {
        "model_name": cfg.model_name,
        "num_hidden_layers": layers,
        "lora_r": r,
        "lora_alpha": cfg.lora_alpha,
        "lora_dropout": cfg.lora_dropout,
        "target_modules": list(cfg.target_modules),
        "per_layer_trainable_params": trainable_per_layer,
        "per_layer_module_params": per_layer_breakdown,
        "trainable_lora_params": trainable_params,
        "frozen_base_params": base_params,
        "total_params_with_adapters": total_params,
        "trainable_percentage": round(trainable_pct, 4),
    }


def validate_jsonl_dataset(train_path: Path, val_path: Path) -> dict[str, Any]:
    """Validate QLoRA train/val JSONL records and assert zero task_id leakage."""
    if not train_path.is_file():
        raise FileNotFoundError(
            f"Training JSONL not found at {train_path}. Run `python finetuning/prepare_qlora_dataset.py` first."
        )
    if not val_path.is_file():
        raise FileNotFoundError(
            f"Validation JSONL not found at {val_path}. Run `python finetuning/prepare_qlora_dataset.py` first."
        )

    def _load(path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            msgs = item.get("messages")
            if not isinstance(msgs, list) or len(msgs) != 3:
                raise ValueError(f"{path.name}:{idx}: expected 3 chat messages (system, user, assistant).")
            roles = [m.get("role") for m in msgs]
            if roles != ["system", "user", "assistant"]:
                raise ValueError(f"{path.name}:{idx}: unexpected message roles {roles}.")
            target_json = json.loads(msgs[2]["content"])
            if "failure_type" not in target_json or "recovery_action" not in target_json:
                raise ValueError(f"{path.name}:{idx}: assistant target missing required keys.")
            records.append(item)
        return records

    train_recs = _load(train_path)
    val_recs = _load(val_path)
    train_tasks = {str(r.get("task_id")) for r in train_recs}
    val_tasks = {str(r.get("task_id")) for r in val_recs}
    overlap = train_tasks.intersection(val_tasks)
    if overlap:
        raise ValueError(f"Task-ID leakage detected between train and val splits: {sorted(overlap)[:5]}")

    return {
        "train_examples": len(train_recs),
        "val_examples": len(val_recs),
        "train_unique_tasks": len(train_tasks),
        "val_unique_tasks": len(val_tasks),
        "task_id_overlap": 0,
        "train_failure_types": dict(collections.Counter(r["failure_type"] for r in train_recs)),
        "val_failure_types": dict(collections.Counter(r["failure_type"] for r in val_recs)),
        "train_recovery_actions": dict(collections.Counter(r["recovery_action"] for r in train_recs)),
        "val_recovery_actions": dict(collections.Counter(r["recovery_action"] for r in val_recs)),
    }


def run_dry_run(cfg: QLoRAConfig, train_path: Path, val_path: Path) -> dict[str, Any]:
    """Validate datasets and report exact trainable LoRA parameter counts on CPU without GPU."""
    data_report = validate_jsonl_dataset(train_path, val_path)
    param_report = compute_exact_lora_parameter_counts(cfg)
    steps_per_epoch = max(
        1,
        data_report["train_examples"] // (cfg.per_device_train_batch_size * cfg.gradient_accumulation_steps),
    )
    report = {
        "mode": "dry_run_cpu_validation",
        "status": "validated",
        "hyperparameters": asdict(cfg),
        "dataset_validation": data_report,
        "parameter_budget": param_report,
        "estimated_optimizer_steps": steps_per_epoch * cfg.num_train_epochs,
    }
    return report


def run_training(cfg: QLoRAConfig, train_path: Path, val_path: Path, output_dir: Path) -> None:
    """Execute genuine 4-bit QLoRA training when GPU + transformers/peft/bitsandbytes are installed."""
    validate_jsonl_dataset(train_path, val_path)
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            DataCollatorForSeq2Seq,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Full QLoRA training requires GPU dependencies (`torch`, `transformers`, `peft`, "
            "`bitsandbytes`, `datasets`). Use `--dry-run` on CPU to validate data and LoRA parameter counts."
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for 4-bit bitsandbytes QLoRA training; use `--dry-run` on CPU.")

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=cfg.load_in_4bit,
        bnb_4bit_quant_type=cfg.bnb_4bit_quant_type,
        bnb_4bit_use_double_quant=cfg.bnb_4bit_use_double_quant,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    tokenizer = AutoTokenizer.from_pretrained(cfg.model_name, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        cfg.model_name,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=False,
    )
    model = prepare_model_for_kbit_training(model)
    lora_cfg = LoraConfig(
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(cfg.target_modules),
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    raw_ds = load_dataset("json", data_files={"train": str(train_path), "validation": str(val_path)})

    def _tokenize(example: dict[str, Any]) -> dict[str, Any]:
        rendered = tokenizer.apply_chat_template(example["messages"], tokenize=False, add_generation_prompt=False)
        tok = tokenizer(rendered, truncation=True, max_length=cfg.max_seq_length)
        tok["labels"] = list(tok["input_ids"])
        return tok

    tok_ds = raw_ds.map(_tokenize, remove_columns=raw_ds["train"].column_names)
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=cfg.num_train_epochs,
        per_device_train_batch_size=cfg.per_device_train_batch_size,
        per_device_eval_batch_size=cfg.per_device_train_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        learning_rate=cfg.learning_rate,
        lr_scheduler_type=cfg.lr_scheduler_type,
        warmup_ratio=cfg.warmup_ratio,
        bf16=True,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        seed=cfg.seed,
        report_to="none",
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tok_ds["train"],
        eval_dataset=tok_ds["validation"],
        data_collator=DataCollatorForSeq2Seq(tokenizer, pad_to_multiple_of=8),
    )
    trainer.train()
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))


def main() -> None:
    parser = argparse.ArgumentParser(description="4-bit QLoRA fine-tuning for TraceRecover policy model.")
    parser.add_argument("--train-jsonl", type=Path, default=DEFAULT_TRAIN_JSONL)
    parser.add_argument("--val-jsonl", type=Path, default=DEFAULT_VAL_JSONL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Validate data & print exact LoRA parameter counts on CPU.")
    args = parser.parse_args()

    cfg = QLoRAConfig()
    if args.dry_run:
        summary = run_dry_run(cfg, args.train_jsonl, args.val_jsonl)
        print(json.dumps(summary, indent=2))
    else:
        run_training(cfg, args.train_jsonl, args.val_jsonl, args.output_dir)


if __name__ == "__main__":
    main()
