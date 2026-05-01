"""
Disk layout helpers. One canonical place to compute paths so generator,
scraper, and FastAPI routes don't drift.

Layout under DATA_ROOT:

    <handle>/
    ├── profile.json
    ├── posts.json
    ├── manifest.json
    ├── profile_pic.jpg
    ├── brand_dna.json          # cached after first generation call
    ├── images/<shortcode>.jpg
    └── output/<asset>_<ts>/    # one folder per generated asset
        ├── asset.html | asset.md | asset.svg
        └── meta.json
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Railway mounts the persistent volume at /data by default. Falls back to
# ./data for local dev. Override via DATA_ROOT env var.
DATA_ROOT = Path(os.getenv("DATA_ROOT", "./data")).resolve()


_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_handle(raw: str) -> str:
    """Strip @, lowercase, validate against Instagram's character set."""
    h = (raw or "").strip().lstrip("@").lower()
    if not _HANDLE_RE.match(h):
        raise ValueError(
            "invalid handle — letters, numbers, periods, and underscores only, max 30 chars"
        )
    return h


def brand_dir(handle: str) -> Path:
    return DATA_ROOT / handle


def ensure_brand_dir(handle: str) -> Path:
    d = brand_dir(handle)
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, payload) -> None:
    """Atomic JSON write — temp file + rename, so a crash mid-write doesn't corrupt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def list_brands() -> list[dict]:
    """Used by the homepage to show what's already scraped."""
    if not DATA_ROOT.exists():
        return []
    out: list[dict] = []
    for entry in sorted(DATA_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = read_json(manifest_path)
            out.append({
                "handle": m.get("handle", entry.name),
                "fullName": (m.get("profile_summary") or {}).get("fullName"),
                "followers": (m.get("profile_summary") or {}).get("followersCount"),
                "post_count": m.get("post_count", 0),
                "scraped_at": m.get("scraped_at"),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x.get("scraped_at") or "", reverse=True)
    return out


def new_output_dir(handle: str, asset_type: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = brand_dir(handle) / "output" / f"{asset_type}_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_relpath(p: Path) -> str:
    """Path relative to DATA_ROOT, with forward slashes — for URLs."""
    return str(p.relative_to(DATA_ROOT)).replace("\\", "/")
