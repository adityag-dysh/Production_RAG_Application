from pydantic import BaseModel


class UserContext(BaseModel):
    user_id: str
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
