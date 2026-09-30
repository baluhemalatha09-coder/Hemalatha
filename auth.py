"""Password hashing + JWT helpers and the current-user dependency."""
import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Request

import database

ALGORITHM = "HS256"


def secret_key() -> str:
    key = os.getenv("SECRET_KEY", "").strip()
    if not key or key.startswith("change-me"):
        warnings.warn("SECRET_KEY is not set - using an insecure development key.", stacklevel=2)
        return "insecure-dev-key-please-set-SECRET_KEY-in-env-file"
    return key


def token_lifetime_minutes() -> int:
    try:
        return int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    except ValueError:
        return 60


class NotAuthenticated(Exception):
    """Raised when a protected route is hit without a valid token."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=token_lifetime_minutes())
    return jwt.encode({"sub": str(user_id), "exp": expire}, secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, secret_key(), algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    user = database.get_user_by_username(username.strip())
    if user and verify_password(password, user["password_hash"]):
        return user
    return None


def _token_from_request(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("access_token")


def get_optional_user(request: Request) -> Optional[dict]:
    token = _token_from_request(request)
    if not token:
        return None
    user_id = decode_token(token)
    return database.get_user_by_id(user_id) if user_id else None


def get_current_user(request: Request) -> dict:
    user = get_optional_user(request)
    if not user:
        raise NotAuthenticated()
    return user
