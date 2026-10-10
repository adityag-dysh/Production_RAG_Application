"""Per-user / per-IP rate limiting as FastAPI dependencies.

Fixed-window counters. Backend is chosen by RATE_LIMIT_BACKEND:
  memory : per-process (fine for one worker / dev; limits multiply with multiple workers)
  redis  : shared across workers (needs Redis >= 7 for EXPIRE ... NX); falls back to memory if Redis is down
"""
import logging
import time

from fastapi import Depends, Request

from app.api.v1.schemas.auth_schemas import UserContext
from app.core.config import settings
from app.core.exceptions import RateLimited
from app.core.security import get_current_user

log = logging.getLogger("app.ratelimit")

_mem: dict[str, tuple[float, int]] = {}   # key -> (window_start_monotonic, count)
_redis = None


def _hit_memory(key: str, window: int) -> tuple[int, int]:
    now = time.monotonic()
    if len(_mem) > 10_000:  # prune expired keys so the dict can't grow forever
        for k in [k for k, (start, _) in _mem.items() if now - start >= window]:
            _mem.pop(k, None)
    start, n = _mem.get(key, (now, 0))
    if now - start >= window:
        start, n = now, 0
    n += 1
    _mem[key] = (start, n)
    return n, max(int(window - (now - start)), 1)


async def _hit_redis(key: str, window: int) -> tuple[int, int]:
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(settings.redis_url)
    pipe = _redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, window, nx=True)   # only sets TTL on the first hit of a window
    pipe.ttl(key)
    n, _, ttl = await pipe.execute()
    return int(n), max(int(ttl), 1)


async def _hit(key: str, window: int) -> tuple[int, int]:
    """Returns (hits_in_current_window, seconds_until_reset)."""
    if settings.rate_limit_backend == "redis":
        try:
            return await _hit_redis(key, window)
        except Exception:
            log.exception("redis_ratelimit_failed_using_memory")  # degrade, don't take the API down
    return _hit_memory(key, window)


def limit(bucket: str, per_min: int):
    """Dependency factory. Usage:
        @router.post("/query", dependencies=[Depends(limit("query", settings.rate_limit_query_per_min))])
    Runs after authentication (get_current_user is shared/cached within the request)."""
    async def dep(user: UserContext = Depends(get_current_user)):
        n, retry = await _hit(f"rl:{bucket}:{user.org_id}:{user.user_id}", 60)
        if n > per_min:
            log.warning("rate_limited", extra={"bucket": bucket, "user_id": user.user_id, "org_id": user.org_id})
            raise RateLimited(f"Too many {bucket} requests. Try again in {retry}s.",
                              {"Retry-After": str(retry)})
    return dep


async def login_limit(request: Request):
    """Per-IP limit for /auth/login. Behind a reverse proxy, start uvicorn with --proxy-headers
    (and trusted forwarded-allow-ips) or every request will look like the proxy's IP."""
    ip = request.client.host if request.client else "unknown"
    n, retry = await _hit(f"rl:login:{ip}", 60)
    if n > settings.rate_limit_login_per_min:
        log.warning("login_rate_limited", extra={"ip": ip})
        raise RateLimited("Too many login attempts. Please wait and try again.",
                          {"Retry-After": str(retry)})