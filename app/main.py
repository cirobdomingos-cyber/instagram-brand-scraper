"""
FastAPI app — entrypoint for Railway.

Routes:
  GET  /                   — homepage SPA
  GET  /api/config         — { google_client_id, mock_mode, allowed_emails_count }
  GET  /api/me             — verify auth, return current user
  GET  /api/brands         — list scraped brands (allowlisted users only)
  POST /api/scrape         — { handle } → scrape via Apify
  GET  /api/brand/{h}      — { manifest, brand_dna_cached }
  POST /api/generate       — { handle, asset_type, audience_or_goal, constraints }
  GET  /data/<handle>/...  — static serving of scraped images / outputs

The owner provides ANTHROPIC_API_KEY + APIFY_API_TOKEN as Railway env vars.
ALLOWED_EMAILS controls who can hit any /api route except /api/config.
"""
from __future__ import annotations

import io
import logging
import os
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anthropic import AsyncAnthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import allowlist, auth, cataloger, generator, scraper, storage


log = logging.getLogger("uvicorn.error")
log.setLevel(logging.INFO)


HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE.parent / "static"

# Default 50 keeps Apify cost predictable (~$0.005/scrape on free tier).
# Owner can raise temporarily by setting MAX_POSTS_PER_SCRAPE in env.
MAX_POSTS_PER_SCRAPE = int(os.getenv("MAX_POSTS_PER_SCRAPE", "50"))


app = FastAPI(title="Instagram Brand Scraper", version="0.1.0")

# SECRET_KEY signs session cookies. If unset, generate a random one — the
# downside is that sessions don't survive a restart (acceptable for portfolio
# scale; production should set this in Railway env).
_SECRET_KEY = os.getenv("SECRET_KEY")
_SECRET_KEY_ENV_SET = bool(_SECRET_KEY)
if not _SECRET_KEY:
    _SECRET_KEY = secrets.token_urlsafe(32)

app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    session_cookie="ibs_session",
    same_site="lax",
    https_only=False,        # Railway terminates TLS, internal hop is HTTP
    max_age=7 * 24 * 3600,   # 7 days
)


@app.on_event("startup")
async def startup():
    storage.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    log.info(f"DATA_ROOT={storage.DATA_ROOT}")
    if str(storage.DATA_ROOT).startswith("/app") and "DATA_ROOT" not in os.environ:
        log.warning(
            "⚠ DATA_ROOT defaults to %s (ephemeral on Railway — data is wiped on redeploy). "
            "Mount a volume and set DATA_ROOT to its mount path.", storage.DATA_ROOT,
        )
    if not _SECRET_KEY_ENV_SET:
        log.warning(
            "⚠ SECRET_KEY not set — generated a random one. "
            "Sessions will be invalidated on every redeploy. "
            "Set SECRET_KEY in Railway env to keep users signed in."
        )
    log.info(f"auth mode = {'real (Google)' if auth.is_real_mode() else 'mock'}")
    log.info(f"allowed emails: {len(auth._allowed_emails())}")


# ── public config endpoint — no auth, used by the SPA bootstrap ──
@app.get("/api/config")
def api_config():
    # An ephemeral data root (defaulted, or anywhere under /app on Railway)
    # means scraped data is wiped on every redeploy. Surface this so the
    # SPA can show a warning banner.
    data_root_str = str(storage.DATA_ROOT)
    data_root_explicit = "DATA_ROOT" in os.environ
    data_root_ephemeral = (
        not data_root_explicit
        or data_root_str.startswith("/app")
        or data_root_str.startswith(str(HERE.parent))
    )
    return {
        "google_client_id": auth.GOOGLE_CLIENT_ID or None,
        "mock_mode": not auth.is_real_mode(),
        "allowed_emails_count": len(auth._allowed_emails()),
        "max_posts": MAX_POSTS_PER_SCRAPE,
        "asset_types": list(generator.prompts.ASSET_FORMATS.keys()),
        "data_root": data_root_str,
        "data_root_ephemeral": data_root_ephemeral,
    }


@app.get("/api/me")
def api_me(user: auth.User = Depends(auth.require_user)):
    return {
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "is_admin": auth.is_admin(user.email),
    }


@app.post("/api/auth/session")
async def api_session_login(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_mock_email: Optional[str] = Header(default=None),
):
    """Verify credentials, write a session cookie, return user info."""
    user = await auth.verify_credentials(authorization, x_mock_email)
    auth.write_session(request, user)
    return {
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "is_admin": auth.is_admin(user.email),
    }


@app.delete("/api/auth/session")
async def api_session_logout(request: Request):
    auth.clear_session(request)
    return {"ok": True}


# ── Admin: allowlist management ──
class AllowlistAddRequest(BaseModel):
    email: str = Field(..., max_length=200)


def _allowlist_payload() -> dict:
    return {
        "owner": auth.OWNER_EMAIL,
        "entries": allowlist.get_entries(),
    }


@app.get("/api/admin/allowlist")
def api_admin_list(user: auth.User = Depends(auth.require_admin)):
    return _allowlist_payload()


@app.post("/api/admin/allowlist")
def api_admin_add(req: AllowlistAddRequest, user: auth.User = Depends(auth.require_admin)):
    try:
        allowlist.add(req.email, added_by=user.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _allowlist_payload()


@app.delete("/api/admin/allowlist/{email}")
def api_admin_delete(email: str, user: auth.User = Depends(auth.require_admin)):
    if email.strip().lower() == auth.OWNER_EMAIL:
        raise HTTPException(status_code=400, detail="cannot remove the owner")
    allowlist.remove(email)
    return _allowlist_payload()


@app.get("/api/brands")
def api_brands(user: auth.User = Depends(auth.require_user)):
    return {"brands": storage.list_brands()}


class ScrapeRequest(BaseModel):
    handle: str = Field(..., min_length=1, max_length=40)
    posts: int = Field(default=50, ge=1, le=200)


@app.post("/api/scrape")
async def api_scrape(req: ScrapeRequest, user: auth.User = Depends(auth.require_user)):
    try:
        handle = storage.normalize_handle(req.handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    posts_n = min(req.posts, MAX_POSTS_PER_SCRAPE)
    apify_token = os.getenv("APIFY_API_TOKEN")
    if not apify_token:
        raise HTTPException(status_code=500, detail="APIFY_API_TOKEN not configured")

    bdir = storage.ensure_brand_dir(handle)
    log.info(f"scrape @{handle} (posts={posts_n}) by {user.email}")

    try:
        profile = await scraper.fetch_profile(apify_token, handle)
    except scraper.ScrapeError as e:
        raise HTTPException(status_code=502, detail=f"profile fetch failed: {e}")
    if not profile:
        raise HTTPException(status_code=404, detail=f"no profile for @{handle} (private/banned/typo?)")
    storage.write_json(bdir / "profile.json", profile)

    try:
        posts = await scraper.fetch_posts(apify_token, handle, posts_n)
    except scraper.ScrapeError as e:
        raise HTTPException(status_code=502, detail=f"posts fetch failed: {e}")
    storage.write_json(bdir / "posts.json", posts)

    ok, total = await scraper.download_images(profile, posts, bdir)

    manifest = scraper.build_manifest(
        handle, profile, posts,
        scraped_at_iso=datetime.now(timezone.utc).isoformat(),
    )
    storage.write_json(bdir / "manifest.json", manifest)

    # Force a re-extract on next generate call — old DNA may be stale now.
    dna_cache = bdir / "brand_dna.json"
    if dna_cache.exists():
        dna_cache.unlink()

    return {
        "handle": handle,
        "posts_fetched": len(posts),
        "images_downloaded": ok,
        "images_total": total,
        "manifest": manifest,
    }


@app.get("/api/brand/{handle}")
def api_brand(handle: str, user: auth.User = Depends(auth.require_user)):
    try:
        handle = storage.normalize_handle(handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bdir = storage.brand_dir(handle)
    if not (bdir / "manifest.json").exists():
        raise HTTPException(status_code=404, detail=f"@{handle} not scraped yet")
    manifest = storage.read_json(bdir / "manifest.json")
    dna_path = bdir / "brand_dna.json"
    catalog_path = bdir / "image_catalog.json"
    catalog_summary = None
    if catalog_path.exists():
        try:
            cat = storage.read_json(catalog_path)
            layouts: dict[str, int] = {}
            quality: dict[str, int] = {}
            for img in cat.get("images", []):
                layouts[img.get("layout", "?")] = layouts.get(img.get("layout", "?"), 0) + 1
                quality[img.get("design_quality", "?")] = quality.get(img.get("design_quality", "?"), 0) + 1
            catalog_summary = {
                "image_count": cat.get("image_count", 0),
                "cataloged_at": cat.get("cataloged_at"),
                "by_layout": layouts,
                "by_quality": quality,
            }
        except Exception:
            catalog_summary = None
    return {
        "manifest": manifest,
        "brand_dna": storage.read_json(dna_path) if dna_path.exists() else None,
        "catalog_summary": catalog_summary,
        "outputs": _list_outputs(bdir),
    }


@app.get("/api/brand/{handle}/download")
def api_download(
    handle: str,
    mode: str = Query("curated", pattern="^(curated|full)$"),
    samples: int = Query(10, ge=3, le=50),
    user: auth.User = Depends(auth.require_user),
):
    """
    Download a brand bundle as a ZIP for handoff to another AI tool.

    mode=curated (default): all JSON files + a curated sample of `samples`
    images selected for design diversity (max 2 per layout, no carousel
    siblings, sorted by quality + usable-for breadth + engagement).
    Falls back to top-by-engagement if the catalog isn't built yet.

    mode=full: all JSON files + every downloaded image. Larger and
    less curated; useful for archival or when you want to pick yourself.

    Generated outputs (asset.md / asset.html / asset.svg) are always
    included.
    """
    try:
        handle = storage.normalize_handle(handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bdir = storage.brand_dir(handle)
    if not (bdir / "manifest.json").exists():
        raise HTTPException(status_code=404, detail=f"@{handle} not scraped yet")

    catalog: Optional[dict] = None
    cat_path = bdir / "image_catalog.json"
    if cat_path.exists():
        try:
            catalog = storage.read_json(cat_path)
        except Exception:
            catalog = None

    # Build the ZIP in memory — single brand, max ~100MB. Streaming would
    # be overkill at this scale and complicate the curation logic.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # JSON metadata + profile pic — always included.
        for name in ("profile.json", "posts.json", "manifest.json",
                     "brand_dna.json", "image_catalog.json"):
            p = bdir / name
            if p.exists():
                zf.write(p, arcname=name)
        pp = bdir / "profile_pic.jpg"
        if pp.exists():
            zf.write(pp, arcname="profile_pic.jpg")

        # BRAND_DNA.md — human-readable rendering of brand_dna.json so
        # the receiving AI gets a primer it can read directly without
        # parsing JSON. Skipped if DNA hasn't been extracted yet.
        dna_path = bdir / "brand_dna.json"
        if dna_path.exists():
            try:
                dna = storage.read_json(dna_path)
                md = generator.dna_to_markdown(dna, handle=handle)
                zf.writestr("BRAND_DNA.md", md)
            except Exception as e:
                log.warning(f"failed to render BRAND_DNA.md for {handle}: {e}")

        # Image selection
        image_paths: list[Path] = []
        if mode == "full":
            img_dir = bdir / "images"
            if img_dir.exists():
                image_paths = sorted(p for p in img_dir.iterdir() if p.is_file())
        else:
            image_paths = _curated_image_paths(bdir, catalog, samples)

        for path in image_paths:
            zf.write(path, arcname=f"images/{path.name}")

        # Generated outputs — always included, full tree under output/
        out_dir = bdir / "output"
        if out_dir.exists():
            for f in out_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(bdir)
                    zf.write(f, arcname=str(rel).replace("\\", "/"))

        # Tiny manifest of what's in the bundle so the recipient AI knows
        # the layout without having to spelunk.
        bundle_meta = {
            "handle": handle,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "samples": len(image_paths) if mode == "curated" else None,
            "image_count_in_zip": len(image_paths),
            "has_catalog": catalog is not None,
            "has_brand_dna": (bdir / "brand_dna.json").exists(),
        }
        zf.writestr("BUNDLE_META.json",
                    storage.json_dumps_pretty(bundle_meta) if hasattr(storage, "json_dumps_pretty")
                    else __import__("json").dumps(bundle_meta, indent=2))

    buf.seek(0)
    log.info(f"download {mode} for @{handle} by {user.email}: "
             f"{len(image_paths)} images, {buf.getbuffer().nbytes} bytes")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{handle}_{mode}_{ts}.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _curated_image_paths(bdir: Path, catalog: Optional[dict], n: int) -> list[Path]:
    """Pick `n` representative images for the curated bundle."""
    if catalog and catalog.get("images"):
        picks = cataloger.pick_curated_images(catalog, n=n)
        out: list[Path] = []
        for img in picks:
            p = bdir / img["image_path"]
            if p.exists():
                out.append(p)
        return out
    # Fallback: top-N by likes from posts.json + image existence.
    posts_path = bdir / "posts.json"
    if not posts_path.exists():
        return []
    try:
        posts = storage.read_json(posts_path)
    except Exception:
        return []
    posts_sorted = sorted(posts, key=lambda p: p.get("likesCount") or 0, reverse=True)
    img_dir = bdir / "images"
    out = []
    for p in posts_sorted:
        sc = p.get("shortCode") or p.get("shortcode") or p.get("id") or ""
        if not sc:
            continue
        path = img_dir / f"{sc}.jpg"
        if path.exists():
            out.append(path)
        if len(out) >= n:
            break
    return out


@app.post("/api/brand/{handle}/catalog")
async def api_build_catalog(handle: str, user: auth.User = Depends(auth.require_user)):
    """Force a fresh image catalog (re-runs Haiku on every image)."""
    try:
        handle = storage.normalize_handle(handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bdir = storage.brand_dir(handle)
    if not (bdir / "posts.json").exists():
        raise HTTPException(status_code=404, detail=f"@{handle} not scraped yet")

    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip().strip('"').strip("'")
    if not api_key or not api_key.startswith("sk-ant-"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured or malformed")
    client = AsyncAnthropic(api_key=api_key)
    log.info(f"force-catalog @{handle} by {user.email}")
    try:
        cat = await cataloger.build_catalog(client, handle, force=True)
    except Exception as e:
        log.exception("catalog failed")
        raise HTTPException(status_code=500, detail=f"catalog failed: {e}")
    return {"image_count": cat["image_count"], "cataloged_at": cat["cataloged_at"]}


def _list_outputs(bdir: Path) -> list[dict]:
    out_root = bdir / "output"
    if not out_root.exists():
        return []
    out: list[dict] = []
    for d in sorted(out_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if meta_path.exists():
            try:
                m = storage.read_json(meta_path)
            except Exception:
                m = {}
        else:
            m = {}
        out.append({
            "dir": storage.output_relpath(d),
            "asset_type": m.get("asset_type"),
            "created_at": d.name.split("_")[-1] if "_" in d.name else None,
            "has_html": (d / "asset.html").exists(),
            "has_svg": (d / "asset.svg").exists(),
        })
    return out


class GenerateRequest(BaseModel):
    handle: str
    asset_type: str
    audience_or_goal: str = ""
    constraints: str = ""


@app.post("/api/generate")
async def api_generate(req: GenerateRequest, user: auth.User = Depends(auth.require_user)):
    try:
        handle = storage.normalize_handle(req.handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bdir = storage.brand_dir(handle)
    if not (bdir / "manifest.json").exists():
        raise HTTPException(status_code=404, detail=f"@{handle} not scraped yet")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    client = AsyncAnthropic(api_key=api_key)
    log.info(f"generate {req.asset_type} for @{handle} by {user.email}")
    try:
        result = await generator.generate_asset(
            client, handle, req.asset_type,
            req.audience_or_goal, req.constraints,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("generation failed")
        raise HTTPException(status_code=500, detail=f"generation failed: {e}")
    return result


@app.get("/api/brand/{handle}/dna")
async def api_extract_dna(handle: str, user: auth.User = Depends(auth.require_user)):
    """Force a fresh BRAND_DNA extraction (called explicitly from the UI)."""
    try:
        handle = storage.normalize_handle(handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip().strip('"').strip("'")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    if not api_key.startswith("sk-ant-"):
        raise HTTPException(
            status_code=500,
            detail=f"ANTHROPIC_API_KEY looks malformed (starts with {api_key[:7]!r}, expected 'sk-ant-')",
        )
    client = AsyncAnthropic(api_key=api_key)
    log.info(f"extract DNA @{handle} by {user.email}")
    try:
        dna = await generator.extract_brand_dna(client, handle)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.exception("DNA extraction failed")
        raise HTTPException(status_code=500, detail=f"DNA extraction failed: {e}")
    return dna


@app.get("/api/brand/{handle}/files")
def api_brand_files(handle: str, user: auth.User = Depends(auth.require_user)):
    """Diagnostic — what's actually on disk for this brand."""
    try:
        handle = storage.normalize_handle(handle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bdir = storage.brand_dir(handle)
    if not bdir.exists():
        return {
            "data_root": str(storage.DATA_ROOT),
            "brand_dir": str(bdir),
            "exists": False,
            "files": [],
        }
    files = []
    for f in sorted(bdir.rglob("*")):
        if f.is_file():
            files.append({
                "path": str(f.relative_to(bdir)).replace("\\", "/"),
                "size": f.stat().st_size,
            })
    return {
        "data_root": str(storage.DATA_ROOT),
        "brand_dir": str(bdir),
        "exists": True,
        "files": files,
    }


# ── Static file serving ──
# /data/<handle>/... — auth-gated, served via the route below
# /static/...        — public frontend assets (JS/CSS/HTML)
# /                  — SPA index
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")


@app.get("/data/{path:path}")
def serve_data(path: str, user: auth.User = Depends(auth.require_user)):
    """
    Auth-gated file server for everything under DATA_ROOT — scraped images,
    posts.json, manifests, generated assets. Browser sends the session
    cookie automatically on <img> requests, so the UI just works.

    Path-traversal-safe: resolves the joined path and refuses anything
    outside DATA_ROOT.
    """
    full = (storage.DATA_ROOT / path).resolve()
    root = storage.DATA_ROOT.resolve()
    try:
        full.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="path traversal blocked")
    if not full.is_file():
        raise HTTPException(status_code=404, detail=f"not found: {path}")
    return FileResponse(full)


@app.get("/")
def root():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return {"error": "static/index.html missing"}
    return FileResponse(index)


@app.get("/health")
def health():
    return {"ok": True}
