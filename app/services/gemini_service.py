import json
import logging
from typing import AsyncIterator

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from app.api.v1.schemas.metadata_schemas import DynamicMetadata
from app.core.config import settings
from app.services import template_manager

log = logging.getLogger("app.gemini")
_client: genai.Client | None = None


def _c() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)


@_retry
async def _embed(texts: list[str], task: str) -> list[list[float]]:
    r = await _c().aio.models.embed_content(
        model=settings.embed_model, contents=texts,
        config=types.EmbedContentConfig(task_type=task, output_dimensionality=settings.embed_dim))
    return [e.values for e in r.embeddings]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 100):
        out += await _embed(texts[i:i + 100], "RETRIEVAL_DOCUMENT")
    return out


async def embed_query(text: str) -> list[float]:
    return (await _embed([text], "RETRIEVAL_QUERY"))[0]


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
