from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.tools.impact_reviewers import impact_reviewers
from app.tools.models import ImpactReviewersResponse

router = APIRouter(tags=["Impact Reviewers"])


@router.get("/impact-reviewers", response_model=ImpactReviewersResponse)
def get_impact_reviewers(
    file_path: Optional[str] = Query(None, description="Path to target file"),
    file: Optional[str] = Query(None, description="Path to target file (alias)"),
    repo: str = Query("databases", description="Repository name"),
    limit: int = Query(5, ge=1, le=50, description="Max candidate reviewers to return"),
    max_evidence: int = Query(30, ge=1, le=100, description="Max evidence records to return"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
):
    """
    Synthesizes historical co-change blast radius with author lineage to determine
    ranked, evidence-backed candidate code reviewers respecting persistent graph memory.
    Enforces Anvaya's Trust Contract:
    - Returns structured claims and evidence objects.
    - If target file has no graph path, refuses with NO_PATH.
    - If all candidates are constrained, refuses with NO_PATH.
    """
    target = file_path or file
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parameter 'file_path' or 'file' is required.",
        )

    try:
        response = impact_reviewers(
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
            detail=f"Error computing impact reviewers: {str(exc)}",
        )
