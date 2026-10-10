"""Conversation memory. Sessions are private to (org_id, user_id): every read/write re-checks both.
History is re-validated against CURRENT document access before it is shown or sent to the model."""
import uuid
from datetime import datetime, timedelta, timezone

from app.core.acl import mongo_acl_filter
from app.core.config import settings
from app.core.exceptions import NotFound
from app.services import mongodb_service as mdb


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _own(user, sid: str) -> dict:
    return {"_id": sid, "org_id": user.org_id, "user_id": user.user_id}


async def get_or_create(user, session_id: str | None, first_question: str) -> str:
    if session_id:
        if not await mdb.col("sessions").find_one(_own(user, session_id), {"_id": 1}):
            raise NotFound("Session not found")  # 404 for "not yours" too: don't reveal existence
        return session_id
    sid, now = uuid.uuid4().hex, _now()
    await mdb.col("sessions").insert_one({
        "_id": sid, "org_id": user.org_id, "user_id": user.user_id, "title": first_question[:60],
        "created_at": now, "updated_at": now,
        "expires_at": now + timedelta(days=settings.session_ttl_days)})
    return sid


async def add_message(user, sid: str, role: str, content: str,
                      doc_ids: list[str] | None = None, sources: list[dict] | None = None) -> None:
    now = _now()
    exp = now + timedelta(days=settings.session_ttl_days)
    await mdb.col("messages").insert_one({
        "session_id": sid, "org_id": user.org_id, "user_id": user.user_id, "role": role,
        "content": content, "doc_ids": doc_ids or [], "sources": sources or [], "ts": now, "expires_at": exp})
    await mdb.col("sessions").update_one(_own(user, sid), {"$set": {"updated_at": now, "expires_at": exp}})


async def _filter_visible(user, msgs: list[dict]) -> list[dict]:
    """Drop assistant turns grounded in documents the user can no longer access (or that were deleted),
    together with the question that produced them."""
    ids = {d for m in msgs for d in m.get("doc_ids", [])}
    ok: set[str] = set()
    if ids:
        # rows = await mdb.col("documents").find(
        #     {"_id": {"$in": list(ids)}, **mongo_acl_filter(user.org_id, user.clearance_level, user.groups)},
        #     {"_id": 1}).to_list(None)

        rows = await mdb.col("documents").find(
            {"_id": {"$in": list(ids)},
             **mongo_acl_filter(user.org_id, user.clearance_level, user.groups,
                                level_field="classification_level")},
            {"_id": 1}).to_list(None)
        ok = {r["_id"] for r in rows}
    out: list[dict] = []
    for m in msgs:
        if m["role"] == "assistant" and not set(m.get("doc_ids", [])) <= ok:
            if out and out[-1]["role"] == "user":
                out.pop()
            continue
        out.append(m)
    return out


async def load_history(user, sid: str) -> list[dict]:
    """Last N messages for the prompt, as [{'role','content'}]."""
    rows = await mdb.col("messages").find(
        {"session_id": sid, "org_id": user.org_id, "user_id": user.user_id}
    ).sort("_id", -1).limit(settings.history_turns).to_list(None)
    msgs = await _filter_visible(user, list(reversed(rows)))
    while msgs and msgs[0]["role"] == "assistant":  # window cut a pair in half
        msgs.pop(0)
    return [{"role": m["role"], "content": m["content"][: settings.history_max_chars]} for m in msgs]


async def list_sessions(user) -> list[dict]:
    rows = await mdb.col("sessions").find(
        {"org_id": user.org_id, "user_id": user.user_id}, {"title": 1, "updated_at": 1}
    ).sort("updated_at", -1).limit(50).to_list(None)
    return [{"id": r["_id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]


async def get_messages(user, sid: str) -> list[dict]:
    if not await mdb.col("sessions").find_one(_own(user, sid), {"_id": 1}):
        raise NotFound("Session not found")
    rows = await mdb.col("messages").find(
        {"session_id": sid, "org_id": user.org_id, "user_id": user.user_id}).sort("_id", 1).limit(200).to_list(None)
    msgs = await _filter_visible(user, rows)
    return [{"role": m["role"], "content": m["content"], "sources": m.get("sources", [])} for m in msgs]


async def delete(user, sid: str) -> None:
    r = await mdb.col("sessions").delete_one(_own(user, sid))
    if not r.deleted_count:
        raise NotFound("Session not found")
    await mdb.col("messages").delete_many({"session_id": sid, "org_id": user.org_id, "user_id": user.user_id})