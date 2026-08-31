from fastapi import APIRouter

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/entries")
def list_audit_entries() -> dict:
    return {"status": "not_implemented", "detail": "audit trail read endpoints arrive in Phase 5"}
