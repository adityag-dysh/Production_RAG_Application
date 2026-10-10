from typing import Optional
from pydantic import BaseModel, Field
from app.api.v1.schemas.metadata_schemas import DocType


# class QueryRequest(BaseModel):
#     question: str = Field(min_length=3, max_length=2000)

class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    session_id: Optional[str] = Field(default=None, max_length=64)

class Citation(BaseModel):
    n: int
    chunk_id: str
    doc_id: str
    title: str
    location: str = ""
    score: float
    snippet: str


class GradeResult(BaseModel):
    relevant: bool


class FeedbackRequest(BaseModel):
    log_id: str
    rating: int = Field(ge=-1, le=1)
    comment: Optional[str] = Field(default=None, max_length=1000)


class RecordIngestRequest(BaseModel):
    title: str
    department: str
    classification_level: int = Field(ge=0, le=4)
    doc_type: DocType = DocType.log
    allowed_groups: list[str] = ["all"]
    text: str = Field(min_length=1, max_length=2_000_000)
