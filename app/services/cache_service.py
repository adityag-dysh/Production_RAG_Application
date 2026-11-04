"""Redis-backed semantic query cache with cosine similarity matching."""
import json
import logging
import math
import redis.asyncio as aioredis

from app.core.config import settings

log = logging.getLogger("app.cache")
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis | None:
    global _redis
    if not settings.semantic_cache_enabled:
        return None
    if _redis is None:
        try:
            _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            log.exception("redis_connection_failed")
            return None
    return _redis


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


async def get_cached_answer(org_id: str, clearance_level: int, query_embedding: list[float]) -> dict | None:
    r = _get_redis()
    if not r or not query_embedding:
        return None
    try:
        pattern = f"scache:{org_id}:*"
        keys = []
        async for key in r.scan_iter(match=pattern):
            keys.append(key)
        
        if not keys:
            return None

        # Fetch cached entries
        pipeline = r.pipeline()
        for k in keys:
            pipeline.get(k)
        vals = await pipeline.execute()

        best_match = None
        highest_sim = 0.0

        for raw in vals:
            if not raw:
                continue
            try:
                data = json.loads(raw)
                # Check clearance and similarity
                if clearance_level >= data.get("clearance_level", 0):
                    sim = _cosine_similarity(query_embedding, data.get("embedding", []))
                    if sim > highest_sim:
                        highest_sim = sim
                        best_match = data
            except Exception:
                continue

        if highest_sim >= settings.semantic_cache_threshold and best_match:
            log.info("semantic_cache_hit", extra={"similarity": highest_sim})
            return {
                "answer": best_match["answer"],
                "sources": best_match["sources"],
                "cites": best_match["cites"]
            }
    except Exception:
        log.exception("semantic_cache_get_failed")
    return None


async def set_cached_answer(org_id: str, clearance_level: int, query_text: str, query_embedding: list[float], answer: str, sources: list[dict], cites: list[dict]) -> None:
    r = _get_redis()
    if not r or not query_embedding:
        return
    try:
        import hashlib
        q_hash = hashlib.sha256(query_text.encode()).hexdigest()[:16]
        key = f"scache:{org_id}:{q_hash}"
        payload = {
            "clearance_level": clearance_level,
            "embedding": query_embedding,
            "answer": answer,
            "sources": sources,
            "cites": cites
        }
        await r.set(key, json.dumps(payload), ex=settings.semantic_cache_ttl_seconds)
    except Exception:
        log.exception("semantic_cache_set_failed")
