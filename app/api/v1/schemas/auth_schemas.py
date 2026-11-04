import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


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