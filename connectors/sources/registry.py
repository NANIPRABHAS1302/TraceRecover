"""Multi-tier Source Registry (Tier 1-4), scholarly adapters, deduplication, and contradiction checks."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from connectors.schemas import TRUST_TIER_METADATA, SourceDocument

_TIER_1_DOMAINS = {
    "docs.python.org",
    "developer.mozilla.org",
    "modelcontextprotocol.io",
    "fastapi.tiangolo.com",
    "playwright.dev",
    "nasa.gov",
    "who.int",
    "cdc.gov",
    "sec.gov",
    "nist.gov",
    "ietf.org",
    "w3.org",
    "rfc-editor.org",
}

_TIER_2_DOMAINS = {
    "arxiv.org",
    "export.arxiv.org",
    "semanticscholar.org",
    "api.semanticscholar.org",
    "crossref.org",
    "api.crossref.org",
    "openalex.org",
    "api.openalex.org",
    "pubmed.ncbi.nlm.nih.gov",
    "nature.com",
    "science.org",
    "ieee.org",
    "acm.org",
}

_TIER_3_DOMAINS = {
    "en.wikipedia.org",
    "wikipedia.org",
    "github.com",
    "pypi.org",
    "npmjs.com",
    "learn.microsoft.com",
    "cloud.google.com",
}


def classify_url_tier(url: str, fallback: str = "tier4") -> tuple[str, str]:
    """Assign a provenance tier (tier1..tier4) from domain; tier never replaces factual verification."""
    host = (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    if not host:
        return fallback, ""
    if host in _TIER_1_DOMAINS or host.endswith(".gov") or host.endswith(".edu"):
        return "tier1", host
    if host in _TIER_2_DOMAINS:
        return "tier2", host
    if host in _TIER_3_DOMAINS or host.startswith("docs.") or host.endswith(".readthedocs.io"):
        return "tier3", host
    return fallback, host


def extract_year_or_date(text: str) -> str:
    """Extract an ISO date or 4-digit publication year if present."""
    if not text:
        return ""
    m_iso = re.search(r"\b(20\d{2}-\d{2}-\d{2}|19\d{2}-\d{2}-\d{2})\b", text)
    if m_iso:
        return m_iso.group(1)
    m_year = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    return m_year.group(1) if m_year else ""


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _extract_numbers(text: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text or ""))


class SourceRegistry:
    """Queries research & reference sources, assigns Trust Tiers 1-4, deduplicates, ranks, and cross-checks."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    @staticmethod
    def tier_catalog() -> list[dict[str, Any]]:
        return list(TRUST_TIER_METADATA.values())

    def search_arxiv(self, query: str, max_results: int = 5) -> list[SourceDocument]:
        url = f"https://export.arxiv.org/api/query?search_query=all:{quote_plus(query)}&start=0&max_results={max_results}"
        docs: list[SourceDocument] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
            link = (entry.findtext("atom:id", "", ns) or "").strip()
            summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
            published = (entry.findtext("atom:published", "", ns) or "").strip()[:10]
            if not link.startswith("http"):
                continue
            tier, host = classify_url_tier(link, fallback="tier2")
            docs.append(
                SourceDocument(
                    title=title or "arXiv Preprint",
                    url=link,
                    snippet=summary[:500],
                    provider="arxiv",
                    trust_tier=tier,
                    domain=host,
                    published_at=published,
                    relevance_score=0.75,
                    citation=f"{title} (arXiv, {published[:4] if published else 'n.d.'}). {link}",
                )
            )
        return docs

    def search_crossref(self, query: str, max_results: int = 5) -> list[SourceDocument]:
        url = f"https://api.crossref.org/works?query={quote_plus(query)}&rows={max_results}"
        docs: list[SourceDocument] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "TraceRecover/1.0 (mailto:research@tracerecover.local)"})
            resp.raise_for_status()
            payload = resp.json()
        for item in (payload.get("message") or {}).get("items") or []:
            titles = item.get("title") or []
            title = str(titles[0]) if titles else ""
            doi_url = str(item.get("URL") or "").strip()
            if not doi_url.startswith("http"):
                continue
            date_parts = (((item.get("published") or {}).get("date-parts") or [[]])[0])
            pub = "-".join(str(x) for x in date_parts[:3]) if date_parts else ""
            journal = ", ".join(item.get("container-title") or [])
            snippet = f"{journal}. DOI: {item.get('DOI', '')}".strip()
            docs.append(
                SourceDocument(
                    title=title or str(item.get("DOI") or "Crossref Work"),
                    url=doi_url,
                    snippet=snippet[:500],
                    provider="crossref",
                    trust_tier="tier2",
                    domain=urlparse(doi_url).hostname or "crossref.org",
                    published_at=pub,
                    relevance_score=0.72,
                    citation=f"{title} ({pub[:4] if pub else 'n.d.'}). {journal}. {doi_url}",
                )
            )
        return docs

    def search_openalex(self, query: str, max_results: int = 5) -> list[SourceDocument]:
        url = f"https://api.openalex.org/works?search={quote_plus(query)}&per-page={max_results}"
        docs: list[SourceDocument] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "TraceRecoverResearchAgent/1.0 (https://tracerecover.local; contact=research@tracerecover.local)"})
            resp.raise_for_status()
            payload = resp.json()
        for item in payload.get("results") or []:
            title = str(item.get("title") or "").strip()
            link = str(item.get("doi") or item.get("id") or "").strip()
            if not link.startswith("http"):
                continue
            year = str(item.get("publication_year") or "")
            cited_by = item.get("cited_by_count", 0)
            venue = ((item.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            snippet = f"Published {year or 'n.d.'} in {venue or 'OpenAlex'}. Cited by {cited_by} works."
            docs.append(
                SourceDocument(
                    title=title or link,
                    url=link,
                    snippet=snippet[:500],
                    provider="openalex",
                    trust_tier="tier2",
                    domain=urlparse(link).hostname or "openalex.org",
                    published_at=year,
                    relevance_score=0.74,
                    citation=f"{title} ({year or 'n.d.'}). {link}",
                )
            )
        return docs

    def search_semantic_scholar(self, query: str, max_results: int = 5, api_key: str = "") -> list[SourceDocument]:
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={quote_plus(query)}&limit={max_results}&fields=title,url,year,abstract,citationCount"
        )
        headers = {"x-api-key": api_key} if api_key else {}
        docs: list[SourceDocument] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        for item in payload.get("data") or []:
            title = str(item.get("title") or "").strip()
            link = str(item.get("url") or "").strip()
            if not link.startswith("http"):
                continue
            year = str(item.get("year") or "")
            abstract = str(item.get("abstract") or "").strip()
            docs.append(
                SourceDocument(
                    title=title or link,
                    url=link,
                    snippet=abstract[:500] or f"Semantic Scholar paper ({year or 'n.d.'})",
                    provider="semantic_scholar",
                    trust_tier="tier2",
                    domain=urlparse(link).hostname or "semanticscholar.org",
                    published_at=year,
                    relevance_score=0.76,
                    citation=f"{title} ({year or 'n.d.'}). {link}",
                )
            )
        return docs

    def search_wikipedia(self, query: str, max_results: int = 4) -> list[SourceDocument]:
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={quote_plus(query)}&srlimit={max_results}&format=json&utf8=1"
        )
        docs: list[SourceDocument] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "TraceRecoverResearchAgent/1.0 (https://tracerecover.local; contact=research@tracerecover.local)"})
            resp.raise_for_status()
            payload = resp.json()
        for item in ((payload.get("query") or {}).get("search") or []):
            title = str(item.get("title") or "").strip()
            snippet = re.sub(r"<[^>]+>", "", str(item.get("snippet") or "")).strip()
            page_url = f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
            docs.append(
                SourceDocument(
                    title=title,
                    url=page_url,
                    snippet=snippet[:500],
                    provider="wikipedia",
                    trust_tier="tier3",
                    domain="en.wikipedia.org",
                    published_at=extract_year_or_date(str(item.get("timestamp") or "")),
                    relevance_score=0.68,
                    citation=f"{title} - Wikipedia. {page_url}",
                )
            )
        return docs

    def aggregate_and_rank(
        self,
        query: str,
        documents: list[SourceDocument],
    ) -> dict[str, Any]:
        """Deduplicate URLs/titles, compute weighted rank with trust tier + lexical overlap, and flag corroboration/conflicts."""
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        deduped: list[SourceDocument] = []
        q_terms = {t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower())}

        for doc in documents:
            clean_url = (doc.url or "").strip().rstrip("/")
            norm_title = _normalize_title(doc.title)
            if not clean_url.startswith("http") or clean_url in seen_urls:
                continue
            if norm_title and norm_title in seen_titles:
                continue
            seen_urls.add(clean_url)
            if norm_title:
                seen_titles.add(norm_title)

            tier_meta = TRUST_TIER_METADATA.get(doc.trust_tier, TRUST_TIER_METADATA["tier4"])
            tier_weight = float(tier_meta["weight"])
            text_blob = f"{doc.title} {doc.snippet}".lower()
            overlap = len([t for t in q_terms if t in text_blob]) / max(len(q_terms), 1)
            recency_bonus = 0.1 if (doc.published_at and doc.published_at[:4] >= "2023") else 0.0
            score = round(tier_weight * 0.45 + max(doc.relevance_score, overlap) * 0.45 + recency_bonus, 4)
            doc.relevance_score = score
            if not doc.published_at:
                doc.published_at = extract_year_or_date(doc.snippet)
            if not doc.citation:
                doc.citation = f"{doc.title or doc.domain} ({doc.published_at or 'n.d.'}). {doc.url}"
            deduped.append(doc)

        deduped.sort(key=lambda d: d.relevance_score, reverse=True)

        # Corroboration & basic contradiction signal check
        distinct_domains = {d.domain for d in deduped if d.domain}
        corroborated = len(distinct_domains) >= 2
        number_sets = [_extract_numbers(d.snippet) for d in deduped[:4] if _extract_numbers(d.snippet)]
        neg_hits = [bool(re.search(r"\b(not|no longer|false|refuted|disproven|contrary)\b", d.snippet.lower())) for d in deduped[:4]]
        conflict = False
        if len(set(neg_hits)) > 1:
            conflict = True
        elif len(number_sets) >= 2 and not (number_sets[0] & number_sets[1]):
            conflict = True

        for d in deduped:
            d.corroborated = corroborated
            d.contradiction_flag = conflict

        return {
            "query": query,
            "count": len(deduped),
            "corroborated": corroborated,
            "distinct_domains": sorted(distinct_domains),
            "contradiction_detected": conflict,
            "tier_disclaimer": "Source trust tiers (Tier 1-4) reflect provenance priority only and never guarantee factual truth without cross-source corroboration.",
            "documents": [d.to_dict() for d in deduped],
        }
