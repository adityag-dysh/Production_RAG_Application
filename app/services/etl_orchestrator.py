"""Ingestion: blob (storage) -> Mongo doc (SSOT) -> parse/chunk -> Gemini enrich -> chunks in Mongo -> embed -> Pinecone.
Idempotent (deterministic chunk ids, delete-then-insert) so queue retries are safe.
Status: pending -> processing -> indexed | failed"""
import asyncio
import io
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.services import gemini_service, mongodb_service as mdb, storage_service, vectordb_service
from app.services.observability_service import traceable
from app.services.parsing import Chunk, parse

log = logging.getLogger("app.etl")
_tasks: set[asyncio.Task] = set()


async def enqueue(doc_id: str) -> None:
    if settings.queue_mode == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("process_document_job", doc_id)
    else:  # dev only: not durable
        t = asyncio.create_task(process_document(doc_id))
        _tasks.add(t); t.add_done_callback(_tasks.discard)


def _expand_zip(data: bytes) -> list[tuple[str, bytes]]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > settings.zip_max_files or sum(i.file_size for i in infos) > settings.zip_max_uncompressed_mb * 2**20:
        raise ValueError("zip too large (possible zip bomb)")
    return [(i.filename, zf.read(i)) for i in infos if ".." not in i.filename and not i.filename.startswith("/")]


@traceable(name="ingest.chunk")
def _chunk(filename: str, data: bytes, doc_type: str) -> list[Chunk]:
    if filename.lower().endswith(".zip"):  # directory upload
        out: list[Chunk] = []
        for name, content in _expand_zip(data):
            try:
                out += parse(name, content, "code" if doc_type == "code" else doc_type)
            except Exception:
                log.warning("zip_member_skipped", extra={"member": name})
        return out
    return parse(filename, data, doc_type)


@traceable(name="ingest.process_document")
async def process_document(doc_id: str, raise_on_error: bool = False) -> None:
    docs = mdb.col("documents")
    doc = await docs.find_one({"_id": doc_id})
    if not doc:
        return
    log.info("etl_start", extra={"doc_id": doc_id, "org_id": doc["org_id"]})
    await docs.update_one({"_id": doc_id}, {"$set": {"status": "processing", "error": None}})
    try:
        data = await storage_service.load(doc["blob_uri"])
        chunks = await asyncio.to_thread(_chunk, doc["filename"], data, doc["doc_type"])
        if not chunks:
            raise ValueError("No extractable text")
        sample = "\n".join(c.text for c in chunks[:6])
        dyn = await gemini_service.extract_dynamic_metadata(sample, doc["filename"], doc["doc_type"])
        # ACL fields are copied from the human-supplied doc record ONLY.
        acl = {"org_id": doc["org_id"], "clearance_level": doc["classification_level"],
               "allowed_groups": doc["allowed_groups"]}
        records = [{"_id": f"{doc_id}:{i}", "doc_id": doc_id, "title": doc["title"], "text": c.text,
                    "location": c.location, "meta": c.meta, **acl} for i, c in enumerate(chunks)]
        await mdb.col("chunks").delete_many({"doc_id": doc_id})
        await mdb.col("chunks").insert_many(records)
        # contextual prefix improves embedding quality
        inputs = [f"{doc['title']} | {r['location']}\n{r['text']}"[:6000] for r in records]
        vecs = await gemini_service.embed_documents(inputs)
        await vectordb_service.upsert(doc["org_id"], [
            {"id": r["_id"], "values": v,
             "metadata": {"doc_id": doc_id, "clearance_level": acl["clearance_level"],
                          "allowed_groups": acl["allowed_groups"]}}
            for r, v in zip(records, vecs)])
        await docs.update_one({"_id": doc_id}, {"$set": {
            "status": "indexed", "chunk_count": len(records), "dynamic_metadata": dyn.model_dump(),
            "indexed_at": datetime.now(timezone.utc)}})
        log.info("etl_done", extra={"doc_id": doc_id, "chunks": len(records)})
    except Exception as e:
        log.exception("etl_failed", extra={"doc_id": doc_id})
        await docs.update_one({"_id": doc_id}, {"$set": {"status": "failed", "error": str(e)[:500]}})
        if raise_on_error:
            raise


async def create_document(user, filename: str, data: bytes, meta) -> dict:
    doc_id = uuid.uuid4().hex
    uri = await storage_service.save(user.org_id, doc_id, filename, data)
    doc = {"_id": doc_id, "org_id": user.org_id, "owner_id": user.user_id, "filename": Path(filename).name,
           "blob_uri": uri, "size": len(data), "status": "pending", "created_at": datetime.now(timezone.utc),
           "title": meta.title, "department": meta.department, "doc_type": meta.doc_type.value,
           "classification_level": meta.classification_level, "allowed_groups": meta.allowed_groups}
    await mdb.col("documents").insert_one(doc)
    await enqueue(doc_id)
    return doc
