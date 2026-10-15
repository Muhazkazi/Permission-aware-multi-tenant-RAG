"""Password hashing helpers using Argon2 through pwdlib."""

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a non-empty password without storing or logging its plaintext value."""

    if not password:
        message = "Password must not be empty."
        raise ValueError(message)
    return password_hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Return whether a password matches a stored hash, failing safely if malformed."""

    if not password or not stored_hash:
        return False
    try:
        return password_hasher.verify(password, stored_hash)
    except (TypeError, ValueError, UnknownHashError):
        return False
