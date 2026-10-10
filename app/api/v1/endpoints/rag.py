import json
import logging

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.v1.schemas.RAG_schemas import FeedbackRequest, QueryRequest
from app.api.v1.schemas.auth_schemas import UserContext
from app.core.config import settings
from app.core.exceptions import NotFound
from app.core.rate_limit import limit
from app.core.security import get_current_user
from app.services import mongodb_service as mdb, rag_engine, session_service

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query", dependencies=[Depends(limit("query", settings.rate_limit_query_per_min))])
async def query(body: QueryRequest, user: UserContext = Depends(get_current_user)):
    session_id = await session_service.get_or_create(user, body.session_id, body.question)

    async def sse():
        try:
            yield f"event: session\ndata: {json.dumps(session_id)}\n\n"
            async for event, data in rag_engine.answer_stream(user, body.question, session_id):
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        except Exception:
            logging.getLogger("app.rag").exception("stream_failed")
            yield f"event: error\ndata: {json.dumps('Something went wrong')}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/sessions")
async def sessions(user: UserContext = Depends(get_current_user)):
    return await session_service.list_sessions(user)


@router.get("/sessions/{sid}/messages")
async def session_messages(sid: str, user: UserContext = Depends(get_current_user)):
    return await session_service.get_messages(user, sid)


@router.delete("/sessions/{sid}", status_code=204)
async def delete_session(sid: str, user: UserContext = Depends(get_current_user)):
    await session_service.delete(user, sid)


@router.post("/feedback")
async def feedback(body: FeedbackRequest, user: UserContext = Depends(get_current_user)):
    try:
        oid = ObjectId(body.log_id)
    except InvalidId:
        raise NotFound("Unknown query")
    r = await mdb.col("query_logs").update_one(
        {"_id": oid, "org_id": user.org_id, "user_id": user.user_id},
        {"$set": {"feedback": {"rating": body.rating, "comment": body.comment}}})
    if not r.matched_count:
        raise NotFound("Unknown query")
    return {"ok": True}