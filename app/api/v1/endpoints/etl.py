import hashlib

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.v1.schemas.RAG_schemas import RecordIngestRequest
from app.api.v1.schemas.auth_schemas import UserContext
from app.api.v1.schemas.metadata_schemas import DocType, MandatoryMetadata
from app.core.acl import mongo_acl_filter
from app.core.config import settings
from app.core.exceptions import AppError, Forbidden, NotFound
from app.core.security import get_current_user, require_uploader
from app.services import audit_service, etl_orchestrator, mongodb_service as mdb, storage_service, vectordb_service

router = APIRouter(prefix="/etl", tags=["ingestion"])
_PUBLIC = {"blob_uri": 0, "owner_id": 0}


def _check_level(user: UserContext, level: int) -> None:
    if level > user.clearance_level:  # cannot classify above your own clearance
        raise Forbidden("Classification level exceeds your clearance")


async def _visible_doc(user: UserContext, doc_id: str) -> dict:
    # d = await mdb.col("documents").find_one({"_id": doc_id, **mongo_acl_filter(user.org_id, user.clearance_level, user.groups)})
    d = await mdb.col("documents").find_one({
        "_id": doc_id,
        **mongo_acl_filter(user.org_id, user.clearance_level, user.groups,
                           level_field="classification_level")})
    if not d:
        raise NotFound("Document not found")  # 404, not 403: don't reveal existence
    return d


@router.post("/upload", status_code=202)
async def upload(file: UploadFile = File(...), title: str = Form(...), department: str = Form(...),
                 classification_level: int = Form(...), doc_type: DocType = Form(...),
                 allowed_groups: str = Form("all"), user: UserContext = Depends(require_uploader)):
    meta = MandatoryMetadata(title=title, department=department, classification_level=classification_level,
                             doc_type=doc_type, allowed_groups=allowed_groups.split(","))
    _check_level(user, meta.classification_level)

    # Read up to max_upload_mb + 1 byte to check if it's within standard limit
    limit_bytes = settings.max_upload_mb * 2**20
    data = await file.read(limit_bytes + 1)
    
    if len(data) > limit_bytes:
        # If file is larger than 25MB, check against max_large_upload_mb (e.g. 200MB)
        large_limit_bytes = settings.max_large_upload_mb * 2**20
        if len(data) > large_limit_bytes:
            raise AppError(f"File exceeds maximum allowed large upload size of {settings.max_large_upload_mb} MB")
        
        # Read the remainder of the large file in chunks
        chunks = [data]
        total_len = len(data)
        while True:
            chunk = await file.read(10 * 2**20)  # read 10MB parts
            if not chunk:
                break
            chunks.append(chunk)
            total_len += len(chunk)
            if total_len > large_limit_bytes:
                raise AppError(f"File exceeds maximum allowed large upload size of {settings.max_large_upload_mb} MB")
        data = b"".join(chunks)

    if settings.dedup_uploads_enabled:
        sha = hashlib.sha256(data).hexdigest()
        existing = await mdb.col("documents").find_one(
            {"org_id": user.org_id, "source_sha256": sha, "status": {"$in": ["pending", "processing", "indexed"]}},
            {"_id": 1, "status": 1, "split_total": 1})
        if existing:
            await audit_service.record("upload_dedup", user, doc_id=existing["_id"])
            return {"doc_id": existing["_id"], "status": existing["status"],
                    "deduplicated": True, "parts": existing.get("split_total", 1)}

    doc = await etl_orchestrator.create_document(user, file.filename or "upload", data, meta)
    await audit_service.record("upload", user, doc_id=doc["_id"], level=meta.classification_level)
    return {"doc_id": doc["_id"], "status": doc["status"], "parts": doc.get("_split_total", 1)}


@router.post("/records", status_code=202)
async def ingest_record(body: RecordIngestRequest, user: UserContext = Depends(require_uploader)):
    """Real-time push path (e.g. from a webhook/service account): text record -> same pipeline."""
    meta = MandatoryMetadata(title=body.title, department=body.department,
                             classification_level=body.classification_level, doc_type=body.doc_type,
                             allowed_groups=body.allowed_groups)
    _check_level(user, meta.classification_level)
    doc = await etl_orchestrator.create_document(user, f"{body.title}.txt", body.text.encode(), meta)
    await audit_service.record("ingest_record", user, doc_id=doc["_id"])
    return {"doc_id": doc["_id"], "status": doc["status"]}


@router.get("/documents")
async def list_documents(user: UserContext = Depends(get_current_user)):
    # flt = mongo_acl_filter(user.org_id, user.clearance_level, user.groups)
    flt = mongo_acl_filter(user.org_id, user.clearance_level, user.groups,
                           level_field="classification_level")
    return await mdb.col("documents").find(flt, _PUBLIC).sort("created_at", -1).to_list(200)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, user: UserContext = Depends(get_current_user)):
    d = await _visible_doc(user, doc_id)
    return {k: v for k, v in d.items() if k not in _PUBLIC}


@router.patch("/documents/{doc_id}/acl")
async def update_acl(doc_id: str, classification_level: int = Form(...), allowed_groups: str = Form("all"),
                     user: UserContext = Depends(require_uploader)):
    d = await _visible_doc(user, doc_id)
    if d["owner_id"] != user.user_id and not user.is_admin:
        raise Forbidden("Only the owner or an admin can change access")
    meta = MandatoryMetadata(title=d["title"], department=d["department"], doc_type=d["doc_type"],
                             classification_level=classification_level, allowed_groups=allowed_groups.split(","))
    _check_level(user, meta.classification_level)
    acl = {"clearance_level": meta.classification_level, "allowed_groups": meta.allowed_groups}
    # Order matters for safety when tightening: Mongo (SSOT + recheck) first, then Pinecone.
    await mdb.col("documents").update_one({"_id": doc_id}, {"$set": {
        "classification_level": acl["clearance_level"], "allowed_groups": acl["allowed_groups"]}})
    await mdb.col("chunks").update_many({"doc_id": doc_id}, {"$set": acl})
    ids = [c["_id"] async for c in mdb.col("chunks").find({"doc_id": doc_id}, {"_id": 1})]
    await vectordb_service.update_acl(user.org_id, ids, acl["clearance_level"], acl["allowed_groups"])
    await audit_service.record("acl_change", user, doc_id=doc_id, **acl)
    return {"doc_id": doc_id, "updated_chunks": len(ids)}


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, user: UserContext = Depends(require_uploader)):
    d = await _visible_doc(user, doc_id)
    if d["owner_id"] != user.user_id and not user.is_admin:
        raise Forbidden("Only the owner or an admin can delete")
    ids = [c["_id"] async for c in mdb.col("chunks").find({"doc_id": doc_id}, {"_id": 1})]
    if ids:
        await vectordb_service.delete(user.org_id, ids)
    await mdb.col("chunks").delete_many({"doc_id": doc_id})
    await storage_service.delete(d["blob_uri"])
    await mdb.col("documents").delete_one({"_id": doc_id})
    await audit_service.record("delete", user, doc_id=doc_id)
