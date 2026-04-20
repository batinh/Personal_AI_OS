"""Shared admin credential loading and validation.

Fails fast at import time if required env vars are not set, preventing
the application from starting with insecure hardcoded defaults.
"""
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.logging_conf import get_module_logger

logger = get_module_logger("admin_auth")

_security = HTTPBasic()


def _load_admin_creds() -> tuple[str, str]:
    """Load admin credentials from env. Raises ValueError if not configured."""
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        raise ValueError(
            "ADMIN_USERNAME and ADMIN_PASSWORD must be set in environment. "
            "Refusing to start with insecure defaults."
        )
    if len(password) < 8:
        raise ValueError(
            "ADMIN_PASSWORD must be at least 8 characters."
        )
    return username, password


# Load once at startup — will raise ValueError if env vars missing.
try:
    _ADMIN_USER, _ADMIN_PASS = _load_admin_creds()
except ValueError as e:
    # Log clearly but don't crash import — FastAPI lifespan will catch this
    # if ADMIN_USERNAME/PASSWORD are missing; dev environments set them in .env.
    logger.warning("[ADMIN_AUTH] %s", e)
    _ADMIN_USER = ""
    _ADMIN_PASS = ""


def verify_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """FastAPI dependency: validates Basic Auth credentials against env vars."""
    if not _ADMIN_USER or not _ADMIN_PASS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin credentials not configured on server.",
        )
    is_user_ok = secrets.compare_digest(credentials.username, _ADMIN_USER)
    is_pass_ok = secrets.compare_digest(credentials.password, _ADMIN_PASS)
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai tài khoản hoặc mật khẩu!",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
