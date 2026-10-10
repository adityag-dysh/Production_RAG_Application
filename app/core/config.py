from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

from typing import Literal
from pydantic import field_validator


load_dotenv()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    jwt_secret: str = "dev-only-secret"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    cors_origins: list[str] = ["http://localhost:8000"]

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "rag_app"

    pinecone_api_key: str = ""
    pinecone_index: str = "rag-app"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768

    cohere_api_key: str = ""
    rerank_model: str = "rerank-v3.5"

    storage_backend: str = "local"
    storage_local_dir: str = "./data/blobs"
    gcs_bucket: str = ""

    queue_mode: str = "inline"
    redis_url: str = "redis://localhost:6379"

    retrieve_top_k: int = 30
    context_top_n: int = 6
    confidence_hi: float = 0.55
    confidence_lo: float = 0.20
    eval_sample_rate: float = 0.1
    max_upload_mb: int = 25
    zip_max_files: int = 500
    zip_max_uncompressed_mb: int = 100

    # retrieval
    retrieval_mode: Literal["hybrid", "dense", "sparse"] = "hybrid"
    bm25_index: str = "chunks_search"
    bm25_top_k: int = 30
    rrf_k: int = 60

    @field_validator("retrieval_mode", mode="before")
    @classmethod
    def _norm_mode(cls, v):
        return str(v).strip().lower()  # "Hybrid " -> "hybrid"; invalid values fail at startup

    # conversation memory
    session_ttl_days: int = 30
    history_turns: int = 6           # past messages (user + assistant) sent to the model
    history_max_chars: int = 1200    # per-message cap in prompts

    rate_limit_backend: str = "memory"   # memory | redis
    rate_limit_upload_per_min: int = 10
    rate_limit_query_per_min: int = 20
    rate_limit_login_per_min: int = 10


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.app_env != "dev" and s.jwt_secret == "dev-only-secret":
        raise RuntimeError("Set JWT_SECRET outside dev")
    return s


settings = get_settings()
