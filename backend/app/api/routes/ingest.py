from typing import Optional
import os
from fastapi import APIRouter, Depends, HTTPException, status

from app.ingest.models import IngestRequest, IngestSummary
from app.ingest.git_parser import parse_git_history
from app.ingest.graph_loader import GitGraphLoader
from app.db.falkor import FalkorDBManager, get_falkor_manager

router = APIRouter(tags=["Ingestion"])


@router.post("/ingest")
def ingest_repository(
    request: IngestRequest,
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
):
    """
    Ingests Git history from a local repository directory into FalkorDB.
    Creates Person, Commit, File, Module, Repository, and Sync nodes.

    Security: repo_path is restricted to the configured data/repos/ directory
    to prevent arbitrary filesystem traversal. graph_name is restricted to
    the canonical configured graph name only.
    """
    # Normalize and resolve the absolute path
    abs_repo_path = os.path.realpath(os.path.abspath(request.repo_path))

    # Restrict to the configured data/repos directory — prevent path traversal
    allowed_root = os.path.realpath(os.path.abspath("data/repos"))
    if not abs_repo_path.startswith(allowed_root + os.sep) and abs_repo_path != allowed_root:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Repository path '{request.repo_path}' is outside the permitted repository root. "
                f"Only paths under 'data/repos/' are allowed."
            ),
        )

    if not os.path.isdir(abs_repo_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Repository path does not exist: {request.repo_path}",
        )

    # Restrict graph_name: only canonical graph or isolated test graphs (test_*) are permitted
    from app.core.config import settings as _settings
    canonical_graph = _settings.falkordb_graph_name
    if request.graph_name and request.graph_name != canonical_graph and not request.graph_name.startswith("test_"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"graph_name '{request.graph_name}' is not permitted. "
                f"Only the canonical graph name or test graphs ('test_*') are allowed via this endpoint."
            ),
        )

    repo_name = request.repo_name or os.path.basename(abs_repo_path)

    try:
        commits = parse_git_history(
            repo_path=abs_repo_path,
            max_commits=request.max_commits,
            since=request.since,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse git history: {str(exc)}",
        )

    try:
        loader = GitGraphLoader(db_manager)
        summary = loader.load_history(
            repo_name=repo_name,
            commits=commits,
            repo_path=abs_repo_path,
            graph_name=request.graph_name,
        )
        return {
            "status": "success",
            "message": f"Successfully ingested {len(commits)} commits for '{repo_name}'",
            "summary": summary,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load graph into FalkorDB: {str(exc)}",
        )



@router.get("/sync")
def get_latest_sync(
    repo_name: Optional[str] = None,
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
):
    """
    Returns the latest repository synchronization snapshot and freshness details.
    """
    graph = db_manager.get_graph()

    query = """
    MATCH (r:Repository)
    WHERE $repo_name IS NULL OR r.name = $repo_name
    MATCH (r)-[:SNAPSHOT]->(s:Sync)
    OPTIONAL MATCH (r)-[:CURRENT_AT]->(c:Commit)
    RETURN r.name, s.id, s.head_sha, s.synced_at, s.commit_count, s.source, c.subject
    ORDER BY s.synced_at DESC
    LIMIT 1
    """

    res = graph.query(query, {"repo_name": repo_name})
    if not res.result_set:
        return {
            "status": "not_synced",
            "message": "No repository sync records found in graph.",
        }

    row = res.result_set[0]
    return {
        "status": "synced",
        "repository": row[0],
        "sync_id": row[1],
        "head_sha": row[2],
        "synced_at": row[3],
        "commit_count": row[4],
        "source": row[5],
        "head_subject": row[6],
    }
