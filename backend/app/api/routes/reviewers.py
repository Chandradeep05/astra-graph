from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.tools.reviewers import reviewers_for
from app.tools.models import ReviewersResponse

router = APIRouter(tags=["Reviewers"])


@router.get("/reviewers", response_model=ReviewersResponse)
def get_reviewers(
    repo: str = Query("arth", description="Repository name"),
    file: Optional[str] = Query(None, description="Target file path"),
    module: Optional[str] = Query(None, description="Target module directory"),
    limit: int = Query(10, ge=1, le=50, description="Max candidates to return"),
    max_evidence: int = Query(10, ge=1, le=50, description="Max evidence SHAs per candidate"),
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
):
    """
    Retrieves evidence-backed candidate reviewers for a file or module
    using deterministic graph traversal over commit history.
    """
    if not file and not module:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'file' or 'module' must be provided.",
        )
    if file and module:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specify either 'file' or 'module', not both.",
        )

    try:
        response = reviewers_for(
            target_file=file,
            target_module=module,
            repo_name=repo,
            limit=limit,
            max_evidence=max_evidence,
            db_manager=db_manager,
        )
        return response
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error computing reviewers: {str(exc)}",
        )
