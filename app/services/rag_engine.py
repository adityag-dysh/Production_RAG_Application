"""Lean CRAG + session memory.
 Normal question : retrieve -> rerank -> gate (answer / grade / rewrite once / abstain).
 'This doc' follow-up : the previous answer's document(s) are loaded directly (ACL re-checked), no search.
History (a) turns follow-ups into standalone questions, (b) provides conversational context.
Facts must come from the passages, never from the history."""
import asyncio
import logging
import time
import uuid

from app.api.v1.schemas.RAG_schemas import Citation, GradeResult
from app.core.acl import acl_allows
from app.core.config import settings
from app.core.logging import trace_id_var
from app.services import (audit_service, cache_service, gemini_service, mongodb_service as mdb,
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
async def _condense(question: str, history: list[dict]) -> tuple[str, bool]:
    """Returns (standalone_question, refers_to_previous_document). No LLM call on the first turn."""
    if not history:
        return question, False
    try:
        r = await gemini_service.generate_json(
            template_manager.render("condense_question.j2", question=question, history=history))
        standalone = str(r.get("standalone") or question).strip()[:500] or question
        return standalone, bool(r.get("refers_to_previous_document"))
    except Exception:
        log.exception("condense_failed")
        return question, False


async def _focus_chunks(user, doc_ids: list[str]) -> list[dict]:
    """Chunks of the previously used document(s), in reading order, up to focus_max_chars.
    Every chunk is re-checked against CURRENT access, so a tightened document is never shown."""
    rows = await mdb.col("chunks").find(
        {"doc_id": {"$in": doc_ids[:3]}, "org_id": user.org_id}).limit(500).to_list(None)
    rows = [r for r in rows if acl_allows(user.clearance_level, user.groups,
                                          r["clearance_level"], r["allowed_groups"])]

    def order(r):  # chunk ids look like "<doc_id>:<n>"; sort numerically, not as text
        try:
            return r["doc_id"], int(r["_id"].rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return r["doc_id"], 0

    out, used = [], 0
    for r in sorted(rows, key=order):
        if used + len(r["text"]) > settings.focus_max_chars:
            break
        out.append({"chunk_id": r["_id"], "doc_id": r["doc_id"], "title": r["title"], "text": r["text"],
                    "location": r.get("location", ""), "confidence": 1.0})
        used += len(r["text"])
    return out


async def _sources(user, ctx: list[dict], cites: list[Citation]) -> list[dict]:
    """One entry per FILE with its short summary and full passage text for the Inspector."""
    ids = list({c["doc_id"] for c in ctx})
    docs = await mdb.col("documents").find(
        {"_id": {"$in": ids}, "org_id": user.org_id},
        {"filename": 1, "doc_type": 1, "classification_level": 1, "department": 1, "dynamic_metadata.summary": 1}).to_list(None)
    meta = {d["_id"]: d for d in docs}
    out: dict[str, dict] = {}
    for c in cites:
        d = meta.get(c.doc_id, {})
        s = out.setdefault(c.doc_id, {
            "doc_id": c.doc_id, "title": c.title, "filename": d.get("filename", ""),
            "doc_type": d.get("doc_type", ""),
            "department": d.get("department", ""),
            "classification_level": d.get("classification_level", 0),
            "summary": ((d.get("dynamic_metadata") or {}).get("summary") or "")[:500],
            "passages": []})
        s["passages"].append({"n": c.n, "location": c.location, "score": c.score, "text": c.snippet})
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
    standalone, wants_prev_doc = await _condense(question, history)

    # 1. Check semantic cache first (only if no conversation history or document focus)
    cached = None
    query_emb = []
    if not history and not wants_prev_doc:
        try:
            query_emb = await gemini_service.embed_query(standalone)
            cached = await cache_service.get_cached_answer(user.org_id, user.clearance_level, query_emb)
        except Exception:
            pass

    if cached:
        yield "context", cached["cites"]
        yield "sources", cached["sources"]
        yield "token", cached["answer"]
        answer = cached["answer"]
        sources = cached["sources"]
        cites_data = cached["cites"]
        abstained = (answer == ABSTAIN)
        mode, conf, rewritten = "cache_hit", 1.0, None
    else:
        rewritten, mode = None, "rag"
        ctx: list[dict] = []
        conf: float | None = 0.0

        if wants_prev_doc:
            last = next((m for m in reversed(history) if m["role"] == "assistant" and m["doc_ids"]), None)
            if last:
                ctx = await _focus_chunks(user, last["doc_ids"])
                if ctx:
                    mode, conf = "document_focus", None
        if mode == "rag":
            ctx, conf = await _retrieve(user, standalone)
            if conf < settings.confidence_hi:
                ok = conf >= settings.confidence_lo and await _grade(standalone, ctx)
                if not ok:
                    rewritten = await _rewrite(standalone)
                    ctx2, conf2 = await _retrieve(user, rewritten)
                    if conf2 > conf:
                        ctx, conf = ctx2, conf2

        abstained = (not ctx) or (conf is not None and conf < settings.confidence_lo)
        cites = [Citation(n=i + 1, chunk_id=c["chunk_id"], doc_id=c["doc_id"], title=c["title"],
                          location=c["location"], score=round(c["confidence"], 4), snippet=c["text"])
                 for i, c in enumerate(ctx)]
        sources = []
        answer = ""
        if abstained:
            answer = ABSTAIN
            yield "token", answer
        else:
            sources = await _sources(user, ctx, cites)
            cites_dicts = [c.model_dump() for c in cites]
            yield "context", cites_dicts
            yield "sources", sources
            prompt = template_manager.render("rag_generation.j2", question=standalone, contexts=ctx, history=history)
            async for tok in gemini_service.generate_stream(prompt):
                answer += tok
                yield "token", tok
            
            # Store in semantic cache
            if not wants_prev_doc and not abstained and query_emb:
                _bgtask(cache_service.set_cached_answer(
                    user.org_id, user.clearance_level, standalone, query_emb, answer, sources, cites_dicts
                ))

    latency = int((time.perf_counter() - t0) * 1000)
    used_docs = sorted({c["doc_id"] for c in cached.get("cites", [])} if cached else {c["doc_id"] for c in ctx})
    if session_id:  # saved together at the end so a failed stream never leaves half a turn
        await session_service.add_message(user, session_id, "user", question)
        await session_service.add_message(user, session_id, "assistant", answer,
                                          [] if abstained else used_docs, sources)
    log_id = await obs.save_query_log({
        "org_id": user.org_id, "user_id": user.user_id, "session_id": session_id, "trace_id": trace_id,
        "mode": mode, "question": question, "standalone": standalone if standalone != question else None,
        "rewritten": rewritten, "answer": answer, "confidence": conf, "abstained": abstained,
        "latency_ms": latency, "chunk_ids": [] if cached else [c["chunk_id"] for c in ctx]})
    await audit_service.record("query", user, doc_ids=used_docs, log_id=log_id, session_id=session_id)
    if ctx and not abstained:
        _bgtask(obs.run_sampled_eval(log_id, standalone, answer, [c["text"] for c in ctx]))
    yield "done", {"log_id": log_id, "trace_id": trace_id, "session_id": session_id, "mode": mode,
                   "confidence": None if conf is None else round(conf, 4), "abstained": abstained,
                   "latency_ms": latency, "rewritten_query": rewritten}