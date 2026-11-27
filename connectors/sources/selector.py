"""Adaptive domain-aware source selection distinguishing primary, secondary, and rejected sources."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_TICKET_RE = re.compile(r"\bT-\d+\b", re.IGNORECASE)
_METRIC_RE = re.compile(r"\b(?:M-\d+|S-\d+)\b", re.IGNORECASE)
_ACADEMIC_RE = re.compile(
    r"\b(?:arxiv|preprint|doi|crossref|openalex|semantic scholar|paper|papers|journal|citation|author|benchmark study|systematic review|hallucination detection|uncertainty calibration|conformal prediction|deberta|react prompting)\b",
    re.IGNORECASE,
)
_OFFICIAL_DOC_TARGETS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:python|asyncio|dataclass|urllib|pathlib)\b", re.I), "Python Official Docs", "https://docs.python.org/3/"),
    (re.compile(r"\b(?:fastapi|pydantic|starlette)\b", re.I), "FastAPI Official Docs", "https://fastapi.tiangolo.com/"),
    (re.compile(r"\b(?:playwright)\b", re.I), "Playwright Official Docs", "https://playwright.dev/python/docs/intro"),
    (re.compile(r"\b(?:mcp|model context protocol)\b", re.I), "Model Context Protocol Spec", "https://modelcontextprotocol.io/docs/getting-started/intro"),
    (re.compile(r"\b(?:mdn|http/2|content security policy|csp|cors|fetch api|html5)\b", re.I), "MDN / W3C Web Docs", "https://developer.mozilla.org/en-US/docs/Web/HTTP"),
    (re.compile(r"\b(?:opentelemetry|semantic conventions)\b", re.I), "OpenTelemetry Docs", "https://opentelemetry.io/docs/"),
    (re.compile(r"\b(?:docker|container|kubernetes|postgresql|redis)\b", re.I), "Cloud & Infra Docs", "https://docs.docker.com/"),
]
_DOC_TECH_RE = re.compile(
    r"\b(?:documentation|official docs|spec|specification|api reference|release notes|rfc|sdk|syntax|flag|endpoint|http/2|tls|oauth|owasp)\b",
    re.IGNORECASE,
)
_SCIENCE_CONCEPT_RE = re.compile(
    r"\b(?:physics|entropy|thermodynamic|quantum|crispr|dna|gene|biology|protein|climate|solar|photovoltaic|catalyst|chemistry|economics|inflation|monetary)\b",
    re.IGNORECASE,
)
_MULTI_SOURCE_RE = re.compile(
    r"\b(?:compare|versus|vs\.?|who|when|where|population|capital|history|inventor|founded|trade[- ]offs?|difference between)\b",
    re.IGNORECASE,
)


@dataclass
class SourceSelectionDecision:
    domain: str
    information_needed: str
    selected_sources: list[str]
    primary_sources: list[str] = field(default_factory=list)
    secondary_context_sources: list[str] = field(default_factory=list)
    rejected_sources: list[dict[str, str]] = field(default_factory=list)
    primary_doc_urls: list[str] = field(default_factory=list)
    browser_fallback_recommended: bool = False
    cross_check_required: bool = False
    rationale: str = ""
    epistemic_note: str = "Source trust tier prioritizes provenance quality only; claims remain unverified until content agreement passes."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IntelligentSourceSelector:
    """Selects primary & secondary contextual sources by query domain and avoids wasteful fan-out."""

    def select(
        self,
        question: str,
        *,
        available_tools: Sequence[str] = (),
        analysis_intent: str = "",
        needs_external: bool = False,
        missing_info: Sequence[str] = (),
        prompt_injection_flagged: bool = False,
    ) -> SourceSelectionDecision:
        q = (question or "").strip()
        avail = set(available_tools)
        rejected: list[dict[str, str]] = []

        if prompt_injection_flagged or missing_info or analysis_intent in ("unsupported_or_unsafe", "ambiguous_or_missing_info"):
            return SourceSelectionDecision(
                domain="ambiguous_or_blocked",
                information_needed="Clarification or policy safety validation before querying any source",
                selected_sources=[],
                primary_sources=[],
                secondary_context_sources=[],
                rejected_sources=[{"source": "all_external", "reason": "Query blocked by safety guard or missing required parameters."}],
                rationale="Abstaining before tool execution because parameters are missing or input triggered safety filters.",
            )

        url_match = _URL_RE.search(q)
        if url_match:
            selected = ["browser_inspect"] if "browser_inspect" in avail else (["external_search"] if "external_search" in avail else [])
            for src in ("research_sources", "search", "calculator"):
                if src in avail:
                    rejected.append({"source": src, "reason": "Direct URL inspection requested; non-browser sources skipped."})
            return SourceSelectionDecision(
                domain="browser_url",
                information_needed=f"Live page content from {url_match.group(0)}",
                selected_sources=selected,
                primary_sources=list(selected),
                secondary_context_sources=[],
                rejected_sources=rejected,
                primary_doc_urls=[url_match.group(0)],
                browser_fallback_recommended=False,
                cross_check_required=False,
                rationale="Selected Playwright browser_inspect directly as primary inspector for user-supplied HTTP(S) URL.",
            )

        if _TICKET_RE.search(q) or _METRIC_RE.search(q) or analysis_intent in (
            "ticket_api_lookup",
            "local_kb_lookup",
            "arithmetic_computation",
            "safe_python_evaluation",
            "multi_step_synthesis",
        ):
            local_picks = [t for t in ("mock_api", "search", "calculator", "python_exec") if t in avail]
            for ext in ("research_sources", "browser_inspect", "external_search"):
                if ext in avail:
                    rejected.append({"source": ext, "reason": "Deterministic local enterprise/telemetry/math query requires no external fan-out."})
            return SourceSelectionDecision(
                domain="local_enterprise_or_computation",
                information_needed="Internal ticket, local KB telemetry, or deterministic arithmetic/Python",
                selected_sources=local_picks,
                primary_sources=list(local_picks),
                secondary_context_sources=[],
                rejected_sources=rejected,
                rationale="Selected deterministic internal tools (mock_api/search/calculator/python_exec) as authoritative local primary sources.",
            )

        if _ACADEMIC_RE.search(q) or analysis_intent == "scholarly_research":
            primary: list[str] = []
            if "research_sources" in avail:
                primary.append("research_sources")
            elif "external_search" in avail:
                primary.append("external_search")
            selected = list(dict.fromkeys(primary + ["research-arxiv", "research-openalex", "research-crossref", "research-semantic-scholar"]))
            secondary = ["search-wikipedia"] if _MULTI_SOURCE_RE.search(q) else []
            for s in ("calculator", "python_exec", "mock_api"):
                if s in avail:
                    rejected.append({"source": s, "reason": "Academic literature query does not need local ticket/math tools."})
            rejected.append({"source": "search-duckduckgo", "reason": "Prefer Tier 2 scholarly indexes over noisy Tier 4 open web snippets."})
            return SourceSelectionDecision(
                domain="academic_research",
                information_needed="Peer-reviewed papers, preprints, DOIs, and abstracts (Tier 2 primary academic provenance)",
                selected_sources=selected,
                primary_sources=primary or ["research-arxiv", "research-openalex"],
                secondary_context_sources=secondary,
                rejected_sources=rejected,
                browser_fallback_recommended=False,
                cross_check_required=True,
                rationale="Selected Tier 2 academic databases as primary sources for empirical/scholarly verification, avoiding Tier 4 open-web noise.",
            )

        matched_doc_urls: list[str] = []
        matched_doc_names: list[str] = []
        for pat, label, doc_url in _OFFICIAL_DOC_TARGETS:
            if pat.search(q):
                matched_doc_names.append(label)
                matched_doc_urls.append(doc_url)

        if matched_doc_urls or _DOC_TECH_RE.search(q) or analysis_intent == "programming_help":
            primary: list[str] = []
            secondary: list[str] = []
            if "mcp-google-dev-knowledge" in avail:
                primary.append("mcp-google-dev-knowledge")
            if "browser_inspect" in avail and matched_doc_urls:
                primary.append("browser_inspect")
            if "external_search" in avail:
                if not primary:
                    primary.append("external_search")
                else:
                    secondary.append("external_search")
            selected = list(dict.fromkeys(primary + secondary))
            rejected.append({"source": "research-arxiv", "reason": "Technical specification/API documentation prefers Tier 1 official docs over preprints."})
            return SourceSelectionDecision(
                domain="official_documentation",
                information_needed=f"Authoritative documentation ({', '.join(matched_doc_names) or 'technical specification'})",
                selected_sources=selected,
                primary_sources=primary,
                secondary_context_sources=secondary,
                rejected_sources=rejected,
                primary_doc_urls=matched_doc_urls,
                browser_fallback_recommended=bool("browser_inspect" in avail and matched_doc_urls),
                cross_check_required=bool(len(selected) > 1 or _MULTI_SOURCE_RE.search(q)),
                rationale="Prioritized Tier 1 official documentation and browser inspection fallback if search snippets lack authoritative detail.",
            )

        if _SCIENCE_CONCEPT_RE.search(q):
            primary = [t for t in ("research_sources", "external_search") if t in avail]
            secondary = ["search-wikipedia"]
            if "mock_api" in avail:
                rejected.append({"source": "mock_api", "reason": "Enterprise ticket lookup irrelevant to scientific/economics domain."})
            return SourceSelectionDecision(
                domain="scientific_or_domain_concepts",
                information_needed="Scientific mechanism, quantitative facts, and scholarly/encyclopedic corroboration",
                selected_sources=list(dict.fromkeys(primary + secondary)),
                primary_sources=primary or secondary,
                secondary_context_sources=secondary,
                rejected_sources=rejected,
                browser_fallback_recommended=False,
                cross_check_required=True,
                rationale="Selected scholarly sources + secondary encyclopedic background for scientific and domain-factual corroboration.",
            )

        if needs_external or _MULTI_SOURCE_RE.search(q):
            selected = [t for t in ("external_search", "research_sources", "browser_inspect") if t in avail]
            return SourceSelectionDecision(
                domain="multi_source_fact",
                information_needed="Cross-verified reference facts across independent domains",
                selected_sources=selected,
                primary_sources=selected[:1],
                secondary_context_sources=selected[1:],
                rejected_sources=rejected,
                browser_fallback_recommended="browser_inspect" in avail,
                cross_check_required=True,
                rationale="Selected multi-source verification across primary reference and secondary corroborating adapters.",
            )

        return SourceSelectionDecision(
            domain="general_reasoning",
            information_needed="Conceptual synthesis or scoped response",
            selected_sources=[t for t in ("external_search",) if t in avail] if needs_external else [],
            primary_sources=[],
            secondary_context_sources=[t for t in ("external_search",) if t in avail] if needs_external else [],
            rejected_sources=rejected,
            rationale="General query without external research triggers.",
        )
