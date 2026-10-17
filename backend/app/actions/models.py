from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ActionStatus(str, Enum):
    """
    Deterministic state machine for actions.
    Enforces human approval before execution.
    """
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ActionType(str, Enum):
    """
    Supported application-owned action types.
    M6 supports strictly 'request_review'.
    """
    REQUEST_REVIEW = "request_review"


class ActionCreate(BaseModel):
    """Payload to propose a new action."""
    type: ActionType = ActionType.REQUEST_REVIEW
    repo: str = Field(default="arth", description="Target repository name")
    target: str = Field(description="Target file or module path")
    reviewer: str = Field(description="Reviewer username or name")
    reviewer_email: Optional[str] = Field(default=None, description="Reviewer email")
    reason: str = Field(description="Deterministic reason grounded in graph evidence")
    evidence_ids: List[str] = Field(min_length=1, description="Verified evidence IDs supporting this action")
    claim_ids: List[str] = Field(default_factory=list, description="Claim IDs supporting this action")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Additional structured parameters")


class Action(BaseModel):
    """
    Complete structured action model fulfilling Anvaya's Trust and Lifecycle Contract.
    """
    id: str = Field(description="Unique action ID, e.g. act_7f9b8c2d")
    type: ActionType = Field(description="Action type")
    repo: str = Field(description="Target repository")
    target: str = Field(description="Target file or module")
    reviewer: Optional[str] = Field(default=None, description="Target reviewer username or email")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Validated arguments")
    reason: str = Field(description="Evidence-backed rationale")
    evidence_ids: List[str] = Field(default_factory=list, description="Associated evidence IDs")
    claim_ids: List[str] = Field(default_factory=list, description="Associated claim IDs")
    status: ActionStatus = Field(default=ActionStatus.PROPOSED, description="Lifecycle status")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approved_at: Optional[str] = None
    rejected_at: Optional[str] = None
    executed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class Outcome(BaseModel):
    """Recorded outcome of an executed action."""
    id: str = Field(description="Unique outcome ID")
    action_id: str = Field(description="ID of the executed action")
    status: ActionStatus = Field(description="Final status: SUCCEEDED or FAILED")
    success: bool = Field(description="True if execution succeeded")
    executed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    result: Dict[str, Any] = Field(default_factory=dict, description="Result payload")


class InvalidStateTransitionError(Exception):
    """Raised when an illegal action lifecycle transition is attempted."""
    pass


class ActionNotFoundError(Exception):
    """Raised when an action is not found."""
    pass


class InvalidActionError(Exception):
    """Raised when an action fails validation or has missing evidence."""
    pass
