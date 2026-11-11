import asyncio
import json
import logging
from typing import AsyncIterator

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from app.api.v1.schemas.metadata_schemas import DynamicMetadata
from app.core.config import settings
from app.core.rate_limiter import TokenBucket
from app.services import cache_service, template_manager

log = logging.getLogger("app.gemini")
_client: genai.Client | None = None
_embedding_semaphore = asyncio.Semaphore(2)  # Limit concurrent embedding requests to avoid 429 quota exhaustion


def _c() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


_retry = retry(stop=stop_after_attempt(6), wait=wait_exponential(min=2, max=60), reraise=True)

_req_bucket = TokenBucket(settings.embed_rpm_limit, settings.embed_rpm_limit)
_tok_bucket = TokenBucket(settings.embed_tpm_limit, settings.embed_tpm_limit)


@_retry
async def _embed(texts: list[str], task: str) -> list[list[float]]:
    async with _embedding_semaphore:
        if settings.embed_rate_limit_enabled:
            await _req_bucket.acquire(1)
            await _tok_bucket.acquire(max(sum(len(t) for t in texts) // 4, 1))
        r = await _c().aio.models.embed_content(
            model=settings.embed_model, contents=texts,
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=settings.embed_dim))
        return [e.values for e in r.embeddings]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed with per-chunk cache (skip Gemini on identical text) + paced batching."""
    if not texts:
        return []
    out: list[list[float] | None] = [None] * len(texts)
    miss: list[int] = []
    if settings.embed_cache_enabled:
        cached = await cache_service.get_cached_embeddings(texts, "RETRIEVAL_DOCUMENT")
        for i, c in enumerate(cached):
            if c is not None:
                out[i] = c
            else:
                miss.append(i)
    else:
        miss = list(range(len(texts)))

    for j in range(0, len(miss), 50):
        idxs = miss[j:j + 50]
        vecs = await _embed([texts[i] for i in idxs], "RETRIEVAL_DOCUMENT")
        for i, v in zip(idxs, vecs):
            out[i] = v
        if settings.embed_cache_enabled:
            await cache_service.set_cached_embeddings(
                [(texts[i], "RETRIEVAL_DOCUMENT", v) for i, v in zip(idxs, vecs)])
        if j + 50 < len(miss):
            await asyncio.sleep(1.0)
    return out  # type: ignore[return-value]


async def embed_query(text: str) -> list[float]:
    if settings.embed_cache_enabled:
        hit = (await cache_service.get_cached_embeddings([text], "RETRIEVAL_QUERY"))[0]
        if hit:
            return hit
    vec = (await _embed([text], "RETRIEVAL_QUERY"))[0]
    if settings.embed_cache_enabled:
        await cache_service.set_cached_embeddings([(text, "RETRIEVAL_QUERY", vec)])
    return vec


@_retry
async def generate_text(prompt: str) -> str:
    r = await _c().aio.models.generate_content(model=settings.gemini_model, contents=prompt)
    return r.text or ""


@_retry
async def generate_json(prompt: str) -> dict:
    r = await _c().aio.models.generate_content(
        model=settings.gemini_model, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0))
    return json.loads(r.text or "{}")


async def generate_stream(prompt: str) -> AsyncIterator[str]:
    stream = await _c().aio.models.generate_content_stream(
        model=settings.gemini_model, contents=prompt,
        config=types.GenerateContentConfig(temperature=0.1))
    async for chunk in stream:
        if chunk.text:
            yield chunk.text


async def extract_dynamic_metadata(text: str, filename: str, doc_type: str) -> DynamicMetadata:
    prompt = template_manager.render("dynamic_metadata.j2", filename=filename, doc_type=doc_type, text=text[:8000])
    try:
        return DynamicMetadata(**(await generate_json(prompt)))
    except Exception:
        log.exception("dynamic_metadata_failed")  # non-fatal: ingestion continues without it
        return DynamicMetadata()
