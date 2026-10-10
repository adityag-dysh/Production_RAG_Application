import logging
import httpx
from app.core.config import settings

log = logging.getLogger("app.rerank")


async def rerank(query: str, chunks: list[dict], top_n: int) -> list[dict]:
    """Adds chunk['confidence'] in [0,1]. Falls back to dense score if no reranker is configured."""
    if not chunks:
        return []
    if settings.cohere_api_key:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.cohere.com/v2/rerank",
                                 headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
                                 json={"model": settings.rerank_model, "query": query, "top_n": top_n,
                                       "documents": [x["text"][:4000] for x in chunks]})
                r.raise_for_status()
            out = []
            for item in r.json()["results"]:
                ch = dict(chunks[item["index"]])
                ch["confidence"] = float(item["relevance_score"])
                out.append(ch)
            return out
        except Exception:
            log.exception("rerank_failed_fallback_to_dense")
    ranked = sorted(chunks, key=lambda x: x["dense_score"], reverse=True)[:top_n]
    return [{**c, "confidence": float(c["dense_score"])} for c in ranked]
