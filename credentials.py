"""
Symmetric encryption for at-rest credentials (SMTP/IMAP passwords).

Set CREDENTIALS_KEY to a urlsafe base64 Fernet key (32 bytes). Generate one
with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Values are stored with a `g:` prefix so plaintext legacy/env values can still
be read without breaking existing installs. Anything written through
`encrypt_secret` is always encrypted; without CREDENTIALS_KEY we refuse to
store secrets rather than silently writing them in plaintext.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "g:"


def _fernet():
    key = (os.getenv("CREDENTIALS_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "CREDENTIALS_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add it to .env / "
            "the Render environment."
        )
    try:
        return Fernet(key.encode())
    except Exception as e:
        raise RuntimeError(f"CREDENTIALS_KEY is invalid: {e}") from e


def encrypt_secret(value):
    if value is None or value == "":
        return None
    return PREFIX + _fernet().encrypt(str(value).encode()).decode()


def decrypt_secret(value):
    if not value:
        return ""
    if not str(value).startswith(PREFIX):
        return str(value)  # legacy/plaintext or env-provided
    try:
        return _fernet().decrypt(str(value)[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise RuntimeError(
            "Could not decrypt stored credentials — CREDENTIALS_KEY changed?"
        ) from e
