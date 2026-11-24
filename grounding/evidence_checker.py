"""Deterministic claim verification against eligible tool-trace evidence."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from healing.detector import FailureDetector

from .claim_extractor import ClaimExtractor, FactualClaim
from .nli_verifier import DebertaNLIVerifier


def linearize_json(obj: Any, *, prefix: str = "") -> str:
    """Convert nested JSON/dict/list tool outputs into readable natural-language sentences."""
    if obj is None:
        return ""
    if isinstance(obj, bool):
        text = "true" if obj else "false"
        return f"{prefix} is {text}." if prefix else text
    if isinstance(obj, (int, float)):
        return f"{prefix} is {obj}." if prefix else str(obj)
    if isinstance(obj, str):
        cleaned = obj.strip()
        if not cleaned:
            return ""
        return f"{prefix} is {cleaned}." if prefix else cleaned
    if isinstance(obj, Mapping):
        if not obj:
            return ""
        clauses: list[str] = []
        subject = None
        for key in ("ticket_id", "id", "name", "sensor_id", "metric_id"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                subject = val.strip()
                break
        for key in sorted(obj.keys(), key=str):
            label = str(key).replace("_", " ").strip()
            full_label = f"{prefix} {label}".strip() if prefix else label
            val = obj[key]
            if isinstance(val, Mapping):
                nested = linearize_json(val, prefix=full_label)
                if nested:
                    clauses.append(nested.rstrip("."))
            elif isinstance(val, (list, tuple)):
                if not val:
                    clauses.append(f"{full_label} is empty")
                elif all(not isinstance(item, (Mapping, list, tuple)) for item in val):
                    joined = ", ".join(str(item) for item in val)
                    clauses.append(f"{full_label} are {joined}")
                else:
                    nested_items = [
                        linearize_json(item, prefix=f"{full_label} item {i}").rstrip(".")
                        for i, item in enumerate(val, start=1)
                    ]
                    clauses.append("; ".join(item for item in nested_items if item))
            elif val is not None and str(val).strip() != "":
                val_str = (
                    ("true" if val else "false")
                    if isinstance(val, bool)
                    else str(val).replace("_", " ").strip()
                )
                clauses.append(f"{full_label} is {val_str}")
        if not clauses:
            return ""
        body = ", ".join(clauses)
        if subject and not prefix:
            return f"Record {subject}: {body}."
        return f"{body}."
    if isinstance(obj, (list, tuple)):
        if not obj:
            return ""
        if all(not isinstance(item, (Mapping, list, tuple)) for item in obj):
            joined = ", ".join(str(item) for item in obj)
            return f"{prefix} are {joined}." if prefix else f"{joined}."
        sentences = [
            linearize_json(item, prefix=f"{prefix} item {i}".strip())
            for i, item in enumerate(obj, start=1)
        ]
        return " ".join(sentence for sentence in sentences if sentence)
    return f"{prefix} is {obj}." if prefix else str(obj)


_STATUSES = ("in progress", "in_progress", "open", "closed", "pending", "resolved")
_NUMBER = re.compile(r"(?<![A-Za-z0-9-])[-+]?\d[\d,]*(?:\.\d+)?%?")
_CAPITAL_FACTS = (
    re.compile(
        r"\bcapital\s+of\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)"
        r"\s+(?:is|was)\s+(?:the\s+)?([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)\s+"
        r"(?:is|was)\s+(?:the\s+)?capital\s+of\s+"
        r"([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)",
        re.IGNORECASE,
    ),
)
_CAPITAL_NEGATIVE = re.compile(
    r"\bcapital\s+of\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)"
    r"\s+(?:is|was)\s+not\s+(?:the\s+)?([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)",
    re.IGNORECASE,
)


class EvidenceChecker:
    """Verify claims conservatively and cite only successful trace steps."""

    def __init__(
        self,
        claim_extractor: ClaimExtractor | None = None,
        failure_detector: FailureDetector | None = None,
        *,
        use_nli: bool = False,
        nli_verifier: DebertaNLIVerifier | None = None,
    ) -> None:
        if not isinstance(use_nli, bool):
            raise TypeError("use_nli must be a boolean.")
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.failure_detector = failure_detector or FailureDetector()
        self.use_nli = use_nli
        self.nli_verifier = (
            nli_verifier
            if nli_verifier is not None
            else (DebertaNLIVerifier() if use_nli else None)
        )

    def verify(
        self,
        answer: str,
        trace: Sequence[Mapping[str, Any]],
        *,
        query: str = "",
        recovery_report: Mapping[str, Any] | None = None,
        recovery_records: Sequence[Mapping[str, Any]] = (),
        failure_report: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return claim statuses, evidence citations, and a grounding score."""
        claims = self.claim_extractor.extract(answer)
        sources = self._eligible_sources(
            trace, recovery_report, recovery_records, failure_report
        )
        verified = [self._verify_claim(claim, sources) for claim in claims]
        counts = {
            status: sum(item["status"] == status for item in verified)
            for status in ("SUPPORTED", "UNSUPPORTED", "CONTRADICTED")
        }
        total = len(verified)
        no_claims = total == 0
        return {
            "query": query,
            "final_answer": answer,
            "grounded_answer": self._format_grounded_answer(answer, verified),
            "status": "NO_CLAIMS" if no_claims else "VERIFIED",
            "claims": verified,
            "grounding_score": (
                counts["SUPPORTED"] / total if total else None
            ),
            "supported_count": counts["SUPPORTED"],
            "unsupported_count": counts["UNSUPPORTED"],
            "contradicted_count": counts["CONTRADICTED"],
            "total_claim_count": total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _format_grounded_answer(
        answer: str, claims: Sequence[Mapping[str, Any]]
    ) -> str:
        if not claims:
            return answer
        lines: list[str] = []
        for claim in claims:
            step_ids = list(
                dict.fromkeys(
                    item["step_id"]
                    for item in claim["evidence"]
                    if "step_id" in item
                )
            )
            citations = (
                "; evidence: "
                + ", ".join(f"step {step_id}" for step_id in step_ids)
                if step_ids
                else ""
            )
            lines.append(f"{claim['claim']} [{claim['status']}{citations}]")
        return "\n".join(lines)

    def _eligible_sources(
        self,
        trace: Sequence[Mapping[str, Any]],
        recovery_report: Mapping[str, Any] | None,
        recovery_records: Sequence[Mapping[str, Any]],
        failure_report: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        validated_recoveries = self._validated_recovery_steps(
            recovery_report, recovery_records
        )
        failed_step_ids = {
            self._id_key(item.get("step_id"))
            for item in (failure_report or {}).get("failures", [])
            if isinstance(item, Mapping)
        }
        sources: list[dict[str, Any]] = []
        for step in trace:
            step_id = step.get("step_id")
            step_key = self._id_key(step_id)
            if step.get("tool") in {"final", "llm"}:
                continue
            recovery = validated_recoveries.get(step_key)
            if step_key in failed_step_ids or step.get("failure_injected") is True:
                continue
            if step.get("output_state") == "discarded":
                continue
            if recovery is None and (
                step.get("recovery_success") is False
                or step.get("recovery_attempt") is True
            ):
                continue
            if step.get("error") is not None:
                continue
            if self._empty(step.get("output")):
                continue
            if self._is_no_evidence_search_result(step):
                continue
            if not isinstance(step_id, (int, str)) or not str(step_id).strip():
                continue

            detection = self.failure_detector.detect_step(dict(step))
            if detection.failure_detected:
                continue
            if recovery is not None and not self._trace_matches_recovery(step, recovery):
                continue
            source = {
                "step_id": step_id,
                "tool": step.get("tool", "unknown"),
                "input": step.get("input"),
                "output": step.get("output"),
                "matched_text": self._render(step.get("output")),
                "source_type": (
                    "successful_recovery" if recovery is not None else "tool_trace"
                ),
            }
            if recovery is not None:
                source["original_step_id"] = recovery["original_step_id"]
                source["recovery_attempt_number"] = recovery["attempt_number"]
            sources.append(source)
        return sources

    @staticmethod
    def _is_no_evidence_search_result(step: Mapping[str, Any]) -> bool:
        output = step.get("output")
        return (
            step.get("tool") == "search"
            and isinstance(output, str)
            and output.strip().casefold().startswith("no local evidence found for:")
        )

    def _validated_recovery_steps(
        self,
        report: Mapping[str, Any] | None,
        records: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not report:
            return {}
        logged_attempts: dict[tuple[str, str], Mapping[str, Any]] = {}
        links: dict[tuple[str, str], Mapping[str, Any]] = {}
        for record in records:
            key = (
                self._id_key(record.get("original_step_id")),
                str(record.get("attempt_number")),
            )
            if record.get("record_type") == "recovery_attempt":
                logged_attempts[key] = record
            elif record.get("record_type") == "recovery_trace_link":
                links[key] = record

        validated: dict[str, dict[str, Any]] = {}
        for failure in report.get("failures", []):
            if not isinstance(failure, Mapping):
                continue
            for attempt in failure.get("attempts", []):
                if not isinstance(attempt, Mapping) or not attempt.get("recovery_success"):
                    continue
                execution = attempt.get("recovery_execution_result")
                if not isinstance(execution, Mapping):
                    continue
                if execution.get("detector_failure_detected") is not False:
                    continue
                original_id = attempt.get("original_step_id")
                number = attempt.get("attempt_number")
                key = (self._id_key(original_id), str(number))
                logged = logged_attempts.get(key)
                link = links.get(key)
                if not logged or not link:
                    continue
                logged_execution = logged.get("recovery_execution_result")
                if (
                    logged.get("recovery_success") is not True
                    or not isinstance(logged_execution, Mapping)
                    or logged_execution.get("detector_failure_detected") is not False
                    or link.get("recovery_success") is not True
                    or link.get("detector_validation_passed") is not True
                ):
                    continue
                step_id = link.get("step_id")
                if self._id_key(step_id) != self._id_key(
                    attempt.get("recovery_trace_step_id")
                ):
                    continue
                validated[self._id_key(step_id)] = {
                    "original_step_id": original_id,
                    "attempt_number": number,
                    "tool": attempt.get("recovery_tool"),
                    "input": execution.get("input"),
                    "output": execution.get("output"),
                }
        return validated

    @staticmethod
    def _trace_matches_recovery(
        step: Mapping[str, Any], recovery: Mapping[str, Any]
    ) -> bool:
        return (
            step.get("recovery_success") is True
            and step.get("detector_validation_passed") is True
            and step.get("recovery_attempt_number") == recovery["attempt_number"]
            and EvidenceChecker._id_key(step.get("original_step_id"))
            == EvidenceChecker._id_key(recovery["original_step_id"])
            and step.get("tool") == recovery["tool"]
            and step.get("input") == recovery["input"]
            and step.get("output") == recovery["output"]
        )

    def _verify_claim(
        self, claim: FactualClaim, sources: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        evidence: list[dict[str, Any]] = []
        relations: list[str] = []
        for source in sources:
            relation = self._compare(
                claim.claim, source["output"], source.get("input")
            )
            nli_meta: dict[str, Any] | None = None
            if self.use_nli and self.nli_verifier is not None:
                premise = linearize_json(source["output"])
                if premise:
                    nli_result = self.nli_verifier.predict(premise, claim.claim)
                    nli_relation = self.nli_verifier.to_claim_relation(nli_result)
                    nli_meta = nli_result.to_dict()
                    if relation is None:
                        relation = nli_relation
                    elif nli_relation == "CONTRADICTED" and relation != "CONTRADICTED":
                        # Only let NLI override if deterministic rule didn't already confirm exact numeric/structured match
                        if not isinstance(source["output"], (int, float)):
                            relation = "CONTRADICTED"
            if relation is None:
                continue
            relations.append(relation)
            item: dict[str, Any] = {
                "step_id": source["step_id"],
                "tool": source["tool"],
                "matched_text": source["matched_text"],
                "source_type": source["source_type"],
                "relation": relation,
            }
            if nli_meta is not None:
                item["nli"] = nli_meta
            evidence.append(item)

        if "CONTRADICTED" in relations:
            status = "CONTRADICTED"
            explanation = "At least one relevant tool output directly conflicts with the claim."
        elif "SUPPORTED" in relations:
            status = "SUPPORTED"
            explanation = "The claim is directly supported by the cited tool output."
        else:
            status = "UNSUPPORTED"
            explanation = (
                "No eligible tool output provides enough evidence to verify this claim."
            )
        return {
            "claim_id": claim.claim_id,
            "claim": claim.claim,
            "status": status,
            "evidence": evidence,
            "explanation": explanation,
        }

    def _compare(
        self, claim: str, output: Any, tool_input: Any = None
    ) -> str | None:
        claim_normalized = self._normalize(claim)
        output_text = self._render(output)
        output_normalized = self._normalize(output_text)
        if not claim_normalized or not output_normalized:
            return None

        claim_capital = self._capital_fact(claim)
        output_capital = self._capital_fact(output_text)
        if claim_capital and output_capital:
            claim_country, claim_city, claim_positive = claim_capital
            output_country, output_city, output_positive = output_capital
            if claim_country == output_country:
                if claim_positive and output_positive:
                    return "SUPPORTED" if claim_city == output_city else "CONTRADICTED"
                if claim_city == output_city:
                    return (
                        "CONTRADICTED"
                        if claim_positive != output_positive
                        else "SUPPORTED"
                    )

        structured_relation = self._structured_relation(claim, output)
        if structured_relation is not None:
            return structured_relation

        numeric_relation = self._numeric_relation(
            claim, output, output_text, tool_input
        )
        if numeric_relation is not None:
            return numeric_relation
        if claim_normalized in output_normalized:
            return "SUPPORTED"
        return None

    def _structured_relation(self, claim: str, output: Any) -> str | None:
        claim_facts = self._structured_facts(claim)
        output_facts = self._structured_facts(output)
        if not claim_facts:
            return None
        claim_id = claim_facts.get("ticket_id")
        output_id = output_facts.get("ticket_id")
        if claim_id and output_id and claim_id.casefold() != output_id.casefold():
            return None
        if claim_id and not output_id and claim_id.casefold() not in self._render(output).casefold():
            return None

        comparisons = [
            (field, claim_facts.get(field), output_facts.get(field))
            for field in ("status", "owner")
            if claim_facts.get(field) is not None
        ]
        if not comparisons:
            return None
        relevant = False
        for _, expected, actual in comparisons:
            if actual is None:
                continue
            relevant = True
            if str(expected).casefold() != str(actual).casefold():
                return "CONTRADICTED"
        return "SUPPORTED" if relevant else None

    def _numeric_relation(
        self, claim: str, output: Any, output_text: str, tool_input: Any = None
    ) -> str | None:
        claim_numbers = self._numbers(claim)
        if not claim_numbers:
            return None
        if not self._numeric_operation_matches(claim, tool_input):
            return None
        stripped_claim = claim.strip().rstrip(".!?")
        is_bare_number = bool(_NUMBER.fullmatch(stripped_claim))
        has_result_marker = bool(
            re.search(
                r"\b(answer|result|total|sum|product|difference|quotient|remainder|square|calculation|calculated|computed|value|threshold|reading|equals?)\b|=",
                claim,
                re.I,
            )
        )
        result_claim = is_bare_number or has_result_marker
        if isinstance(output, (int, float)) and not isinstance(output, bool):
            if not result_claim:
                return None
            return (
                "SUPPORTED"
                if self._number_key(claim_numbers[-1]) == self._number_key(output)
                else "CONTRADICTED"
            )

        output_numbers = self._numbers(output_text)
        if len(claim_numbers) == 1 and len(output_numbers) == 1 and result_claim:
            output_is_bare_number = bool(
                _NUMBER.fullmatch(output_text.strip().rstrip(".!?"))
            )
            claim_words = {
                tok
                for tok in self._skeleton(claim).split()
                if len(tok) > 2
            }
            output_words = {
                tok
                for tok in self._skeleton(output_text).split()
                if len(tok) > 2
            }
            if output_is_bare_number or len(claim_words.intersection(output_words)) >= 2:
                return (
                    "SUPPORTED"
                    if self._number_key(claim_numbers[0]) == self._number_key(output_numbers[0])
                    else "CONTRADICTED"
                )

        claim_skeleton = self._skeleton(claim)
        output_skeleton = self._skeleton(output_text)
        if (
            len(claim_skeleton.split()) >= 3
            and claim_skeleton in output_skeleton
            and len(claim_numbers) == len(output_numbers)
        ):
            matches = all(
                self._number_key(left) == self._number_key(right)
                for left, right in zip(claim_numbers, output_numbers)
            )
            return "SUPPORTED" if matches else "CONTRADICTED"
        return None

    @staticmethod
    def _numeric_operation_matches(claim: str, tool_input: Any) -> bool:
        """Avoid treating an unrelated arithmetic output as a contradiction."""
        if not isinstance(tool_input, Mapping):
            return True
        expression = tool_input.get("expression", tool_input.get("code"))
        if not isinstance(expression, str):
            return True
        operations = (
            ("addition", r"\b(sum|addition|add|plus)\b", r"\+"),
            (
                "multiplication",
                r"\b(product|multiply|multiplied|multiplication|times|square)\b",
                r"\*",
            ),
            ("subtraction", r"\b(difference|subtract|subtraction|minus)\b", r"-"),
            ("division", r"\b(quotient|divide|divided|division)\b", r"/"),
            ("remainder", r"\b(remainder|modulo|mod)\b", r"%"),
        )
        for _, claim_pattern, expression_pattern in operations:
            if re.search(claim_pattern, claim, re.IGNORECASE):
                return re.search(expression_pattern, expression) is not None
        return True

    @classmethod
    def _structured_facts(cls, value: Any) -> dict[str, str]:
        text = cls._render(value)
        pairs = {
            key.casefold(): item.strip(" '\".,")
            for key, item in re.findall(
                r"""['"]?([A-Za-z_]+)['"]?\s*:\s*(['"][^'"]*['"]|[^,}\]]+)""",
                text,
            )
        }
        facts: dict[str, str] = {}
        ticket = pairs.get("ticket_id") or re.search(r"\bT-\d+\b", text, re.I)
        if ticket:
            facts["ticket_id"] = ticket if isinstance(ticket, str) else ticket.group(0)

        status_value = pairs.get("status")
        if status_value:
            facts["status"] = status_value.replace("_", " ").casefold()
        else:
            normalized = text.casefold().replace("_", " ")
            for status in _STATUSES:
                if re.search(rf"\b{re.escape(status)}\b", normalized):
                    facts["status"] = status.replace("_", " ")
                    break

        owner_value = pairs.get("owner")
        if owner_value:
            facts["owner"] = owner_value.casefold()
        else:
            owner_match = re.search(
                r"\b(?:owner(?:\s+is)?|owned\s+by)\s*[:=]?\s*"
                r"([A-Za-z][\w.-]*)",
                text,
                re.I,
            )
            if owner_match:
                facts["owner"] = owner_match.group(1).rstrip(".,").casefold()
        return facts

    @staticmethod
    def _capital_fact(text: str) -> tuple[str, str, bool] | None:
        negative = _CAPITAL_NEGATIVE.search(text)
        if negative:
            return (
                negative.group(1).casefold(),
                negative.group(2).casefold(),
                False,
            )
        match = _CAPITAL_FACTS[0].search(text)
        if match:
            return match.group(1).casefold(), match.group(2).casefold(), True
        match = _CAPITAL_FACTS[1].search(text)
        if match:
            return match.group(2).casefold(), match.group(1).casefold(), True
        return None

    @staticmethod
    def _numbers(text: str) -> list[str]:
        return [match.group(0).rstrip("%") for match in _NUMBER.finditer(text)]

    @staticmethod
    def _number_key(value: Any) -> float | None:
        try:
            return float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()

    @classmethod
    def _skeleton(cls, text: str) -> str:
        return cls._normalize(_NUMBER.sub(" ", text))

    @staticmethod
    def _render(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _empty(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}

    @staticmethod
    def _id_key(value: Any) -> str:
        return "" if value is None else str(value)