"""
Per-image catalog using Claude Haiku. One pass per brand, cached on disk.

For each downloaded image, ask Haiku for structured JSON: subject, palette,
layout, mood, on-screen text, design quality, which asset types it'd work
for. The catalog becomes a searchable index that DNA + asset prompts can
reference by shortcode without attaching every image as base64.

Cost: ~$0.001/image with Haiku 4.5. A 75-image brand catalogs for ~$0.08
in ~20s with 8-way concurrency.

Why catalog at all:
  - DNA extraction sees more visual signal than 6 attached images would
    allow — Claude can see "67 of 75 images are flat-lay product shots"
    instead of inferring it from a tiny sample.
  - Asset generation can cite specific shortcodes ("the warm flat-lay
    from Cabc123") and we attach a smart subset based on asset type.
  - The catalog is one-time per brand, paid back across every asset.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anthropic import AsyncAnthropic

from . import prompts, storage


log = logging.getLogger(__name__)

CATALOG_MODEL = "claude-haiku-4-5-20251001"
CATALOG_CONCURRENCY = 8
_MAX_BYTES = 5 * 1024 * 1024


def _b64(path: Path) -> Optional[tuple[str, str]]:
    if not path.exists() or path.stat().st_size == 0 or path.stat().st_size > _MAX_BYTES:
        return None
    try:
        data = path.read_bytes()
    except Exception:
        return None
    suffix = path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    return base64.b64encode(data).decode("ascii"), media_type


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


async def _catalog_one(
    client: AsyncAnthropic,
    image_path: Path,
    sem: asyncio.Semaphore,
) -> Optional[dict]:
    img = _b64(image_path)
    if not img:
        return None
    b64, media_type = img
    async with sem:
        try:
            resp = await client.messages.create(
                model=CATALOG_MODEL,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": prompts.IMAGE_CATALOG_PROMPT},
                    ],
                }],
            )
            return json.loads(_strip_json_fence(resp.content[0].text))
        except Exception as e:
            log.warning(f"catalog failed for {image_path.name}: {e}")
            return None


async def build_catalog(
    client: AsyncAnthropic, handle: str, force: bool = False
) -> dict:
    """
    Catalog every downloaded image. Returns the catalog dict and writes it
    to data/<handle>/image_catalog.json. Cached unless force=True.
    """
    bdir = storage.brand_dir(handle)
    cache = bdir / "image_catalog.json"
    if cache.exists() and not force:
        try:
            return storage.read_json(cache)
        except Exception:
            pass

    posts = storage.read_json(bdir / "posts.json")
    img_dir = bdir / "images"

    targets: list[tuple[str, Path, bool, dict]] = []
    for p in posts:
        sc = p.get("shortCode") or p.get("shortcode") or p.get("id") or ""
        if not sc:
            continue
        main_path = img_dir / f"{sc}.jpg"
        if main_path.exists():
            targets.append((sc, main_path, False, p))
        for i, _child in enumerate(p.get("childPosts") or []):
            child_path = img_dir / f"{sc}_{i}.jpg"
            if child_path.exists():
                targets.append((f"{sc}_{i}", child_path, True, p))

    if not targets:
        catalog = {
            "version": 1,
            "handle": handle,
            "cataloged_at": datetime.now(timezone.utc).isoformat(),
            "model": CATALOG_MODEL,
            "image_count": 0,
            "images": [],
        }
        storage.write_json(cache, catalog)
        return catalog

    log.info(f"cataloging {len(targets)} images for @{handle}")
    sem = asyncio.Semaphore(CATALOG_CONCURRENCY)
    results = await asyncio.gather(
        *[_catalog_one(client, path, sem) for _, path, _, _ in targets],
        return_exceptions=False,
    )

    images: list[dict] = []
    for (shortcode, path, is_child, post), data in zip(targets, results):
        if data is None:
            continue
        images.append({
            "shortcode": shortcode,
            "post_url": post.get("url"),
            "image_path": str(path.relative_to(bdir)).replace("\\", "/"),
            "is_carousel_child": is_child,
            "post_likes": post.get("likesCount", 0),
            "post_timestamp": post.get("timestamp"),
            **data,
        })

    catalog = {
        "version": 1,
        "handle": handle,
        "cataloged_at": datetime.now(timezone.utc).isoformat(),
        "model": CATALOG_MODEL,
        "image_count": len(images),
        "images": images,
    }
    storage.write_json(cache, catalog)
    log.info(f"cataloged {len(images)}/{len(targets)} images for @{handle}")
    return catalog


def summarize_catalog(catalog: dict) -> str:
    """Compact one-line-per-image text representation for prompt injection."""
    images = catalog.get("images", [])
    if not images:
        return "(no images cataloged)"
    lines: list[str] = []
    for img in images:
        text = (img.get("on_screen_text") or "").replace("\n", " ")[:80]
        line = (
            f"- {img['shortcode']}"
            f" | likes={img.get('post_likes', 0)}"
            f" | layout={img.get('layout', '?')}"
            f" | mood={img.get('mood', '?')}"
            f" | colors={','.join((img.get('dominant_colors_hex') or [])[:3])}"
            f" | quality={img.get('design_quality', '?')}"
            f" | text={text!r}"
            f" | for={','.join((img.get('usable_for') or [])[:6])}"
            f" | subject={(img.get('subject') or '')[:120]}"
        )
        lines.append(line)
    return "\n".join(lines)


def pick_images_for_dna(catalog: dict, n: int = 12) -> list[dict]:
    """Pick a diverse, high-engagement set of images to attach to the DNA call."""
    images = list(catalog.get("images", []))
    if not images:
        return []
    images.sort(key=lambda i: i.get("post_likes", 0), reverse=True)
    seen_parents: set[str] = set()
    out: list[dict] = []
    for img in images:
        parent = img["shortcode"].split("_")[0]
        if parent in seen_parents:
            continue
        seen_parents.add(parent)
        out.append(img)
        if len(out) >= n:
            break
    return out


def pick_images_for_asset(catalog: dict, asset_type: str, n: int = 5) -> list[dict]:
    """Pick images most likely to strengthen this asset type."""
    images = list(catalog.get("images", []))
    if not images:
        return []

    def score(img: dict) -> tuple:
        usable = asset_type in (img.get("usable_for") or [])
        quality = {"high": 2, "medium": 1, "low": 0}.get(img.get("design_quality"), 0)
        return (usable, quality, img.get("post_likes", 0))

    images.sort(key=score, reverse=True)
    seen_parents: set[str] = set()
    out: list[dict] = []
    for img in images:
        parent = img["shortcode"].split("_")[0]
        if parent in seen_parents:
            continue
        seen_parents.add(parent)
        out.append(img)
        if len(out) >= n:
            break
    return out
