"""Deterministic extraction of candidate factual claims from agent answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FactualClaim:
    """A claim candidate with a stable, answer-local identifier."""

    claim_id: str
    claim: str

    def to_dict(self) -> dict[str, str]:
        """Return the claim in its report representation."""
        return {"claim_id": self.claim_id, "claim": self.claim}


class ClaimExtractor:
    """Split an answer into sentence- and clause-level claim candidates."""

    _splitter = re.compile(
        r"(?<=[.!?])\s+|;\s*|,\s+(?:and|but)\s+(?=[A-Z0-9])"
    )
    _list_marker = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")

    def extract(self, answer: Any) -> list[FactualClaim]:
        """Extract non-empty candidate statements while preserving their text."""
        if not isinstance(answer, str) or not answer.strip():
            return []

        candidates: list[str] = []
        for line in answer.strip().splitlines():
            clean_line = self._list_marker.sub("", line, count=1).strip()
            if not clean_line:
                continue
            for segment in self._splitter.split(clean_line):
                claim = segment.strip()
                if re.search(r"[A-Za-z0-9]", claim):
                    candidates.append(claim)

        return [
            FactualClaim(claim_id=f"claim_{index}", claim=text)
            for index, text in enumerate(candidates, start=1)
        ]