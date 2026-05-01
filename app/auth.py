"""
Auth: Google ID token verification + email allowlist.

Two modes, mirroring reroot's pattern:
  REAL  — GOOGLE_CLIENT_ID is set. Frontend uses GSI, sends ID token in
          Authorization header. Backend verifies signature against Google's
          public keys, then checks email against ALLOWED_EMAILS.
  MOCK  — GOOGLE_CLIENT_ID unset. Backend trusts X-Mock-Email header iff
          that email is in ALLOWED_EMAILS. Frictionless local dev; fails
          closed in prod (mock mode is gated by GOOGLE_CLIENT_ID being
          unset, so misconfiguring prod is the only way to enable it).

ALLOWED_EMAILS is a comma-separated env var. Empty/unset = nobody allowed.
The owner email is auto-included so they can't lock themselves out.

Usage in FastAPI routes:
    @app.post("/scrape")
    async def scrape(user: User = Depends(require_user)):
        ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token as g_id_token

from . import allowlist


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "ciro.b.domingos@gmail.com").strip().lower()


def _allowed_emails() -> set[str]:
    """Live read — the file is the source of truth, env is only the seed."""
    return allowlist.all_allowed_emails(OWNER_EMAIL)


def is_admin(email: str) -> bool:
    return email.strip().lower() == OWNER_EMAIL


def is_real_mode() -> bool:
    return bool(GOOGLE_CLIENT_ID)


@dataclass(frozen=True)
class User:
    email: str
    name: str
    picture: str


# Cached request adapter — google-auth reuses the underlying HTTP session.
_g_request = g_requests.Request()


def _verify_google_token(token: str) -> dict:
    """Returns the verified token payload, or raises ValueError."""
    return g_id_token.verify_oauth2_token(token, _g_request, GOOGLE_CLIENT_ID)


async def require_user(
    authorization: Optional[str] = Header(default=None),
    x_mock_email: Optional[str] = Header(default=None),
) -> User:
    """FastAPI dependency. Returns User on success, raises 401/403 otherwise."""
    allowed = _allowed_emails()

    if is_real_mode():
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
            )
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = _verify_google_token(token)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid Google ID token: {e}",
            )
        email = (payload.get("email") or "").lower()
        if not email or not payload.get("email_verified", False):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="email not verified by Google",
            )
        if email not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{email} is not on the allowlist",
            )
        return User(
            email=email,
            name=payload.get("name") or email,
            picture=payload.get("picture") or "",
        )

    # ── MOCK MODE ──
    # Require the header explicitly. No silent fallback to owner — a prod
    # deploy that forgets GOOGLE_CLIENT_ID would otherwise be wide open.
    email = (x_mock_email or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Mock-Email header (mock mode)",
        )
    if email not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{email} is not on the allowlist",
        )
    return User(email=email, name=email.split("@")[0], picture="")


async def require_admin(user: User = Depends(require_user)) -> User:
    """Owner-only routes."""
    if not is_admin(user.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin only",
        )
    return user
