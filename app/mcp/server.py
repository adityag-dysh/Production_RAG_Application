"""MCP server exposing RAG tools to external AI agents.
Reuses app/services (retriever, reranker, rag_engine) and app/core (ACL, auth) — no logic duplication.
Transports: streamable-http (default) | sse | stdio. Run: python -m app.mcp [--transport ...]
Endpoints: streamable-http -> /mcp ; sse -> /sse + /messages/ ."""
import argparse
import asyncio
import logging
import os
import sys
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP

from app.core.acl import mongo_acl_filter
from app.core.config import settings
from app.core.logging import setup_logging
from app.mcp.auth import bearer, resolve
from app.services import (audit_service, mongodb_service as mdb, rag_engine,
                          reranker_service, retriever_service)

log = logging.getLogger("app.mcp")

# Authenticated caller for the current request/session. Set by the ASGI middleware (http/sse)
# or once at startup for stdio. Tools read it via _require_user(); agents can never override it.
current_user: ContextVar = ContextVar("mcp_user", default=None)

mcp = FastMCP(settings.mcp_name, host=settings.mcp_host, port=settings.mcp_port)


def _require_user():
    u = current_user.get()
    if u is None:
        raise RuntimeError("Unauthenticated MCP request")
    return u


def _require_scope(name: str):
    u = _require_user()
    if u.api_scopes and name not in u.api_scopes:
        raise RuntimeError(f"Missing MCP API key scope: {name}")
    return u


@mcp.tool()
async def rag_search(query: str, top_k: int = 6) -> list[dict]:
    """ACL-filtered hybrid retrieval + rerank. Returns ranked passages with title/location/snippet."""
    u = _require_scope("read")
    cands = await retriever_service.retrieve(u, query)
    ranked = await reranker_service.rerank(query, cands, max(1, min(top_k, 20)))
    return [{"doc_id": r["doc_id"], "title": r["title"], "location": r.get("location", ""),
             "score": round(r.get("confidence", r.get("dense_score", 0.0)), 4),
             "snippet": r["text"][:300]} for r in ranked]


@mcp.tool()
async def rag_generate(query: str) -> dict:
    """Full RAG answer (retrieve -> rerank -> generate) with citations, as a single object."""
    u = _require_scope("generate")
    res = await rag_engine.answer_once(u, query)
    await audit_service.record("mcp_generate", u, log_id=res.get("log_id"))
    return res


@mcp.tool()
async def list_documents() -> list[dict]:
    """Documents the caller is permitted to see (metadata only, no file contents)."""
    u = _require_scope("documents")
    flt = mongo_acl_filter(u.org_id, u.clearance_level, u.groups, level_field="classification_level")
    docs = await mdb.col("documents").find(
        flt, {"title": 1, "doc_type": 1, "classification_level": 1, "status": 1, "chunk_count": 1}
    ).sort("created_at", -1).to_list(100)
    return [{"doc_id": d.pop("_id"), **d} for d in docs]


@mcp.tool()
async def whoami() -> dict:
    """Identity + effective clearance of the key in use (agents can self-check their scope)."""
    u = _require_scope("identity")
    return {"user_id": u.user_id, "org_id": u.org_id, "role": u.role,
            "clearance_level": u.clearance_level, "groups": u.groups, "api_scopes": u.api_scopes}


async def _send_401(send) -> None:
    body = b'{"error":"unauthorized"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def auth_middleware(inner_app):
    """Pure-ASGI wrapper: resolves the bearer key to a UserContext, or rejects with 401."""
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await inner_app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        user = await resolve(bearer(headers.get("authorization")))
        if user is None:
            return await _send_401(send)
        tok = current_user.set(user)
        try:
            await inner_app(scope, receive, send)
        finally:
            current_user.reset(tok)
    return wrapped


def _run_stdio():
    raw = os.getenv("RAG_API_KEY")
    user = asyncio.run(resolve(raw))
    if user is None:
        sys.exit("stdio: set a valid RAG_API_KEY env var (sk_live_...).")
    current_user.set(user)  # bound for the whole session; tools read it directly
    mcp.run(transport="stdio")


def _run_http(transport: str):
    import uvicorn
    port = int(os.getenv("PORT", settings.mcp_port))  # Render/heroku inject PORT
    app = auth_middleware(mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app())
    uvicorn.run(app, host=settings.mcp_host, port=port)


def main():
    setup_logging()
    if not settings.mcp_enabled:
        sys.exit("MCP disabled (set MCP_ENABLED=true).")
    p = argparse.ArgumentParser(description="RAG MCP server")
    p.add_argument("--transport", choices=["streamable-http", "sse", "stdio"], default="streamable-http")
    args = p.parse_args()
    if args.transport == "stdio":
        _run_stdio()
    else:
        _run_http(args.transport)


if __name__ == "__main__":
    main()
