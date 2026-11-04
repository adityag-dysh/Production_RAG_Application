import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from pymongo import ReturnDocument

from app.api.v1.schemas.auth_schemas import PasswordChange, ProfileUpdate, TokenResponse, UserContext, UserCreateRequest
from app.core import security
from app.core.config import settings
from app.core.exceptions import AppError, AuthError, Forbidden
from app.core.passwords import hash_password, verify_password
from app.core.rate_limit import limit, login_limit
from app.services import audit_service, mongodb_service as mdb

router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_HASH = hash_password("timing-equalisation-dummy")  # unknown email costs the same time as a wrong password


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_limit)])
async def login(form: OAuth2PasswordRequestForm = Depends()):
    u = await mdb.col("users").find_one({"email": form.username.strip().lower(), "active": True})
    ok = await asyncio.to_thread(verify_password, form.password, u["password_hash"] if u else _DUMMY_HASH)
    if not u or not ok:
        raise AuthError("Invalid credentials")
    ctx = security.user_context(u)
    await audit_service.record("login", ctx)
    return TokenResponse(
        access_token=security.create_access_token(u["_id"], u["org_id"], u.get("token_version", 0)), user=ctx)


@router.get("/me", response_model=UserContext)
async def me(user: UserContext = Depends(security.get_current_user)):
    return user


@router.patch("/me", response_model=UserContext, dependencies=[Depends(limit("profile", 10))])
async def update_profile(body: ProfileUpdate, user: UserContext = Depends(security.get_current_user)):
    await mdb.col("users").update_one({"_id": user.user_id, "org_id": user.org_id}, {"$set": {"name": body.name}})
    await audit_service.record("profile_update", user, field="name")
    return user.model_copy(update={"name": body.name})


@router.post("/change-password",
             dependencies=[Depends(limit("password", settings.rate_limit_password_per_min))])
async def change_password(body: PasswordChange, user: UserContext = Depends(security.get_current_user)):
    u = await mdb.col("users").find_one({"_id": user.user_id, "org_id": user.org_id})
    if not u or not await asyncio.to_thread(verify_password, body.current_password, u["password_hash"]):
        raise AppError("Current password is incorrect")  # 400, not 401: the session itself is valid
    if await asyncio.to_thread(verify_password, body.new_password, u["password_hash"]):
        raise AppError("New password must be different from the current one")
    new_hash = await asyncio.to_thread(hash_password, body.new_password)
    updated = await mdb.col("users").find_one_and_update(
        {"_id": user.user_id, "org_id": user.org_id},
        {"$set": {"password_hash": new_hash, "password_changed_at": datetime.now(timezone.utc)},
         "$inc": {"token_version": 1}},          # invalidates every older token (other devices/tabs)
        return_document=ReturnDocument.AFTER)
    await audit_service.record("password_change", user)  # never log the password itself
    return {"access_token": security.create_access_token(updated["_id"], updated["org_id"],
                                                         updated["token_version"]), "token_type": "bearer"}


@router.post("/users", status_code=201)
async def create_user(body: UserCreateRequest, user: UserContext = Depends(security.require_hr_or_ceo)):
    email = body.email.strip().lower()
    existing = await mdb.col("users").find_one({"_id": email})
    if existing:
        raise AppError("User with this email already exists")
    
    user_doc = {
        "_id": email,
        "email": email,
        "name": body.name,
        "org_id": user.org_id,  # restricted to admin's organization
        "role": body.role,
        "clearance_level": body.clearance_level,
        "groups": body.groups,
        "can_upload": body.can_upload,
        "is_admin": body.is_admin,
        "active": True,
        "token_version": 0,
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc)
    }
    await mdb.col("users").insert_one(user_doc)
    await audit_service.record("user_create", user, created_email=email, role=body.role)
    return {"message": "User created successfully", "email": email}


@router.delete("/users/{email}", status_code=204)
async def delete_user(email: str, user: UserContext = Depends(security.require_hr_or_ceo)):
    email = email.strip().lower()
    if email == user.email:
        raise AppError("Cannot delete your own account")
    
    target = await mdb.col("users").find_one({"_id": email, "org_id": user.org_id})
    if not target:
        raise AppError("User not found")
    
    await mdb.col("users").update_one({"_id": email}, {"$set": {"active": False}, "$inc": {"token_version": 1}})
    await audit_service.record("user_delete", user, deleted_email=email)
    return None