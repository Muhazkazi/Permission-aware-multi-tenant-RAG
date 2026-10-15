"""Tests for password hashing safeguards."""

from app.core.passwords import hash_password, verify_password


def test_password_hash_verifies_only_the_correct_password() -> None:
    """A stored hash must verify its original password and reject another password."""

    stored_hash = hash_password("correct horse battery staple")

    assert stored_hash != "correct horse battery staple"
    assert verify_password("correct horse battery staple", stored_hash)
    assert not verify_password("incorrect password", stored_hash)


def test_malformed_password_hash_fails_safely() -> None:
    """A malformed stored hash must never authenticate a password or crash a request."""

    assert not verify_password("correct password", "not-a-valid-password-hash")
