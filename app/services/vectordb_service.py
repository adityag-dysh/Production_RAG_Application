"""Pinecone wrapper. Namespace = org_id (hard tenant boundary). The ACL filter is built HERE from the
authenticated user; callers cannot pass a filter, so client input can never widen access."""
import asyncio
from pinecone import Pinecone, ServerlessSpec

from app.core.acl import build_pinecone_filter
from app.core.config import settings

_pc: Pinecone | None = None
_index = None


def _idx():
    global _pc, _index
    if _index is None:
        _pc = Pinecone(api_key=settings.pinecone_api_key)
        _index = _pc.Index(settings.pinecone_index)
    return _index


def _ensure():
    pc = Pinecone(api_key=settings.pinecone_api_key)
    if not pc.has_index(settings.pinecone_index):
        pc.create_index(name=settings.pinecone_index, dimension=settings.embed_dim, metric="cosine",
                        spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region))


async def ensure_index() -> None:
    if settings.pinecone_api_key:
        await asyncio.to_thread(_ensure)


async def upsert(org_id: str, vectors: list[dict]) -> None:
    for i in range(0, len(vectors), 100):
        batch = vectors[i:i + 100]
        await asyncio.to_thread(lambda b=batch: _idx().upsert(vectors=b, namespace=org_id))


async def query(user, vector: list[float], top_k: int) -> list[dict]:
    flt = build_pinecone_filter(user.clearance_level, user.groups)
    res = await asyncio.to_thread(lambda: _idx().query(
        vector=vector, top_k=top_k, namespace=user.org_id, filter=flt, include_metadata=False))
    return [{"id": m["id"], "score": m["score"]} for m in res["matches"]]


async def update_acl(org_id: str, ids: list[str], clearance: int, groups: list[str]) -> None:
    for cid in ids:
        await asyncio.to_thread(lambda c=cid: _idx().update(
            id=c, set_metadata={"clearance_level": clearance, "allowed_groups": groups}, namespace=org_id))


async def delete(org_id: str, ids: list[str]) -> None:
    for i in range(0, len(ids), 500):
        batch = ids[i:i + 500]
        await asyncio.to_thread(lambda b=batch: _idx().delete(ids=b, namespace=org_id))
