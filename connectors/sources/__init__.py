"""Multi-source retrieval, trust tiers, intelligent selection, and claim-level evidence verification."""

from connectors.sources.registry import SourceRegistry
from connectors.sources.selector import IntelligentSourceSelector, SourceSelectionDecision
from connectors.sources.verifier import ClaimEvidenceVerifier, ClaimVerificationRecord

__all__ = [
    "ClaimEvidenceVerifier",
    "ClaimVerificationRecord",
    "IntelligentSourceSelector",
    "SourceRegistry",
    "SourceSelectionDecision",
]
