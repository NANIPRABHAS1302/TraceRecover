"""DeBERTa-v3 NLI claim verification with deterministic offline fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


SUPPORTED_NLI_MODELS = (
    "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
    "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli",
)

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9-])[-+]?\d[\d,]*(?:\.\d+)?%?")
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|none|neither|nor|cannot|can't|isn't|aren't|wasn't|weren't|doesn't|don't|didn't)\b",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "of", "on", "or", "that", "the", "to", "was",
    "were", "with",
}
_ANTONYM_PAIRS = (
    ("open", "closed"),
    ("open", "resolved"),
    ("closed", "pending"),
    ("true", "false"),
    ("active", "inactive"),
    ("enabled", "disabled"),
    ("pass", "fail"),
    ("passed", "failed"),
    ("high", "low"),
    ("increase", "decrease"),
)


@dataclass(frozen=True)
class NLIResult:
    """Three-way NLI classification between a premise and hypothesis."""

    label: str
    scores: dict[str, float]
    backend: str
    model_name: str
    premise: str
    hypothesis: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize NLI output for grounding reports."""
        return {
            "label": self.label,
            "scores": dict(self.scores),
            "backend": self.backend,
            "model_name": self.model_name,
        }


class DebertaNLIVerifier:
    """Verify claim entailment/contradiction via DeBERTa-v3 or offline fallback.

    When ``transformers`` and ``torch`` are installed and the requested model
    weights are available, predictions come from the sequence-classification
    DeBERTa-v3 NLI head. In offline CI environments without model weights or
    PyTorch installed, the verifier falls back cleanly to a deterministic
    lexical/numeric/polarity NLI heuristic when ``allow_fallback=True``.
    """

    def __init__(
        self,
        model_name: str = SUPPORTED_NLI_MODELS[0],
        *,
        device: str | None = None,
        entailment_threshold: float = 0.55,
        contradiction_threshold: float = 0.55,
        local_files_only: bool = True,
        allow_fallback: bool = True,
        force_fallback: bool = False,
    ) -> None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string.")
        if not 0.0 <= entailment_threshold <= 1.0:
            raise ValueError("entailment_threshold must be between 0 and 1.")
        if not 0.0 <= contradiction_threshold <= 1.0:
            raise ValueError("contradiction_threshold must be between 0 and 1.")
        self.model_name = model_name.strip()
        self.device = device
        self.entailment_threshold = float(entailment_threshold)
        self.contradiction_threshold = float(contradiction_threshold)
        self.local_files_only = bool(local_files_only)
        self.allow_fallback = bool(allow_fallback)
        self.force_fallback = bool(force_fallback)

        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._id2label: dict[int, str] = {}
        self._load_attempted = False
        self._backend = "deterministic_offline_fallback" if force_fallback else "uninitialized"

    @property
    def backend_name(self) -> str:
        """Return the active inference backend identifier."""
        if not self._load_attempted and not self.force_fallback:
            self._ensure_loaded()
        return self._backend

    @property
    def is_transformer_loaded(self) -> bool:
        """Return True when DeBERTa-v3 transformer weights are active."""
        return self._model is not None and self._tokenizer is not None

    def _ensure_loaded(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        if self.force_fallback:
            self._backend = "deterministic_offline_fallback"
            return
        try:
            import torch  # type: ignore[import-not-found]
            from transformers import (  # type: ignore[import-not-found]
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                local_files_only=self.local_files_only,
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                self.model_name,
                local_files_only=self.local_files_only,
            )
            target_device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            model.to(target_device)
            model.eval()

            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model
            raw_map = getattr(model.config, "id2label", None) or {
                0: "ENTAILMENT",
                1: "NEUTRAL",
                2: "CONTRADICTION",
            }
            self._id2label = {
                int(idx): str(lbl).upper() for idx, lbl in raw_map.items()
            }
            self._backend = "deberta_v3"
        except Exception as exc:
            if not self.allow_fallback:
                raise RuntimeError(
                    f"Could not load DeBERTa-v3 NLI model {self.model_name!r}: {exc}"
                ) from exc
            self._tokenizer = None
            self._model = None
            self._torch = None
            self._backend = "deterministic_offline_fallback"

    def predict(self, premise: str, hypothesis: str) -> NLIResult:
        """Classify whether premise entails, contradicts, or is neutral to hypothesis."""
        if not isinstance(premise, str) or not isinstance(hypothesis, str):
            raise TypeError("premise and hypothesis must be strings.")
        self._ensure_loaded()
        if self.is_transformer_loaded:
            return self._predict_transformer(premise, hypothesis)
        return self._predict_fallback(premise, hypothesis)

    def predict_batch(
        self, pairs: Sequence[tuple[str, str]]
    ) -> list[NLIResult]:
        """Run NLI classification over a sequence of (premise, hypothesis) pairs."""
        return [self.predict(premise, hypothesis) for premise, hypothesis in pairs]

    def to_claim_relation(self, result: NLIResult) -> str | None:
        """Map an NLIResult to TraceRecover's claim relation label."""
        if (
            result.label == "CONTRADICTION"
            and result.scores.get("CONTRADICTION", 0.0) >= self.contradiction_threshold
        ):
            return "CONTRADICTED"
        if (
            result.label == "ENTAILMENT"
            and result.scores.get("ENTAILMENT", 0.0) >= self.entailment_threshold
        ):
            return "SUPPORTED"
        return None

    def _predict_transformer(self, premise: str, hypothesis: str) -> NLIResult:
        torch = self._torch
        encoded = self._tokenizer(
            premise,
            hypothesis,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        target_device = next(self._model.parameters()).device
        encoded = {key: value.to(target_device) for key, value in encoded.items()}
        with torch.no_grad():
            logits = self._model(**encoded).logits[0]
            probs = torch.softmax(logits, dim=-1).detach().cpu().tolist()

        scores = {"ENTAILMENT": 0.0, "NEUTRAL": 0.0, "CONTRADICTION": 0.0}
        for idx, prob in enumerate(probs):
            raw_label = self._id2label.get(idx, "NEUTRAL").upper()
            if "ENTAIL" in raw_label:
                scores["ENTAILMENT"] = float(prob)
            elif "CONTRA" in raw_label:
                scores["CONTRADICTION"] = float(prob)
            else:
                scores["NEUTRAL"] = float(prob)

        best_label = max(scores, key=lambda key: scores[key])
        return NLIResult(
            label=best_label,
            scores=scores,
            backend="deberta_v3",
            model_name=self.model_name,
            premise=premise,
            hypothesis=hypothesis,
        )

    def _predict_fallback(self, premise: str, hypothesis: str) -> NLIResult:
        p_norm = self._normalize(premise)
        h_norm = self._normalize(hypothesis)
        if not p_norm or not h_norm:
            return self._make_fallback_result(
                premise, hypothesis, "NEUTRAL", 0.05, 0.90, 0.05
            )

        p_tokens = self._content_tokens(p_norm)
        h_tokens = self._content_tokens(h_norm)
        if not p_tokens or not h_tokens:
            return self._make_fallback_result(
                premise, hypothesis, "NEUTRAL", 0.05, 0.90, 0.05
            )

        p_neg = bool(_NEGATION_RE.search(premise))
        h_neg = bool(_NEGATION_RE.search(hypothesis))
        p_nums = self._number_set(premise)
        h_nums = self._number_set(hypothesis)

        overlap_tokens = h_tokens.intersection(p_tokens)
        containment = len(overlap_tokens) / len(h_tokens)

        # Direct normalized substring match without negation flip -> high entailment
        if h_norm in p_norm and p_neg == h_neg:
            return self._make_fallback_result(
                premise, hypothesis, "ENTAILMENT", 0.95, 0.04, 0.01
            )

        # Antonym conflict on overlapping subject
        non_num_overlap = {
            tok for tok in overlap_tokens if not _NUMBER_RE.fullmatch(tok)
        }
        if non_num_overlap:
            for left, right in _ANTONYM_PAIRS:
                if (left in p_tokens and right in h_tokens) or (
                    right in p_tokens and left in h_tokens
                ):
                    return self._make_fallback_result(
                        premise, hypothesis, "CONTRADICTION", 0.04, 0.11, 0.85
                    )

        # Polarity flip with substantial shared content -> contradiction
        if p_neg != h_neg and containment >= 0.5:
            return self._make_fallback_result(
                premise, hypothesis, "CONTRADICTION", 0.05, 0.10, 0.85
            )

        # Numeric mismatch on shared lexical context -> contradiction
        if p_nums and h_nums and not h_nums.issubset(p_nums):
            non_num_h = {tok for tok in h_tokens if not _NUMBER_RE.fullmatch(tok)}
            non_num_containment = (
                len(non_num_overlap) / len(non_num_h) if non_num_h else 0.0
            )
            if non_num_containment >= 0.45:
                return self._make_fallback_result(
                    premise, hypothesis, "CONTRADICTION", 0.05, 0.12, 0.83
                )

        # High content containment with compatible numbers and polarity -> entailment
        if containment >= 0.65 and (not h_nums or h_nums.issubset(p_nums)):
            ent_score = min(0.94, 0.55 + 0.40 * containment)
            rem = 1.0 - ent_score
            return self._make_fallback_result(
                premise,
                hypothesis,
                "ENTAILMENT",
                round(ent_score, 4),
                round(rem * 0.8, 4),
                round(rem * 0.2, 4),
            )

        # Insufficient semantic alignment -> neutral
        neutral_score = round(max(0.55, 0.92 - 0.35 * containment), 4)
        ent_score = round((1.0 - neutral_score) * 0.7, 4)
        contra_score = round(max(0.0, 1.0 - neutral_score - ent_score), 4)
        return self._make_fallback_result(
            premise, hypothesis, "NEUTRAL", ent_score, neutral_score, contra_score
        )

    def _make_fallback_result(
        self,
        premise: str,
        hypothesis: str,
        label: str,
        entailment: float,
        neutral: float,
        contradiction: float,
    ) -> NLIResult:
        return NLIResult(
            label=label,
            scores={
                "ENTAILMENT": entailment,
                "NEUTRAL": neutral,
                "CONTRADICTION": contradiction,
            },
            backend="deterministic_offline_fallback",
            model_name=self.model_name,
            premise=premise,
            hypothesis=hypothesis,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()

    @staticmethod
    def _content_tokens(normalized_text: str) -> set[str]:
        return {
            token
            for token in normalized_text.split()
            if token and token not in _STOP_WORDS
        }

    @staticmethod
    def _number_set(text: str) -> set[float]:
        values: set[float] = set()
        for match in _NUMBER_RE.finditer(text):
            raw = match.group(0).replace(",", "").rstrip("%")
            try:
                values.add(float(raw))
            except ValueError:
                continue
        return values
