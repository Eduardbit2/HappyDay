"""Пароли и случайные токены; открытые значения не журналируются."""

import hashlib
import re
import secrets
import unicodedata
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_login(login: str) -> str:
    return unicodedata.normalize("NFKC", login).strip().casefold()


def validate_identity(name: str, login: str) -> tuple[str, str]:
    name = unicodedata.normalize("NFC", " ".join(name.split()))
    login = normalize_login(login)
    if not 1 <= len(name) <= 200:
        raise ValueError("Имя должно содержать от 1 до 200 символов")
    if not re.fullmatch(r"[\w.-]{3,40}", login):
        raise ValueError("Логин: 3–40 букв, цифр, точек, дефисов или подчеркиваний")
    return name, login


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Пароль должен содержать от 12 до 128 символов")
    return hasher.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    if len(password) > 128:
        return False
    try:
        return hasher.verify(encoded or DUMMY_HASH, password)
    except (InvalidHashError, VerificationError):
        return False
