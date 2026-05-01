"""
Dynamic email allowlist — JSON file on the data volume.

The OWNER_EMAIL is always allowed (auto-included in code, can't be added or
removed via the API). ALLOWED_EMAILS env var seeds the file on first boot
if it's missing — after that, the file is canonical and env changes are
ignored. To re-seed, delete the file.

Why a file and not a DB: keeps the dependency surface tiny (no SQLite, no
migrations), the data fits in <10 KB, and the file is just JSON — if the
admin UI ever broke, the owner could SSH in and edit it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from . import storage


def _allowlist_path() -> Path:
    return storage.DATA_ROOT / "_allowlist.json"


def _seed_from_env() -> dict:
    raw = os.getenv("ALLOWED_EMAILS", "")
    seeded = [e.strip().lower() for e in raw.split(",") if e.strip()]
    now = datetime.now(timezone.utc).isoformat()
    return {
        "entries": [
            {"email": e, "added_at": now, "added_by": "env(ALLOWED_EMAILS)"}
            for e in seeded
        ],
    }


def _load() -> dict:
    p = _allowlist_path()
    if not p.exists():
        seed = _seed_from_env()
        try:
            storage.write_json(p, seed)
        except Exception:
            return seed
        return seed
    try:
        return storage.read_json(p)
    except Exception:
        # Corrupted file — fall back to env seed but DO NOT overwrite the
        # bad file (owner may want to recover it manually).
        return _seed_from_env()


def get_entries() -> list[dict]:
    """List of {email, added_at, added_by}. Owner is NOT included here —
    it's added in code by `all_allowed_emails`."""
    return _load().get("entries", [])


def all_allowed_emails(owner_email: str) -> set[str]:
    """Union of file entries + owner. This is what auth checks against."""
    emails = {e["email"] for e in get_entries() if e.get("email")}
    if owner_email:
        emails.add(owner_email.lower())
    return emails


def add(email: str, added_by: str) -> dict:
    """Idempotent — re-adding an existing email is a no-op."""
    email = (email or "").strip().lower()
    if not email or "@" not in email or len(email) > 200:
        raise ValueError("invalid email")
    data = _load()
    entries = data.get("entries", [])
    if any(e.get("email") == email for e in entries):
        return data
    entries.append({
        "email": email,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": added_by,
    })
    data["entries"] = entries
    storage.write_json(_allowlist_path(), data)
    return data


def remove(email: str) -> dict:
    email = (email or "").strip().lower()
    data = _load()
    data["entries"] = [e for e in data.get("entries", []) if e.get("email") != email]
    storage.write_json(_allowlist_path(), data)
    return data
