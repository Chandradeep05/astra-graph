from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.db.falkor import FalkorDBManager, get_falkor_manager

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check(manager: FalkorDBManager = Depends(get_falkor_manager)):
    """
    Health check endpoint.
    Verifies that the API is alive and FalkorDB is connected and accepting queries.
    Returns 200 OK when healthy, 503 Service Unavailable when FalkorDB is unreachable.
    """
    db_health = manager.check_health()

    if db_health["connected"]:
        return {
            "status": "healthy",
            "falkordb": db_health,
        }

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "unhealthy",
            "falkordb": db_health,
        },
    )
