from fastapi import APIRouter, Depends
from app.api.v1.schemas.auth_schemas import UserContext
from app.core.security import require_admin
from app.services import mongodb_service as mdb, observability_service as obs

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/summary")
async def summary(days: int = 7, user: UserContext = Depends(require_admin)):
    return await obs.summary(user.org_id, min(max(days, 1), 90))


@router.get("/audit")
async def audit(limit: int = 100, user: UserContext = Depends(require_admin)):
    rows = await mdb.col("audit").find({"org_id": user.org_id}, {"_id": 0}).sort("ts", -1).to_list(min(limit, 500))
    return rows
