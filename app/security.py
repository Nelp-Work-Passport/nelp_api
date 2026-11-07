"""PIN hashing, tokens, one-time codes and phone handling."""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings
from app.db import utcnow

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("0000")
_PHONE_RE = re.compile(r"^(?:\+?254|0)([17]\d{8})$")


def hash_pin(pin: str) -> str:
    return _hasher.hash(pin)


def verify_pin(pin_hash: str | None, pin: str) -> bool:
    """Hashes even for unknown users, so timing does not reveal who is registered."""
    try:
        matched = _hasher.verify(pin_hash or _DUMMY_HASH, pin)
    except (VerificationError, InvalidHashError):
        return False
    return matched and pin_hash is not None


def create_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(purpose: str, subject_id: uuid.UUID, code: str) -> str:
    """Keyed, because a plain hash of a 6-digit code is reversible by brute force."""
    message = f"{purpose}:{subject_id}:{code}".encode()
    return hmac.new(get_settings().secret_key.encode(), message, hashlib.sha256).hexdigest()


def code_matches(stored_hash: str, purpose: str, subject_id: uuid.UUID, code: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_code(purpose, subject_id, code))


def normalize_phone(raw: str) -> str:
    """Accept 07.., 01.., 2547.., +2547..; return E.164. Raises ValueError otherwise."""
    cleaned = re.sub(r"[\s\-()]", "", raw)
    match = _PHONE_RE.match(cleaned)
    if not match:
        raise ValueError("Enter a valid Kenyan phone number, such as 0712345678")
    return f"+254{match.group(1)}"


def mask_phone(phone: str) -> str:
    return f"{phone[:5]}*****{phone[-3:]}"
