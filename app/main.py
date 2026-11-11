import os
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.endpoints import auth, etl, observability, rag
from app.core.config import settings
from app.core.exceptions import register_handlers
from app.core.logging import setup_logging, trace_id_var
from app.mcp.server import auth_middleware, mcp
from app.services import mongodb_service, vectordb_service

log = logging.getLogger("app.http")


def _make_mcp_http_app():
    """Build FastMCP's streamable HTTP app for mounting at /mcp without creating /mcp/mcp."""
    try:
        old_path = mcp.settings.streamable_http_path
        mcp.settings.streamable_http_path = "/"
        try:
            return mcp.streamable_http_app()
        finally:
            mcp.settings.streamable_http_path = old_path
    except AttributeError:
        return mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await mongodb_service.ensure_indexes()
    await vectordb_service.ensure_index()

    mcp_child_app = getattr(app.state, "mcp_http_app", None)
    if mcp_child_app:
        async with mcp_child_app.router.lifespan_context(mcp_child_app):
            yield
    else:
        yield

    logging.getLogger("app").info("retrieval_mode", extra={"mode": settings.retrieval_mode})

    from app.services import observability_service as obs
    logging.getLogger("app").info("langsmith_tracing", extra={
        "enabled": obs.TRACING_ON, "project": os.getenv("LANGSMITH_PROJECT")})
        
def create_app() -> FastAPI:
    app = FastAPI(title="Multi-tenant RAG", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    register_handlers(app)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        trace_id_var.set(uuid.uuid4().hex)
        t0 = time.perf_counter()
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        log.info("request", extra={"path": request.url.path, "method": request.method,
                                   "status": resp.status_code, "ms": int((time.perf_counter() - t0) * 1000)})
        return resp

    for r in (auth.router, etl.router, rag.router, observability.router):
        app.include_router(r, prefix="/api/v1")

    if settings.mcp_enabled:
        mcp_child_app = _make_mcp_http_app()
        app.state.mcp_http_app = mcp_child_app
        app.mount("/mcp", auth_middleware(mcp_child_app), name="mcp")

    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.app_env == "dev")
