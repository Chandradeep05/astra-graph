from fastapi import APIRouter, Depends, HTTPException, status
from app.context.models import ContextPackage, ContextPackageRequest
from app.context.service import ContextPackageService
from app.db.falkor import FalkorDBManager, get_falkor_manager

router = APIRouter(tags=["Context"])


@router.post("/context", response_model=ContextPackage)
def create_context_package(
    req: ContextPackageRequest,
    db_manager: FalkorDBManager = Depends(get_falkor_manager),
) -> ContextPackage:
    """
    Generates a deterministic, bounded, machine-readable repository or subject
    Context Package exclusively from FalkorDB knowledge graph relationships.

    Guarantees:
    - Never uses an ungrounded LLM to hallucinate graph facts.
    - Fails closed with refusal and reason_code when FalkorDB is unreachable.
    - Enforces bounds on depth, file count, and evidence records.
    - All quantitative claims are verified via EvidenceVerifier.
    """
    try:
        service = ContextPackageService(db_manager=db_manager)
        return service.get_context_package(
            repository=req.repository,
            subject=req.subject,
            subject_type=req.subject_type,
            depth=req.depth,
            max_files=req.max_files,
            max_contributors=req.max_contributors,
            max_evidence=req.max_evidence,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating context package: {str(exc)}",
        )
