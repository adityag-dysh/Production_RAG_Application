import asyncio
import os
import re
from pathlib import Path
from app.core.config import settings


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name))[:120] or "file"


async def save(org_id: str, doc_id: str, filename: str, data: bytes) -> str:
    key = f"{org_id}/{doc_id}/{_safe(filename)}"
    if settings.storage_backend == "gcs":
        from google.cloud import storage
        def _up():
            storage.Client().bucket(settings.gcs_bucket).blob(key).upload_from_string(data)
        await asyncio.to_thread(_up)
        return f"gcs://{key}"
    path = Path(settings.storage_local_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, data)
    return f"local://{key}"


async def load(uri: str) -> bytes:
    scheme, key = uri.split("://", 1)
    if scheme == "gcs":
        from google.cloud import storage
        return await asyncio.to_thread(
            lambda: storage.Client().bucket(settings.gcs_bucket).blob(key).download_as_bytes())
    base = Path(settings.storage_local_dir).resolve()
    path = (base / key).resolve()
    if base not in path.parents:
        raise ValueError("path traversal")
    return await asyncio.to_thread(path.read_bytes)


async def delete(uri: str) -> None:
    scheme, key = uri.split("://", 1)
    if scheme == "gcs":
        from google.cloud import storage
        await asyncio.to_thread(lambda: storage.Client().bucket(settings.gcs_bucket).blob(key).delete())
    else:
        p = Path(settings.storage_local_dir) / key
        if p.exists():
            await asyncio.to_thread(p.unlink)
