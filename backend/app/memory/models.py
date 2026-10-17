from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ConstraintType(str, Enum):
    """
    Supported persistent constraint types stored in FalkorDB graph memory.
    M7 supports strictly 'reviewer_unavailable'.
    """
    REVIEWER_UNAVAILABLE = "reviewer_unavailable"


class ConstraintCreate(BaseModel):
    """Payload to establish a persistent graph constraint."""
    type: ConstraintType = ConstraintType.REVIEWER_UNAVAILABLE
    subject: str = Field(description="Entity to which the constraint applies (e.g., reviewer name or email)")
    predicate: str = Field(default="unavailable_for_review", description="Factual condition asserted")
    value: Any = Field(default=True, description="Value of the condition")
    scope: str = Field(default="repo:arth", description="Scope of the constraint (e.g. repo:arth)")
    source_action_id: Optional[str] = Field(default=None, description="Action ID from which this constraint originated")
    active: bool = Field(default=True, description="Whether the constraint is currently active")


class Constraint(BaseModel):
    """
    Persistent graph memory constraint entity.
    Stored directly in FalkorDB as (:Constraint) node.
    """
    id: str = Field(description="Unique constraint ID, e.g. constraint_9f8e7d6c")
    type: ConstraintType = Field(description="Semantic type of the constraint")
    subject: str = Field(description="Subject entity (person, file, etc.)")
    predicate: str = Field(description="Predicate relation")
    value: Any = Field(description="Value")
    scope: str = Field(description="Scope constraint (e.g., repo:arth)")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_action_id: Optional[str] = None
    active: bool = True


class ExcludedCandidate(BaseModel):
    """Reviewer candidate excluded due to active graph constraints."""
    person: str
    reason: str = "reviewer_unavailable"
    evidence_ids: List[str] = Field(default_factory=list)
    constraint_id: Optional[str] = None


class InvalidConstraintError(Exception):
    """Raised when an invalid constraint is created or submitted."""
    pass


class ConstraintNotFoundError(Exception):
    """Raised when a requested constraint does not exist."""
    pass
