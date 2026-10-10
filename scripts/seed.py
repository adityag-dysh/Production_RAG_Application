"""python -m scripts.seed [password]  -- creates demo orgs/users. Use an IdP in production."""
import asyncio
import sys

from app.core.security import hash_password
from app.services import mongodb_service as mdb

PW = sys.argv[1] if len(sys.argv) > 1 else "ChangeMe123!"
USERS = [
    ("james@x.com", "org_X", "engineer", 2, ["engineering", "devops"], True, False),
    ("ceo@x.com", "org_X", "ceo", 4, ["executive", "engineering", "hr"], True, True),
    ("hr@x.com", "org_X", "hr", 3, ["hr"], True, False),
    ("intern@x.com", "org_X", "intern", 0, [], False, False),
    ("alice@y.com", "org_Y", "engineer", 2, ["engineering"], True, True),
]


async def main():
    await mdb.ensure_indexes()
    for email, org, role, lvl, groups, up, admin in USERS:
        await mdb.col("users").update_one({"_id": email}, {"$set": {
            "email": email, "org_id": org, "role": role, "clearance_level": lvl, "groups": groups,
            "can_upload": up, "is_admin": admin, "active": True, "password_hash": hash_password(PW)}}, upsert=True)
        print("seeded", email, org, role, lvl)
    print(f"password: {PW}  (change it)")

asyncio.run(main())
