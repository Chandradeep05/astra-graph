from typing import List, Optional
from pydantic import BaseModel, Field


class GitTouch(BaseModel):
    """Represents a file touched by a commit."""
    path: str
    added: int = 0
    deleted: int = 0
    module: str = "."


class GitCommit(BaseModel):
    """Represents a commit parsed from git history."""
    sha: str
    author_name: str
    author_email: str
    timestamp: str
    subject: str
    touches: List[GitTouch] = Field(default_factory=list)


class IngestRequest(BaseModel):
    """API payload to ingest a git repository."""
    repo_path: str
    repo_name: Optional[str] = None
    max_commits: int = 2000
    since: Optional[str] = None


class IngestSummary(BaseModel):
    """Summary metrics of an ingestion run."""
    repo_name: str
    head_sha: str
    commit_count: int
    person_count: int
    file_count: int
    module_count: int
    sync_id: str
    synced_at: str
