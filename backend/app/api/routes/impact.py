from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.tools.impact import change_impact
from app.tools.models import ChangeImpactResponse

router = APIRouter(tags=["Impact"])


@router.get("/impact", response_model=ChangeImpactResponse)
def get_change_impact(
    file_path: Optional[str] = Query(None, description="Path to target file"),
    file: Optional[str] = Query(None, description="Path to target file (alias)"),
    repo: str = Query("arth", description="Repository name"),
    limit: int = Query(10, ge=1, le=50, description="Max impacted files to return"),
    max_evidence: int = Query(30, ge=1, le=100, description="Max evidence records to return"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
):
    """
    Calculates deterministic, evidence-backed blast radius and historical co-change
    impact for a repository file based on FalkorDB CO_CHANGES and commit history.
    Enforces Anvaya's Trust Contract:
    - Returns structured claims and evidence records.
    - If target file is unknown in graph, refuses with NO_PATH.
    - If file exists with no co-changes, returns empty impacted files and factual signal.
    """
    target = file_path or file
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'file_path' or 'file' query parameter must be provided.",
        )

    try:
        response = change_impact(
            target_file=target,
            repo_name=repo,
            limit=limit,
            max_evidence=max_evidence,
            db_manager=db_manager,
        )
        return response
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error computing change impact: {str(exc)}",
        )
