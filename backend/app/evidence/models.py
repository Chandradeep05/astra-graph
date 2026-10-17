from typing import Any, Dict, List, Optional
from enum import Enum
from pydantic import BaseModel, Field


class RefusalCode(str, Enum):
    """
    Standard refusal reason codes required by the Anvaya PRD.
    Refusal occurs BEFORE any downstream processing or model invocation.
    """
    NO_PATH = "NO_PATH"
    LOW_EVIDENCE = "LOW_EVIDENCE"
    STALE_DATA = "STALE_DATA"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Evidence(BaseModel):
    """
    A single piece of verifiable evidence directly retrieved from the FalkorDB graph.
    """
    id: str = Field(description="Unique evidence identifier, e.g. ev_commit_79595add")
    kind: str = Field(description="Type of entity or relationship, e.g. commit, touch, person, sync")
    ref: str = Field(description="Canonical reference, e.g. commit SHA, email, file path")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Raw graph properties")


class Claim(BaseModel):
    """
    A discrete, structured factual assertion backed by evidence IDs.
    Rule: No evidence ID, no claim.
    """
    claim_id: str = Field(description="Unique claim ID, e.g. claim_1")
    claim_type: str = Field(description="Semantic claim category, e.g. commit_count, lines_added, recency, recommendation")
    subject: str = Field(description="Entity being discussed, e.g. person name or email")
    predicate: str = Field(description="Factual relation or action asserted")
    value: Any = Field(description="Factual quantity, string, or boolean value")
    evidence_ids: List[str] = Field(default_factory=list, description="IDs of Evidence objects supporting this claim")


class ClaimRejection(BaseModel):
    """Details of a claim rejected by the verifier."""
    claim: Claim
    reason: str


class VerificationResult(BaseModel):
    """The result of verifying a list of claims against a set of evidence."""
    verified: bool
    verified_claims: List[Claim] = Field(default_factory=list)
    rejected_claims: List[ClaimRejection] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)


class RefusalResponse(BaseModel):
    """Returned when the graph cannot support an answer."""
    refused: bool = True
    reason_code: RefusalCode
    reason: str
    target: str
    target_type: str
    sync_info: Optional[Dict[str, Any]] = None
