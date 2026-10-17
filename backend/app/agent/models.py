from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.evidence.models import Claim, Evidence, RefusalCode
from app.tools.models import ReviewerCandidate


class ToolCall(BaseModel):
    """
    Structured representation of a tool call selected by an LLM or fallback selector.
    The model cannot execute arbitrary code or Cypher.
    """
    tool: str = Field(description="Name of the registered tool")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments for the selected tool")


class AskRequest(BaseModel):
    """Incoming user request."""
    message: str = Field(description="Natural language question")
    repo: str = Field(default="arth", description="Target repository name")


class AskResponse(BaseModel):
    """
    Verified agent response conforming to Anvaya's Trust Contract.
    Contains both machine-readable verified claims/evidence and human-readable narration.
    """
    question: str
    selected_tool: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    refused: bool = False
    reason_code: Optional[RefusalCode] = None
    reason: Optional[str] = None
    claims: List[Claim] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    candidates: List[ReviewerCandidate] = Field(default_factory=list)
    narration: str = ""
    sync_info: Optional[Dict[str, Any]] = None
    is_stale: bool = False
