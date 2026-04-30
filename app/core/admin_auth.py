"""Shared admin credential loading and validation."""

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.logging_conf import get_module_logger

logger = get_module_logger("admin_auth")

_security = HTTPBasic()


def _get_admin_creds() -> tuple[str, str]:
    """Read admin credentials from env on each call (lazy — no import-time caching)."""
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    return username, password


def verify_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """FastAPI dependency: validates Basic Auth credentials against env vars."""
    admin_user, admin_pass = _get_admin_creds()

    if not admin_user or not admin_pass:
        logger.warning(
            "[ADMIN_AUTH] ADMIN_USERNAME and ADMIN_PASSWORD must be set in environment."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin credentials not configured on server.",
        )
    if len(admin_pass) < 8:
        logger.warning("[ADMIN_AUTH] ADMIN_PASSWORD must be at least 8 characters.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin credentials not configured on server.",
        )

    is_user_ok = secrets.compare_digest(credentials.username, admin_user)
    is_pass_ok = secrets.compare_digest(credentials.password, admin_pass)
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai tài khoản hoặc mật khẩu!",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
