import pytest


def test_password_hasher_never_returns_the_plaintext_and_rejects_wrong_password():
    from data_analysis_agent.services.auth import PasswordHasher

    encoded = PasswordHasher.hash("correct horse battery staple")

    assert encoded != "correct horse battery staple"
    assert encoded.startswith("$argon2id$")
    assert PasswordHasher.verify("correct horse battery staple", encoded) is True
    assert PasswordHasher.verify("wrong password", encoded) is False


def test_password_hasher_rejects_malformed_hashes_without_raising():
    from data_analysis_agent.services.auth import PasswordHasher

    assert PasswordHasher.verify("correct horse battery staple", "not-a-hash") is False

