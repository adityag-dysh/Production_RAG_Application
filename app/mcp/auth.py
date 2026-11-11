"""MCP auth: resolve a caller's API key to a UserContext (server-side, never trust agent args).
The key's max_clearance caps the effective clearance so an agent can never exceed its scope."""
import asyncio
from datetime import datetime, timezone

from app.api.v1.schemas.auth_schemas import UserContext
from app.core import security
from app.core.config import settings
from app.core.passwords import verify_password
from app.services import mongodb_service as mdb


def bearer(raw_header: str | None) -> str | None:
    """Extract the key from an 'Authorization: Bearer <key>' header (or a bare sk_live_ key)."""
    if not raw_header:
        return None
    parts = raw_header.split()
    if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
        return parts[1].strip()
    if raw_header.strip().startswith("sk_live_"):
        return raw_header.strip()
    return None


async def resolve(raw_key: str | None) -> UserContext | None:
    if not raw_key or not raw_key.startswith("sk_live_"):
        return None
    prefix = raw_key[: settings.mcp_api_key_prefix_len]
    users = await mdb.col("users").find({"api_keys.prefix": prefix, "active": True}).to_list(20)
    now = datetime.now(timezone.utc)
    for u in users:
        for k in u.get("api_keys", []):
            if k.get("prefix") != prefix:
                continue
            exp = k.get("expires_at")
            if exp is not None and now > exp:
                continue
            if await asyncio.to_thread(verify_password, raw_key, k["hash"]):
                ctx = security.user_context(u)
                eff = min(ctx.clearance_level, k.get("max_clearance", ctx.clearance_level))
                return ctx.model_copy(update={
                    "clearance_level": eff,
                    "api_scopes": [s.strip().lower() for s in k.get("scopes", []) if s and s.strip()],
                })
    return None
