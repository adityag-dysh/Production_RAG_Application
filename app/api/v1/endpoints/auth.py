from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from app.api.v1.schemas.auth_schemas import TokenResponse, UserContext
from app.core import security
from app.core.exceptions import AuthError
from app.services import audit_service, mongodb_service as mdb

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    u = await mdb.col("users").find_one({"email": form.username.lower(), "active": True})
    # same error for unknown user / bad password
    if not u or not security.verify_password(form.password, u["password_hash"]):
        raise AuthError("Invalid credentials")
    ctx = UserContext(user_id=u["_id"], email=u["email"], org_id=u["org_id"], role=u["role"],
                      clearance_level=u["clearance_level"], groups=u.get("groups", []),
                      can_upload=u.get("can_upload", False), is_admin=u.get("is_admin", False))
    await audit_service.record("login", ctx)
    return TokenResponse(access_token=security.create_access_token(u["_id"], u["org_id"]), user=ctx)


@router.get("/me", response_model=UserContext)
async def me(user: UserContext = Depends(security.get_current_user)):
    return user
