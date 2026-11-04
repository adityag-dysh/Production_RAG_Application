"""Auth. Production: replace /auth/login with an OIDC provider and verify its JWTs here.
The JWT carries identity + a token version. Clearance/groups are loaded from Mongo on every request, and
changing a password bumps token_version, which instantly invalidates every older token (other devices)."""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.api.v1.schemas.auth_schemas import UserContext
from app.core.config import settings
from app.core.exceptions import AuthError, Forbidden
from app.core.passwords import hash_password, verify_password  # noqa: F401  (re-exported for seed/auth)
from app.services import mongodb_service

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def user_context(u: dict) -> UserContext:
    return UserContext(
        user_id=u["_id"], name=u.get("name", ""), email=u["email"], org_id=u["org_id"], role=u["role"],
        clearance_level=u["clearance_level"], groups=u.get("groups", []),
        can_upload=u.get("can_upload", False), is_admin=u.get("is_admin", False))


def create_access_token(user_id: str, org_id: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "org_id": org_id, "ver": token_version, "iat": now,
               "exp": now + timedelta(minutes=settings.access_token_ttl_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_user(token: str = Depends(oauth2)) -> UserContext:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise AuthError("Invalid or expired token")
    u = await mongodb_service.col("users").find_one(
        {"_id": claims.get("sub"), "org_id": claims.get("org_id"), "active": True})
    if not u:
        raise AuthError("User not found or disabled")
    if claims.get("ver", 0) != u.get("token_version", 0):
        raise AuthError("Session expired. Please sign in again.")
    return user_context(u)


async def require_uploader(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not (user.can_upload or user.is_admin):
        raise Forbidden("You are not allowed to upload documents")
    return user


async def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not user.is_admin:
        raise Forbidden("Admin only")
    return user


async def require_hr_or_ceo(user: UserContext = Depends(get_current_user)) -> UserContext:
    if user.role not in ("ceo", "hr") and not user.is_admin:
        raise Forbidden("Only HR and CEO can perform this action")
    return user