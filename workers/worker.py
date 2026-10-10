"""Run: arq workers.worker.WorkerSettings   (set QUEUE_MODE=arq on the API)"""
from arq.connections import RedisSettings
from app.core.config import settings
from app.core.logging import setup_logging
from app.services import etl_orchestrator, mongodb_service, vectordb_service


async def startup(ctx):
    setup_logging()
    await mongodb_service.ensure_indexes()
    await vectordb_service.ensure_index()


async def process_document_job(ctx, doc_id: str):
    await etl_orchestrator.process_document(doc_id, raise_on_error=True)  # raise => arq retries


class WorkerSettings:
    functions = [process_document_job]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_tries = 3
    job_timeout = 900
