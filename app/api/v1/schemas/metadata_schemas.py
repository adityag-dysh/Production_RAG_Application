from enum import Enum
from pydantic import BaseModel, Field, field_validator


class DocType(str, Enum):
    policy = "policy"
    report = "report"
    code = "code"
    log = "log"
    other = "other"


class MandatoryMetadata(BaseModel):
    """Unskippable, human-supplied. These (and only these) drive ACL."""
    title: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=100)
    classification_level: int = Field(ge=0, le=4, description="0=public .. 4=CEO/board")
    doc_type: DocType
    allowed_groups: list[str] = Field(default_factory=lambda: ["all"])

    @field_validator("allowed_groups")
    @classmethod
    def _groups(cls, v):
        v = sorted({g.strip().lower() for g in v if g.strip()})
        return v or ["all"]


class DynamicMetadata(BaseModel):
    """LLM-generated. Retrieval/UX only, NEVER used for access control."""
    summary: str = ""
    entities: list[str] = []
    keywords: list[str] = []
    topics: list[str] = []
    code_symbols: list[str] = []
