"""
Anthropic generation layer.

Two-phase:
  extract_brand_dna(handle) — runs once, caches result to brand_dna.json
  generate_asset(handle, asset_type, audience, constraints) — uses cached DNA

Why two phases:
  - DNA extraction is the expensive/multimodal step (reads images, big context).
    Once extracted, it's reused across many asset requests.
  - Subsequent asset calls are short and cheap (DNA is small JSON, no images).
  - Maps directly to a real product UX: user clicks "Scrape", sees DNA in <30s,
    then iterates on assets.

Prompt caching:
  The system prompt + scraped images are marked cache_control=ephemeral so
  the second asset request for the same brand within 5 minutes is much
  cheaper and faster. (Anthropic's prompt cache: 5-min TTL, ~90% discount
  on cached tokens.)
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Optional

from anthropic import AsyncAnthropic

from . import prompts, storage


# claude-sonnet-4-6 has the right balance: vision-capable, design-coherent,
# 4× cheaper than Opus. Opus only matters when the design task is genuinely
# hard (logo systems, complex art direction) — make that the upgrade path,
# not the default.
DEFAULT_MODEL = "claude-sonnet-4-6"
DESIGN_MODEL = "claude-opus-4-7"  # use for logo_concepts / brand_style_guide

# How many post images to include in the DNA call. More = better visual
# grounding, but costs more tokens and slows the call. 6 is the sweet spot
# in practice: profile pic + 5 top-engagement posts.
N_IMAGES_FOR_DNA = 6
# How many post records (caption + metadata, no image) to include in the
# DNA prompt. Top-engagement first, then a recency sample.
N_POSTS_FOR_DNA_TEXT = 25
# Posts included as evidence in asset prompts (text-only, smaller).
N_POSTS_FOR_ASSET = 15


def _pick_model(asset_type: str) -> str:
    if asset_type in {"logo_concepts", "brand_style_guide"}:
        return DESIGN_MODEL
    return DEFAULT_MODEL


def _select_top_posts(posts: list[dict], n: int) -> list[dict]:
    """Top-by-likes, with most recent appended to fill the gap."""
    if not posts:
        return []
    by_likes = sorted(posts, key=lambda p: p.get("likesCount") or 0, reverse=True)
    head = by_likes[: max(n // 2, 1)]
    by_time = sorted(posts, key=lambda p: p.get("timestamp") or "", reverse=True)
    seen_codes = {p.get("shortCode") or p.get("shortcode") or p.get("id") for p in head}
    for p in by_time:
        sc = p.get("shortCode") or p.get("shortcode") or p.get("id")
        if sc not in seen_codes:
            head.append(p)
            seen_codes.add(sc)
        if len(head) >= n:
            break
    return head[:n]


def _slim_post(p: dict) -> dict:
    """Drop noise fields so the prompt stays in budget."""
    return {
        "shortcode": p.get("shortCode") or p.get("shortcode") or p.get("id"),
        "url": p.get("url"),
        "type": p.get("type"),
        "caption": (p.get("caption") or "")[:1200],
        "timestamp": p.get("timestamp"),
        "likes": p.get("likesCount"),
        "comments": p.get("commentsCount"),
        "hashtags": p.get("hashtags") or [],
        "mentions": p.get("mentions") or [],
        "location": p.get("locationName"),
    }


def _slim_profile(p: dict) -> dict:
    return {
        "username": p.get("username"),
        "fullName": p.get("fullName"),
        "biography": p.get("biography"),
        "followersCount": p.get("followersCount"),
        "followsCount": p.get("followsCount"),
        "postsCount": p.get("postsCount"),
        "businessCategoryName": p.get("businessCategoryName"),
        "isBusinessAccount": p.get("isBusinessAccount"),
        "externalUrl": p.get("externalUrl"),
        "verified": p.get("verified"),
    }


def _image_block(path: Path) -> Optional[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        data = path.read_bytes()
    except Exception:
        return None
    suffix = path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


async def extract_brand_dna(client: AsyncAnthropic, handle: str) -> dict:
    """
    Phase 1. Reads scrape from disk, calls Claude with profile + posts +
    selected images, parses the returned JSON, caches it to brand_dna.json.
    """
    bdir = storage.brand_dir(handle)
    profile = storage.read_json(bdir / "profile.json")
    posts = storage.read_json(bdir / "posts.json")
    manifest = storage.read_json(bdir / "manifest.json")

    top_posts = _select_top_posts(posts, N_POSTS_FOR_DNA_TEXT)
    slim_posts = [_slim_post(p) for p in top_posts]

    # Image selection: profile pic + top N posts by likes (skip those whose
    # image file is missing — Apify CDN sometimes 403s us).
    image_blocks: list[dict] = []
    pp = _image_block(bdir / "profile_pic.jpg")
    if pp:
        image_blocks.append(pp)
    image_posts = _select_top_posts(posts, N_IMAGES_FOR_DNA - 1)
    for p in image_posts:
        sc = p.get("shortCode") or p.get("shortcode") or p.get("id") or ""
        if not sc:
            continue
        blk = _image_block(bdir / "images" / f"{sc}.jpg")
        if blk:
            image_blocks.append(blk)
        if len(image_blocks) >= N_IMAGES_FOR_DNA:
            break

    text_block = {
        "type": "text",
        "text": prompts.BRAND_DNA_PROMPT.format(
            profile_json=json.dumps(_slim_profile(profile), ensure_ascii=False, indent=2),
            manifest_json=json.dumps(manifest, ensure_ascii=False, indent=2),
            n_posts=len(slim_posts),
            posts_json=json.dumps(slim_posts, ensure_ascii=False, indent=2),
        ),
        # Cache the long input — same handle's DNA call won't hit it again,
        # but if the owner re-runs (e.g. after a re-scrape) inside the 5-min
        # window, we save tokens.
        "cache_control": {"type": "ephemeral"},
    }

    response = await client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": prompts.SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": image_blocks + [text_block]}],
    )

    raw = response.content[0].text
    try:
        dna = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned invalid BRAND_DNA JSON: {e}\n{raw[:500]}")

    storage.write_json(bdir / "brand_dna.json", dna)
    return dna


async def get_or_extract_brand_dna(client: AsyncAnthropic, handle: str) -> dict:
    bdir = storage.brand_dir(handle)
    cached = bdir / "brand_dna.json"
    if cached.exists():
        try:
            return storage.read_json(cached)
        except Exception:
            pass
    return await extract_brand_dna(client, handle)


async def generate_asset(
    client: AsyncAnthropic,
    handle: str,
    asset_type: str,
    audience_or_goal: str,
    constraints: str,
) -> dict:
    """Phase 2. Returns {markdown, output_dir_relpath}."""
    if asset_type not in prompts.ASSET_FORMATS:
        raise ValueError(f"unknown asset_type: {asset_type}")

    bdir = storage.brand_dir(handle)
    posts = storage.read_json(bdir / "posts.json")
    dna = await get_or_extract_brand_dna(client, handle)

    evidence_posts = [_slim_post(p) for p in _select_top_posts(posts, N_POSTS_FOR_ASSET)]

    prompt_text = prompts.ASSET_PROMPT.format(
        asset_type=asset_type,
        format_spec=prompts.ASSET_FORMATS[asset_type],
        audience_or_goal=audience_or_goal or "(not specified — use best judgement)",
        constraints=constraints or "(none)",
        brand_dna_json=json.dumps(dna, ensure_ascii=False, indent=2),
        posts_json=json.dumps(evidence_posts, ensure_ascii=False, indent=2),
    )

    response = await client.messages.create(
        model=_pick_model(asset_type),
        max_tokens=8192,
        system=[{"type": "text", "text": prompts.SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt_text}],
    )

    body = response.content[0].text
    out_dir = storage.new_output_dir(handle, asset_type)
    # Save raw markdown always; if the body contains an HTML doc, also save .html.
    (out_dir / "asset.md").write_text(body, encoding="utf-8")
    extracted_html = _extract_first_code_block(body, language="html")
    if extracted_html:
        (out_dir / "asset.html").write_text(extracted_html, encoding="utf-8")
    extracted_svg = _extract_first_code_block(body, language="svg")
    if extracted_svg:
        (out_dir / "asset.svg").write_text(extracted_svg, encoding="utf-8")

    storage.write_json(out_dir / "meta.json", {
        "handle": handle,
        "asset_type": asset_type,
        "audience_or_goal": audience_or_goal,
        "constraints": constraints,
        "model": _pick_model(asset_type),
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", None),
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", None),
    })

    return {
        "markdown": body,
        "html": extracted_html,
        "svg": extracted_svg,
        "output_dir": storage.output_relpath(out_dir),
    }


def _extract_first_code_block(text: str, language: str) -> Optional[str]:
    """Pull a fenced ```lang ... ``` block out of the markdown body."""
    pat = re.compile(rf"```{language}\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
    m = pat.search(text)
    return m.group(1).strip() if m else None
