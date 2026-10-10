"""Separate audit trail: who accessed which documents/chunks. Distinct from app logs."""
import logging
from datetime import datetime, timezone
from app.services import mongodb_service as mdb

log = logging.getLogger("app.audit")


async def record(event: str, user, **details) -> None:
    entry = {"ts": datetime.now(timezone.utc), "event": event, "org_id": user.org_id,
             "user_id": user.user_id, "role": user.role, "details": details}
    try:
        await mdb.col("audit").insert_one(entry)
    except Exception:
        log.exception("audit_write_failed")  # never break the request, but make it loud
    log.info("audit", extra={"event": event, "user_id": user.user_id, "org_id": user.org_id})
