"""Encrypted credential storage for sensitive third-party credentials (Garmin, etc.)."""

import json
from pathlib import Path

from cryptography.fernet import Fernet

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_SECRETS_FILE = _BASE_DIR / "data" / "garmin_secrets.json"
_KEY_FILE = _BASE_DIR / "data" / "encryption.key"


def _get_or_create_key() -> bytes:
    """Return the persistent Fernet key, generating and saving it on first use.

    Key lives in data/encryption.key which is bind-mounted from the host, so it
    survives container rebuilds. A hostname-derived key breaks on every docker rebuild.
    """
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_bytes(key)
    return key


def _fernet() -> Fernet:
    return Fernet(_get_or_create_key())


def encrypt_garmin_credentials(email: str, password: str) -> None:
    """Encrypt and store Garmin credentials in data/garmin_secrets.json."""
    f = _fernet()
    _SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "email": email,  # email stored plaintext (not sensitive)
        "password_encrypted": f.encrypt(password.encode()).decode(),
    }
    _SECRETS_FILE.write_text(json.dumps(data))


def decrypt_garmin_credentials() -> tuple[str, str] | None:
    """Return (email, password) or None if not stored."""
    if not _SECRETS_FILE.exists():
        return None
    try:
        data = json.loads(_SECRETS_FILE.read_text())
        f = _fernet()
        password = f.decrypt(data["password_encrypted"].encode()).decode()
        return data["email"], password
    except Exception:
        return None


def delete_garmin_credentials() -> None:
    """Remove stored credentials (used on re-auth)."""
    if _SECRETS_FILE.exists():
        _SECRETS_FILE.unlink()


def has_garmin_credentials() -> bool:
    """True if credentials file exists and is readable."""
    return decrypt_garmin_credentials() is not None
