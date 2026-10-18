from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.evidence.models import Claim, Evidence, RefusalCode, VerificationResult


class SubjectType(str, Enum):
    """Supported targets for context package generation."""
    REPOSITORY = "repository"
    FILE = "file"
    MODULE = "module"


class FreshnessContext(BaseModel):
    """Graph freshness metadata verifying snapshot lineage."""
    sync_id: str = ""
    head_sha: str = ""
    synced_at: str = ""
    commit_count: int = 0
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history_window: str = "bounded"
    is_stale: bool = False


class ModuleContext(BaseModel):
    """Module structure derived strictly from FalkorDB IN_MODULE edges."""
    name: str
    path: str
    file_count: int = 0
    top_contributors: List[str] = Field(default_factory=list)
    sample_files: List[str] = Field(default_factory=list)


class ArchitectureContext(BaseModel):
    """
    Bounded architectural structure derived from FalkorDB graph relationships.
    Explicitly labeled as graph-derived; not a speculative runtime inference.
    """
    type: str = "graph_derived_structure"
    modules: List[ModuleContext] = Field(default_factory=list)
    cross_module_connections: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    limitations: str = (
        "Derived strictly from Git directory structure and historical co-changes; "
        "runtime microservice topologies and dynamic execution paths are not modeled."
    )


class FileContext(BaseModel):
    """Important file in the repository or subject context."""
    path: str
    module: str = ""
    commit_count: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    top_authors: List[str] = Field(default_factory=list)
    co_change_count: int = 0
    last_commit_at: str = ""


class DependencyContext(BaseModel):
    """Relational dependency or co-change link between entities."""
    source: str
    target: str
    relationship_type: str = "CO_CHANGES"
    strength: int = 1
    evidence_ref: str = ""


class ContributorContext(BaseModel):
    """Contributor ownership and touch statistics grounded in graph commits."""
    name: str
    email: str
    commit_count: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    files_touched: List[str] = Field(default_factory=list)
    last_active: str = ""
    is_constrained: bool = False
    constraint_reason: Optional[str] = None


class HistoricalContext(BaseModel):
    """Historical evolution context derived from Git commits."""
    total_commits: int = 0
    first_commit_at: str = ""
    last_commit_at: str = ""
    top_churn_files: List[Dict[str, Any]] = Field(default_factory=list)
    frequent_co_changes: List[Dict[str, Any]] = Field(default_factory=list)


class ImpactContext(BaseModel):
    """Blast radius and co-change context for subject files."""
    target: str
    impacted_files_count: int = 0
    co_changing_files: List[Dict[str, Any]] = Field(default_factory=list)
    related_modules: List[str] = Field(default_factory=list)


class ReviewerContext(BaseModel):
    """Evidence-backed reviewer recommendations respecting memory constraints."""
    target: str
    recommended_reviewers: List[Dict[str, Any]] = Field(default_factory=list)
    excluded_reviewers: List[Dict[str, Any]] = Field(default_factory=list)


class ConstraintContext(BaseModel):
    """Active persistent graph memory constraint affecting the repository or subject."""
    id: str
    type: str
    subject: str
    scope: str
    active: bool = True
    source_action_id: Optional[str] = None


class ContextPackage(BaseModel):
    """
    Structured, machine-readable, evidence-backed Context Package.
    Enables autonomous AI agents to understand repository structure, history,
    ownership, impact, and constraints quickly without dumping raw source code.
    """
    repository: str
    subject: str
    subject_type: SubjectType
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    freshness: FreshnessContext
    summary: str = ""
    architecture: ArchitectureContext
    modules: List[ModuleContext] = Field(default_factory=list)
    important_files: List[FileContext] = Field(default_factory=list)
    dependencies: List[DependencyContext] = Field(default_factory=list)
    contributors: List[ContributorContext] = Field(default_factory=list)
    historical_context: HistoricalContext
    impact_context: Optional[ImpactContext] = None
    reviewer_context: Optional[ReviewerContext] = None
    constraints: List[ConstraintContext] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    claims: List[Claim] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    verification: Optional[VerificationResult] = None
    refusal: bool = False
    reason_code: Optional[RefusalCode] = None
    reason: Optional[str] = None
    truncated: bool = False
    truncation_reason: Optional[str] = None
    depth: int = 3


class ContextPackageRequest(BaseModel):
    """Request payload to generate a bounded context package."""
    repository: str = Field(default="arth", description="Target repository name")
    subject: Optional[str] = Field(default=None, description="Subject target: repository name, file path, or module")
    subject_type: Optional[str] = Field(default=None, description="Subject type: 'repository', 'file', or 'module'")
    depth: int = Field(default=3, ge=1, le=5, description="Maximum graph traversal depth")
    max_files: int = Field(default=20, ge=1, le=100, description="Maximum files to include")
    max_contributors: int = Field(default=10, ge=1, le=50, description="Maximum contributors to include")
    max_evidence: int = Field(default=30, ge=1, le=100, description="Maximum evidence records to include")
