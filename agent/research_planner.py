"""Dependency-aware multi-step research planner with gap detection and stopping bounds."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from connectors.sources.selector import SourceSelectionDecision
from tools.external_search import NO_EXTERNAL_EVIDENCE_PREFIX, generate_search_query

_SPLIT_CONJUNCTION_RE = re.compile(r"\s+(?:and then|then|also|versus|vs\.?|compared with|compared to)\s+", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(?:compare|comparison|difference between|trade[- ]offs? between|vs\.?|versus)\b", re.IGNORECASE)
_TICKET_RE = re.compile(r"\bT-\d+\b", re.IGNORECASE)
_METRIC_RE = re.compile(r"\b(?:M-\d+|S-\d+)\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


@dataclass
class ResearchSubTask:
    id: str
    objective: str
    required_evidence: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | completed | failed | skipped
    is_gap_fill: bool = False
    evidence_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchPlan:
    question: str
    resolved_query: str
    domain: str
    decomposition_reason: str
    subtasks: list[ResearchSubTask] = field(default_factory=list)
    gaps_detected: list[str] = field(default_factory=list)
    stop_reason: str = ""
    max_subtasks: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "resolved_query": self.resolved_query,
            "domain": self.domain,
            "decomposition_reason": self.decomposition_reason,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "gaps_detected": list(self.gaps_detected),
            "stop_reason": self.stop_reason,
            "max_subtasks": self.max_subtasks,
        }


class MultiStepResearchPlanner:
    """Decomposes complex questions, tracks evidence gaps, and bounds multi-step tool execution."""

    def build_plan(
        self,
        question: str,
        *,
        resolved_query: str,
        available_tools: Sequence[str],
        source_selection: SourceSelectionDecision,
        math_expr: str | None = None,
        py_expr: str | None = None,
        conversation_history: Sequence[Mapping[str, Any]] = (),
        prompt_injection_flagged: bool = False,
        missing_information: Sequence[str] = (),
        max_subtasks: int = 4,
    ) -> ResearchPlan:
        avail = set(available_tools)
        target_q = resolved_query or question
        subtasks: list[ResearchSubTask] = []

        if prompt_injection_flagged or missing_information:
            return ResearchPlan(
                question=question,
                resolved_query=target_q,
                domain=source_selection.domain,
                decomposition_reason="Abstain before planning due to policy flag or missing parameters.",
                subtasks=[],
                stop_reason="blocked_or_missing_info",
                max_subtasks=max_subtasks,
            )

        url_match = _URL_RE.search(question)
        if url_match and "browser_inspect" in avail:
            subtasks.append(
                ResearchSubTask(
                    id="ST-1",
                    objective=f"Inspect official/target URL {url_match.group(0)}",
                    required_evidence="Extracted headings, metadata, and structured page content",
                    tool="browser_inspect",
                    arguments={"url": url_match.group(0), "action": "read_content"},
                )
            )
            return ResearchPlan(
                question=question,
                resolved_query=target_q,
                domain=source_selection.domain,
                decomposition_reason="Direct URL target mapped to permission-controlled browser inspection.",
                subtasks=subtasks,
                max_subtasks=max_subtasks,
            )

        tickets = _TICKET_RE.findall(target_q)
        metrics = _METRIC_RE.findall(target_q)

        # Enterprise / Telemetry multi-hop tasks
        for idx, tid in enumerate(dict.fromkeys([t.upper() for t in tickets]), start=1):
            if "mock_api" in avail and len(subtasks) < max_subtasks - 1:
                subtasks.append(
                    ResearchSubTask(
                        id=f"ST-{len(subtasks) + 1}",
                        objective=f"Retrieve enterprise ticket {tid} record",
                        required_evidence=f"ticket_id, status, owner, and SLA fields for {tid}",
                        tool="mock_api",
                        arguments={"ticket_id": tid},
                    )
                )

        for mid in dict.fromkeys([m.upper() for m in metrics]):
            if "search" in avail and len(subtasks) < max_subtasks:
                subtasks.append(
                    ResearchSubTask(
                        id=f"ST-{len(subtasks) + 1}",
                        objective=f"Look up local telemetry entry {mid}",
                        required_evidence=f"Measured telemetry value and description for {mid}",
                        tool="search",
                        arguments={"query": f"{mid} {question}".strip()},
                    )
                )

        if py_expr and "python_exec" in avail and len(subtasks) < max_subtasks:
            deps = [s.id for s in subtasks]
            subtasks.append(
                ResearchSubTask(
                    id=f"ST-{len(subtasks) + 1}",
                    objective=f"Evaluate Python expression {py_expr}",
                    required_evidence="Exact evaluated expression result",
                    tool="python_exec",
                    arguments={"code": py_expr},
                    depends_on=deps,
                )
            )
        elif math_expr and len(subtasks) < max_subtasks:
            deps = [s.id for s in subtasks]
            calc_tool = "calculator" if "calculator" in avail else ("python_exec" if "python_exec" in avail else "")
            if calc_tool:
                subtasks.append(
                    ResearchSubTask(
                        id=f"ST-{len(subtasks) + 1}",
                        objective=f"Compute exact arithmetic {math_expr}",
                        required_evidence="Exact numerical computation result",
                        tool=calc_tool,
                        arguments={"expression": math_expr} if calc_tool == "calculator" else {"code": math_expr},
                        depends_on=deps,
                    )
                )

        if subtasks:
            return ResearchPlan(
                question=question,
                resolved_query=target_q,
                domain=source_selection.domain,
                decomposition_reason=(
                    f"Decomposed into {len(subtasks)} dependency-ordered local lookup/computation sub-task(s)."
                ),
                subtasks=subtasks[:max_subtasks],
                max_subtasks=max_subtasks,
            )

        # Multi-source / Academic / Comparative / Official docs planning
        is_academic = source_selection.domain == "academic_research"
        is_comparative = bool(_COMPARE_RE.search(question)) and len(_SPLIT_CONJUNCTION_RE.split(question)) >= 2

        if is_academic and "research_sources" in avail:
            q_clean = generate_search_query(target_q, conversation_history) or target_q
            subtasks.append(
                ResearchSubTask(
                    id="ST-1",
                    objective="Query Tier 2 scholarly databases (arXiv, Crossref, OpenAlex, Semantic Scholar)",
                    required_evidence="Peer-reviewed abstracts, publication years, and verifiable DOIs/URLs",
                    tool="research_sources",
                    arguments={"query": q_clean},
                )
            )
            if "external_search" in avail and source_selection.cross_check_required and len(subtasks) < max_subtasks:
                subtasks.append(
                    ResearchSubTask(
                        id="ST-2",
                        objective="Cross-check scholarly findings against reference search",
                        required_evidence="Independent secondary corroboration or conflicting claims",
                        tool="external_search",
                        arguments={"query": q_clean},
                        depends_on=["ST-1"],
                    )
                )
        elif is_comparative and "external_search" in avail:
            parts = [p.strip(" ?.") for p in _SPLIT_CONJUNCTION_RE.split(target_q) if len(p.strip()) >= 4]
            q1 = generate_search_query(parts[0], conversation_history) or parts[0]
            q2 = generate_search_query(parts[-1], conversation_history) or parts[-1]
            subtasks.append(
                ResearchSubTask(
                    id="ST-1",
                    objective=f"Retrieve evidence for first aspect: {q1[:70]}",
                    required_evidence="Primary source facts for entity/concept A",
                    tool="external_search",
                    arguments={"query": q1},
                )
            )
            if q2.lower() != q1.lower() and len(subtasks) < max_subtasks:
                subtasks.append(
                    ResearchSubTask(
                        id="ST-2",
                        objective=f"Retrieve evidence for second aspect: {q2[:70]}",
                        required_evidence="Primary source facts for entity/concept B",
                        tool="external_search",
                        arguments={"query": q2},
                    )
                )
        elif "external_search" in avail and (
            source_selection.domain in {"official_documentation", "scientific_or_domain_concepts", "multi_source_fact"}
            or source_selection.selected_sources
        ):
            q_clean = generate_search_query(target_q, conversation_history) or target_q
            subtasks.append(
                ResearchSubTask(
                    id="ST-1",
                    objective=f"Retrieve authoritative external evidence for {source_selection.domain}",
                    required_evidence="Verified titles, URLs, dates, and supporting snippets",
                    tool="external_search",
                    arguments={"query": q_clean},
                )
            )
            if (
                source_selection.cross_check_required
                and "research_sources" in avail
                and source_selection.domain == "scientific_or_domain_concepts"
                and len(subtasks) < max_subtasks
            ):
                subtasks.append(
                    ResearchSubTask(
                        id="ST-2",
                        objective="Corroborate scientific claim against scholarly literature",
                        required_evidence="Academic literature excerpt or DOI",
                        tool="research_sources",
                        arguments={"query": q_clean},
                        depends_on=["ST-1"],
                    )
                )

        reason = (
            f"Constructed {len(subtasks)}-step {source_selection.domain} plan."
            if subtasks
            else "No matching tool sub-tasks needed; defer to scoped responder or local KB check."
        )
        return ResearchPlan(
            question=question,
            resolved_query=target_q,
            domain=source_selection.domain,
            decomposition_reason=reason,
            subtasks=subtasks[:max_subtasks],
            max_subtasks=max_subtasks,
        )

    @staticmethod
    def _obs_has_usable_evidence(obs: Mapping[str, Any]) -> bool:
        if obs.get("error"):
            return False
        out = obs.get("output")
        if out is None:
            return False
        if isinstance(out, str):
            low = out.strip().lower()
            return bool(low) and not low.startswith("no local evidence found") and not low.startswith(NO_EXTERNAL_EVIDENCE_PREFIX.lower())
        if isinstance(out, Mapping):
            docs = out.get("documents") or out.get("results")
            if isinstance(docs, list):
                return len(docs) > 0
            excerpt = str(out.get("content_excerpt") or out.get("summary") or out.get("text") or "").strip()
            if excerpt and not excerpt.lower().startswith(NO_EXTERNAL_EVIDENCE_PREFIX.lower()):
                return True
            return bool(out.get("ok")) or bool(out)
        return True

    def next_subtask_or_gap_fill(
        self,
        plan: ResearchPlan,
        observations: Sequence[Mapping[str, Any]],
        *,
        available_tools: Sequence[str],
        excluded_tools: set[str],
        source_selection: SourceSelectionDecision,
    ) -> ResearchSubTask | None:
        """Return next unexecuted pending sub-task, or inject a targeted gap-fill step when needed."""
        avail = set(available_tools)
        executed_count = len(observations)
        if executed_count >= plan.max_subtasks:
            plan.stop_reason = "max_subtasks_reached"
            return None

        # Mark status of executed tasks in order (deterministic alignment for sequential execution)
        for idx, obs in enumerate(observations):
            if idx < len(plan.subtasks):
                plan.subtasks[idx].status = "failed" if obs.get("error") else "completed"
                out_preview = str(obs.get("output") or obs.get("error") or "")[:180]
                plan.subtasks[idx].evidence_summary = out_preview

        # Look for pending subtasks whose tool is allowed
        for st in plan.subtasks:
            if st.status == "pending" and st.tool in avail and st.tool not in excluded_tools:
                return st

        # All initial subtasks ran: check for evidence gap requiring targeted secondary action
        last_obs = observations[-1] if observations else None
        if last_obs is not None and not self._obs_has_usable_evidence(last_obs):
            last_tool = str(last_obs.get("tool") or "")
            gap_msg = f"Insufficient evidence from {last_tool}"
            if gap_msg not in plan.gaps_detected:
                plan.gaps_detected.append(gap_msg)

            # Fallback 1: official documentation URL -> browser_inspect
            if (
                source_selection.browser_fallback_recommended
                and source_selection.primary_doc_urls
                and "browser_inspect" in avail
                and "browser_inspect" not in excluded_tools
                and not any(s.tool == "browser_inspect" for s in plan.subtasks)
            ):
                gap_task = ResearchSubTask(
                    id=f"ST-{len(plan.subtasks) + 1}",
                    objective=f"Inspect official doc URL directly after empty {last_tool}",
                    required_evidence="Primary documentation headings and content",
                    tool="browser_inspect",
                    arguments={"url": source_selection.primary_doc_urls[0], "action": "read_content"},
                    depends_on=[s.id for s in plan.subtasks[-1:]],
                    is_gap_fill=True,
                )
                plan.subtasks.append(gap_task)
                return gap_task

            # Fallback 2: research_sources after empty external_search (for research/science queries)
            if (
                last_tool == "external_search"
                and "research_sources" in avail
                and "research_sources" not in excluded_tools
                and not any(s.tool == "research_sources" for s in plan.subtasks)
                and source_selection.domain in {"academic_research", "scientific_or_domain_concepts", "multi_source_fact"}
            ):
                gap_task = ResearchSubTask(
                    id=f"ST-{len(plan.subtasks) + 1}",
                    objective="Search Tier 2 academic/reference sources after empty web search",
                    required_evidence="Scholarly abstracts or reference entries",
                    tool="research_sources",
                    arguments={"query": plan.resolved_query},
                    depends_on=[s.id for s in plan.subtasks[-1:]],
                    is_gap_fill=True,
                )
                plan.subtasks.append(gap_task)
                return gap_task

        plan.stop_reason = "sufficient_evidence" if observations and all(self._obs_has_usable_evidence(o) for o in observations) else "no_further_applicable_tasks"
        return None
