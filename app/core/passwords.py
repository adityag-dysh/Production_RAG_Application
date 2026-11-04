"""Password hashing (bcrypt 4.0.1). Pure module so it can be unit tested without the database.
bcrypt reads at most 72 BYTES. We cut at 70 BYTES ourselves so behaviour is identical on every bcrypt
version (newer versions raise on longer input). Bytes beyond 70 are ignored for hashing AND checking."""
import bcrypt

BCRYPT_MAX_BYTES = 70


def _prep(pw: str) -> bytes:
    return pw.encode("utf-8")[:BCRYPT_MAX_BYTES]


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(_prep(pw), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(pw), hashed.encode())
    except ValueError:
        return False