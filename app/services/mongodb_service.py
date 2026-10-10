"""Mongo = single source of truth. Separate collections: users, documents, chunks, audit, query_logs."""
import logging

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from app.core.config import settings

log = logging.getLogger("app.mongo")
_client: AsyncIOMotorClient | None = None


def _get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            settings.mongo_uri,
            tz_aware=True,
            tlsCAFile=certifi.where(),          # fixes SSL handshake errors with Atlas
            serverSelectionTimeoutMS=8000,      # fail fast on bad URI / IP not allowed
            connectTimeoutMS=8000,
            maxPoolSize=20,                     # M0 free tier allows ~500 connections total
        )
    return _client


def col(name: str) -> AsyncIOMotorCollection:
    return _get_client()[settings.mongo_db][name]


async def ping() -> None:
    await _get_client().admin.command("ping")
    log.info("mongo_connected", extra={"db": settings.mongo_db})


# async def ensure_indexes() -> None:
#     await ping()  # raises immediately with a clear error if Atlas is unreachable
#     await col("users").create_index("email", unique=True)
#     await col("documents").create_index([("org_id", 1), ("status", 1)])
#     await col("chunks").create_index([("org_id", 1), ("doc_id", 1)])
#     await col("audit").create_index([("org_id", 1), ("ts", -1)])
#     await col("query_logs").create_index([("org_id", 1), ("ts", -1)])

async def ensure_indexes() -> None:
    await ping()
    await col("users").create_index("email", unique=True)
    await col("documents").create_index([("org_id", 1), ("status", 1)])
    await col("chunks").create_index([("org_id", 1), ("doc_id", 1)])
    await col("audit").create_index([("org_id", 1), ("ts", -1)])
    await col("query_logs").create_index([("org_id", 1), ("ts", -1)])
    await col("sessions").create_index([("org_id", 1), ("user_id", 1), ("updated_at", -1)])
    await col("sessions").create_index("expires_at", expireAfterSeconds=0)   # TTL
    await col("messages").create_index([("session_id", 1), ("_id", 1)])
    await col("messages").create_index("expires_at", expireAfterSeconds=0)   # TTL