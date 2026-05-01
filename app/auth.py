"""
Auth: Google ID token verification + email allowlist + session cookies.

Cookie-first, header-fallback. The browser holds a signed session cookie
that's auto-sent on every request (including <img src="/data/...">), so
images and other static brand data can be auth-gated without the SPA
having to wrap fetch.

Modes:
  REAL  — GOOGLE_CLIENT_ID set. POST /api/auth/session with a Google ID
          token; backend verifies signature against Google's public keys,
          checks the email against ALLOWED_EMAILS, then writes a session.
  MOCK  — GOOGLE_CLIENT_ID unset. POST /api/auth/session with X-Mock-Email
          header; backend trusts it iff allowlisted. Mock mode requires
          the header explicitly — no silent fallback to owner — so a prod
          deploy that forgets GOOGLE_CLIENT_ID returns 401 instead of
          being open.

ALLOWED_EMAILS env var seeds the allowlist on first boot. After that the
allowlist file is canonical (managed by app.allowlist). OWNER_EMAIL is
always allowed in code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token as g_id_token

from . import allowlist


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "ciro.b.domingos@gmail.com").strip().lower()


def _allowed_emails() -> set[str]:
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


_g_request = g_requests.Request()


def _verify_google_token(token: str) -> dict:
    return g_id_token.verify_oauth2_token(token, _g_request, GOOGLE_CLIENT_ID)


async def verify_credentials(
    authorization: Optional[str], x_mock_email: Optional[str]
) -> User:
    """
    Validate the supplied credentials. Used both directly by /api/auth/session
    (to start a session) and as the header-fallback path inside require_user.
    Raises HTTPException on auth failure.
    """
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


def _user_from_session(session: dict) -> Optional[User]:
    """Re-hydrate User from session data. Returns None if email isn't allowlisted."""
    email = (session.get("email") or "").strip().lower()
    if not email or email not in _allowed_emails():
        return None
    return User(
        email=email,
        name=session.get("name") or email,
        picture=session.get("picture") or "",
    )


def write_session(request: Request, user: User) -> None:
    request.session["email"] = user.email
    request.session["name"] = user.name
    request.session["picture"] = user.picture


def clear_session(request: Request) -> None:
    request.session.clear()


async def require_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_mock_email: Optional[str] = Header(default=None),
) -> User:
    """
    Auth dependency. Cookie first (cheap, no JWT verify). If no cookie,
    fall back to verifying headers — so curl/scripts still work — and
    refresh the session so subsequent same-origin requests (including
    <img src="/data/...">) ride the cookie.
    """
    user = _user_from_session(request.session)
    if user:
        return user
    # Stale or missing session — try header path.
    if request.session:
        request.session.clear()
    user = await verify_credentials(authorization, x_mock_email)
    write_session(request, user)
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if not is_admin(user.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin only",
        )
    return user
