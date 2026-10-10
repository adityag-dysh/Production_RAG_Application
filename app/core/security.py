"""Auth. Production: replace /auth/login with an OIDC provider (Keycloak/Auth0) and verify its JWTs here.
Design choice: the JWT carries ONLY identity (sub, org_id). Clearance/groups are loaded from Mongo on every
request, so revocation and role changes take effect immediately.
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.api.v1.schemas.auth_schemas import UserContext
from app.core.config import settings
from app.core.exceptions import AuthError, Forbidden
from app.services import mongodb_service

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: str, org_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "org_id": org_id, "iat": now,
               "exp": now + timedelta(minutes=settings.access_token_ttl_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_user(token: str = Depends(oauth2)) -> UserContext:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise AuthError("Invalid or expired token")
    u = await mongodb_service.col("users").find_one(
        {"_id": claims["sub"], "org_id": claims["org_id"], "active": True})
    if not u:
        raise AuthError("User not found or disabled")
    return UserContext(
        user_id=u["_id"], email=u["email"], org_id=u["org_id"], role=u["role"],
        clearance_level=u["clearance_level"], groups=u.get("groups", []),
        can_upload=u.get("can_upload", False), is_admin=u.get("is_admin", False))


async def require_uploader(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not (user.can_upload or user.is_admin):
        raise Forbidden("You are not allowed to upload documents")
    return user


async def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not user.is_admin:
        raise Forbidden("Admin only")
    return user
