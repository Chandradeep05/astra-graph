from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.evidence.models import Claim, Evidence, RefusalCode, VerificationResult
from app.memory.models import ExcludedCandidate


class ReviewerCandidate(BaseModel):
    """
    Evidence-backed candidate reviewer derived purely from graph relationships.
    No invented confidence scores or unsupported claims.
    """
    person_name: str
    person_email: str
    commit_count: int
    lines_added: int
    lines_deleted: int
    last_commit_at: str
    last_commit_sha: str
    target: str
    target_type: str  # "file" or "module"
    evidence_commits: List[str] = Field(default_factory=list)


class ReviewersResponse(BaseModel):
    """
    Full response from reviewers_for tool including freshness/sync stamp.
    """
    target: str
    target_type: str
    repo_name: str
    candidates: List[ReviewerCandidate] = Field(default_factory=list)
    total_candidates: int = 0
    sync_info: Optional[Dict[str, Any]] = None


class VerifiedReviewersResponse(BaseModel):
    """
    Complete evidence-backed response from reviewers intelligence with verification,
    refusal support, and persistent graph memory exclusion.
    """
    refused: bool = False
    reason_code: Optional[RefusalCode] = None
    reason: Optional[str] = None
    target: str
    target_type: str
    repo_name: str
    candidates: List[ReviewerCandidate] = Field(default_factory=list)
    excluded_candidates: List[ExcludedCandidate] = Field(default_factory=list)
    claims: List[Claim] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    memory_evidence: List[Evidence] = Field(default_factory=list)
    verification: Optional[VerificationResult] = None
    sync_info: Optional[Dict[str, Any]] = None
