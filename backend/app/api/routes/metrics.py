from fastapi import APIRouter

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/batch")
def batch_metrics() -> dict:
    return {"status": "not_implemented", "detail": "batch metrics land with the Phase 5 dashboard"}
