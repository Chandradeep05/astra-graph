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


class ImpactedFile(BaseModel):
    """
    Evidence-backed impacted file identified by historical co-changes in FalkorDB.
    """
    path: str
    co_change_count: int
    last_co_change: str
    shared_commit_count: int
    evidence_shas: List[str] = Field(default_factory=list)


class RelatedPerson(BaseModel):
    """
    Person with historical authorship of target or co-changing files.
    """
    person: str
    shared_commit_count: int
    evidence_shas: List[str] = Field(default_factory=list)


class ConnectedModule(BaseModel):
    """
    Module containing files historically connected via co-changes to target.
    """
    module: str
    file_count: int
    co_change_count: int


class ChangeImpactResponse(BaseModel):
    """
    Deterministic blast-radius and historical change impact response
    grounded in FalkorDB graph relationships and verified evidence.
    """
    refused: bool = False
    reason_code: Optional[RefusalCode] = None
    reason: Optional[str] = None
    target_file: str
    repo_name: str = "arth"
    impacted_files: List[ImpactedFile] = Field(default_factory=list)
    related_people: List[RelatedPerson] = Field(default_factory=list)
    connected_modules: List[ConnectedModule] = Field(default_factory=list)
    signals: List[str] = Field(default_factory=list)
    claims: List[Claim] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    verification: Optional[VerificationResult] = None
    sync_info: Optional[Dict[str, Any]] = None

