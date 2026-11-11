import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_MCP_SCOPES = {"read", "generate", "documents", "identity"}


def normalize_mcp_scopes(v: list[str]) -> list[str]:
    cleaned = sorted({s.strip().lower() for s in v if s.strip()})
    if not cleaned:
        raise ValueError("At least one scope is required")
    if not set(cleaned) <= ALLOWED_MCP_SCOPES:
        raise ValueError("Invalid MCP API key scope")
    return cleaned


class UserContext(BaseModel):
    user_id: str
    name: str = ""
    email: str
    org_id: str
    role: str
    clearance_level: int
    groups: list[str]
    can_upload: bool = False
    is_admin: bool = False
    api_scopes: list[str] = Field(default_factory=list)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserContext


class ProfileUpdate(BaseModel):
    """Only the name is editable. extra='forbid' blocks mass-assignment (role, clearance, groups, is_admin...)."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = " ".join(v.split())  # collapse whitespace
        if not v or any(unicodedata.category(c).startswith("C") for c in v):
            raise ValueError("Name contains invalid characters")
        return v


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=50)
    clearance_level: int = Field(ge=0, le=4)
    groups: list[str] = Field(default_factory=list)
    can_upload: bool = False
    is_admin: bool = False
    password: str = Field(min_length=8, max_length=128)


class ApiKeyCreate(BaseModel):
    """Provisions a scoped API key for an MCP agent / service account.
    max_clearance caps what the key can see, independent of the owner's own clearance."""
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=60)
    max_clearance: int = Field(ge=0, le=4, default=2)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_in_days: int = Field(ge=1, le=365, default=90)

    @field_validator("label")
    @classmethod
    def _clean_label(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Label cannot be empty")
        return v

    @field_validator("scopes")
    @classmethod
    def _clean_scopes(cls, v: list[str]) -> list[str]:
        return normalize_mcp_scopes(v)


class ApiKeyUpdate(BaseModel):
    """Updates the allowed MCP tool scopes for an existing API key, without exposing or rotating the secret."""
    model_config = ConfigDict(extra="forbid")
    scopes: list[str]

    @field_validator("scopes")
    @classmethod
    def _clean_scopes(cls, v: list[str]) -> list[str]:
        return normalize_mcp_scopes(v)