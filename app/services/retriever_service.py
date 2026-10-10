"""Retrieval with three modes (RETRIEVAL_MODE in .env):
  dense  : Pinecone only
  sparse : Atlas Search BM25 only
  hybrid : both in parallel, fused with Reciprocal Rank Fusion
ACL is enforced inside each store's query, then re-verified in Python before anything is returned."""
import asyncio
import logging

from app.core.acl import acl_allows, atlas_search_pipeline
from app.core.config import settings
from app.services import gemini_service, mongodb_service as mdb, vectordb_service

log = logging.getLogger("app.retriever")


def _shape(d: dict, dense: float = 0.0, bm25: float = 0.0) -> dict:
    return {
        "chunk_id": d["_id"], "doc_id": d["doc_id"], "title": d["title"], "text": d["text"],
        "location": d.get("location", ""), "org_id": d["org_id"],
        "clearance_level": d["clearance_level"], "allowed_groups": d["allowed_groups"],
        "dense_score": dense, "bm25_score": bm25,
    }


async def _dense(user, query: str) -> list[dict]:
    vec = await gemini_service.embed_query(query)
    matches = await vectordb_service.query(user, vec, settings.retrieve_top_k)
    if not matches:
        return []
    scores = {m["id"]: m["score"] for m in matches}
    docs = await mdb.col("chunks").find({"_id": {"$in": list(scores)}, "org_id": user.org_id}).to_list(None)
    return sorted((_shape(d, dense=scores[d["_id"]]) for d in docs),
                  key=lambda x: x["dense_score"], reverse=True)


async def _bm25(user, query: str, strict: bool = False) -> list[dict]:
    """strict=True (sparse mode): re-raise so a missing index is loud.
    strict=False (hybrid mode): log and return [] so dense results still work."""
    try:
        pipe = atlas_search_pipeline(settings.bm25_index, query, user.org_id,
                                     user.clearance_level, user.groups, settings.bm25_top_k)
        docs = await mdb.col("chunks").aggregate(pipe).to_list(None)
        return [_shape(d, bm25=d["bm25_score"]) for d in docs]
    except Exception:
        log.exception("bm25_failed", extra={"index": settings.bm25_index, "strict": strict})
        if strict:
            raise
        return []


def _rrf(dense: list[dict], bm25: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion: needs no score calibration between BM25 and cosine."""
    fused: dict[str, dict] = {}
    for lst in (dense, bm25):
        for rank, h in enumerate(lst):
            e = fused.setdefault(h["chunk_id"], {**h, "rrf": 0.0})
            e["dense_score"] = max(e["dense_score"], h["dense_score"])
            e["bm25_score"] = max(e["bm25_score"], h["bm25_score"])
            e["rrf"] += 1.0 / (settings.rrf_k + rank + 1)
    return sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)[: settings.retrieve_top_k]


async def retrieve(user, query: str) -> list[dict]:
    mode = settings.retrieval_mode
    if mode == "dense":
        hits = await _dense(user, query)
    elif mode == "sparse":
        hits = (await _bm25(user, query, strict=True))[: settings.retrieve_top_k]
    else:  # hybrid
        dense, bm25 = await asyncio.gather(_dense(user, query), _bm25(user, query),
                                           return_exceptions=True)
        if isinstance(dense, Exception):  # one side failing should degrade, not kill, the query
            log.error("dense_failed_in_hybrid", exc_info=dense)
            dense = []
        if isinstance(bm25, Exception):
            log.error("bm25_failed_in_hybrid", exc_info=bm25)
            bm25 = []
        hits = _rrf(dense, bm25)

    # Defence in depth: never trust the index filters alone.
    out, dropped = [], 0
    for h in hits:
        if h["org_id"] != user.org_id or not acl_allows(
                user.clearance_level, user.groups, h["clearance_level"], h["allowed_groups"]):
            dropped += 1
            continue
        out.append(h)
    if dropped:
        log.error("acl_mismatch_dropped_chunks", extra={"count": dropped, "org_id": user.org_id})
    log.info("retrieved", extra={"mode": mode, "hits": len(out)})
    return out