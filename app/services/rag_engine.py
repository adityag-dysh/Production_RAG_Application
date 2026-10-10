"""Lean CRAG + session memory.
 conf >= HI : answer.   LO <= conf < HI : one cheap LLM grade.   Fails / < LO : rewrite ONCE, retry, else abstain.
History only (a) turns follow-ups into standalone questions and (b) gives conversational context.
Every turn still retrieves fresh through the ACL; facts must come from the passages."""
import asyncio
import logging
import time
import uuid

from app.api.v1.schemas.RAG_schemas import Citation, GradeResult
from app.core.config import settings
from app.core.logging import trace_id_var
from app.services import (audit_service, gemini_service, mongodb_service as mdb,
                          observability_service as obs, reranker_service, retriever_service,
                          session_service, template_manager)

log = logging.getLogger("app.rag")
_bg: set[asyncio.Task] = set()
ABSTAIN = "I couldn't find information you have access to that answers this question."


@obs.traceable(name="rag.retrieve_rerank")
async def _retrieve(user, query: str) -> tuple[list[dict], float]:
    cands = await retriever_service.retrieve(user, query)
    ctx = await reranker_service.rerank(query, cands, settings.context_top_n)
    return ctx, (max((c["confidence"] for c in ctx), default=0.0))


@obs.traceable(name="rag.grade")
async def _grade(question: str, ctx: list[dict]) -> bool:
    try:
        r = await gemini_service.generate_json(
            template_manager.render("crag_eval.j2", question=question, contexts=ctx[:3]))
        return GradeResult(**r).relevant
    except Exception:
        log.exception("grade_failed")
        return True


@obs.traceable(name="rag.rewrite")
async def _rewrite(question: str) -> str:
    try:
        out = await gemini_service.generate_text(template_manager.render("query_rewrite.j2", question=question))
        return out.strip() or question
    except Exception:
        return question


@obs.traceable(name="rag.condense")
async def _condense(question: str, history: list[dict]) -> str:
    if not history:  # first turn: no extra LLM call
        return question
    try:
        out = await gemini_service.generate_text(
            template_manager.render("condense_question.j2", question=question, history=history))
        return out.strip()[:500] or question
    except Exception:
        log.exception("condense_failed")
        return question


async def _sources(user, ctx: list[dict], cites: list[Citation]) -> list[dict]:
    """One entry per FILE (several chunks of the same file are grouped), with its short summary.
    Summaries are document-level; ACL is document-level too, so showing them leaks nothing."""
    ids = list({c["doc_id"] for c in ctx})
    docs = await mdb.col("documents").find(
        {"_id": {"$in": ids}, "org_id": user.org_id},
        {"filename": 1, "doc_type": 1, "dynamic_metadata.summary": 1}).to_list(None)
    meta = {d["_id"]: d for d in docs}
    out: dict[str, dict] = {}
    for c in cites:  # already in rank order
        d = meta.get(c.doc_id, {})
        s = out.setdefault(c.doc_id, {
            "doc_id": c.doc_id, "title": c.title, "filename": d.get("filename", ""),
            "doc_type": d.get("doc_type", ""),
            "summary": ((d.get("dynamic_metadata") or {}).get("summary") or "")[:300],
            "passages": []})
        s["passages"].append({"n": c.n, "location": c.location, "score": c.score})
    return list(out.values())


def _bgtask(coro):
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


async def answer_stream(user, question: str, session_id: str | None = None):
    """Async generator of (event, data). Events: context, sources, token, done."""
    t0 = time.perf_counter()
    trace_id = uuid.uuid4().hex
    trace_id_var.set(trace_id)

    history = await session_service.load_history(user, session_id) if session_id else []
    standalone = await _condense(question, history)

    rewritten = None
    ctx, conf = await _retrieve(user, standalone)
    if conf < settings.confidence_hi:
        ok = conf >= settings.confidence_lo and await _grade(standalone, ctx)
        if not ok:
            rewritten = await _rewrite(standalone)
            ctx2, conf2 = await _retrieve(user, rewritten)
            if conf2 > conf:
                ctx, conf = ctx2, conf2

    abstained = (not ctx) or conf < settings.confidence_lo
    cites = [Citation(n=i + 1, chunk_id=c["chunk_id"], doc_id=c["doc_id"], title=c["title"],
                      location=c["location"], score=round(c["confidence"], 4), snippet=c["text"][:300])
             for i, c in enumerate(ctx)]
    sources: list[dict] = []
    answer = ""
    if abstained:
        answer = ABSTAIN
        yield "token", answer
    else:
        sources = await _sources(user, ctx, cites)
        yield "context", [c.model_dump() for c in cites]
        yield "sources", sources
        prompt = template_manager.render("rag_generation.j2", question=standalone, contexts=ctx, history=history)
        async for tok in gemini_service.generate_stream(prompt):
            answer += tok
            yield "token", tok

    latency = int((time.perf_counter() - t0) * 1000)
    retrieved_docs = sorted({c["doc_id"] for c in ctx})
    if session_id:  # saved together at the end so a failed stream never leaves half a turn
        await session_service.add_message(user, session_id, "user", question)
        await session_service.add_message(user, session_id, "assistant", answer,
                                          [] if abstained else retrieved_docs, sources)
    log_id = await obs.save_query_log({
        "org_id": user.org_id, "user_id": user.user_id, "session_id": session_id, "trace_id": trace_id,
        "question": question, "standalone": standalone if standalone != question else None,
        "rewritten": rewritten, "answer": answer, "confidence": conf, "abstained": abstained,
        "latency_ms": latency, "chunk_ids": [c["chunk_id"] for c in ctx]})
    await audit_service.record("query", user, doc_ids=retrieved_docs, log_id=log_id, session_id=session_id)
    if not abstained:
        _bgtask(obs.run_sampled_eval(log_id, standalone, answer, [c["text"] for c in ctx]))
    yield "done", {"log_id": log_id, "trace_id": trace_id, "session_id": session_id,
                   "confidence": round(conf, 4), "abstained": abstained,
                   "latency_ms": latency, "rewritten_query": rewritten}