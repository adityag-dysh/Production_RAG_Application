"""Typed application errors and the handlers that turn them into consistent JSON responses.

Every AppError becomes {"error": <code>, "message": <text>} with the right HTTP status.
Unexpected exceptions are logged with a stack trace but return a generic 500, so internals never leak.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("app.errors")


class AppError(Exception):
    """Base class. Subclasses set status_code and code; headers is optional (e.g. Retry-After)."""
    status_code = 400
    code = "app_error"

    def __init__(self, message: str = "", headers: dict | None = None):
        super().__init__(message)
        self.message = message or self.code
        self.headers = headers or {}


class AuthError(AppError):
    status_code, code = 401, "unauthorized"


class Forbidden(AppError):
    status_code, code = 403, "forbidden"


class NotFound(AppError):
    status_code, code = 404, "not_found"


class RateLimited(AppError):
    status_code, code = 429, "rate_limited"


class PipelineError(AppError):
    status_code, code = 500, "pipeline_error"


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            {"error": exc.code, "message": exc.message},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")  # full detail goes to the logs only
        return JSONResponse({"error": "internal_error", "message": "Internal error"}, status_code=500)