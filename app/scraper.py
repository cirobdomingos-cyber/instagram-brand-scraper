"""
Apify Instagram scraper — pure async functions.

Two phases:
  1. fetch_profile(handle) — profile-details payload (bio, followers, etc.)
  2. fetch_posts(handle, limit) — array of post dicts with captions, urls, etc.

No DB, no FastAPI, no I/O beyond network — those concerns belong upstream.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import httpx


APIFY_ACTOR_ID = "apify~instagram-scraper"
APIFY_RUN_URL = (
    f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
)
# Cold-start on the free tier can take 60s+. Long timeout is intentional.
APIFY_TIMEOUT_S = 240

_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_IMAGE_CONCURRENCY = 8


class ScrapeError(RuntimeError):
    """Raised when Apify itself fails — distinguish from empty results."""


async def _run_apify(token: str, payload: dict) -> list:
    async with httpx.AsyncClient(timeout=APIFY_TIMEOUT_S) as c:
        r = await c.post(f"{APIFY_RUN_URL}?token={token}", json=payload)
    if r.status_code not in (200, 201):
        raise ScrapeError(f"Apify HTTP {r.status_code}: {r.text[:300]}")
    items = r.json()
    if not isinstance(items, list):
        raise ScrapeError(f"Apify returned {type(items).__name__}, expected list")
    return items


async def fetch_profile(token: str, handle: str) -> dict:
    items = await _run_apify(token, {
        "directUrls": [f"https://www.instagram.com/{handle}/"],
        "resultsType": "details",
        "addParentData": False,
    })
    return items[0] if items else {}


async def fetch_posts(token: str, handle: str, limit: int) -> list[dict]:
    return await _run_apify(token, {
        "directUrls": [f"https://www.instagram.com/{handle}/"],
        "resultsType": "posts",
        "resultsLimit": limit,
        "addParentData": False,
    })


async def _download_one(
    client: httpx.AsyncClient, url: str, dest: Path, sem: asyncio.Semaphore
) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    async with sem:
        try:
            r = await client.get(url, headers=_IMAGE_HEADERS, follow_redirects=True)
            if r.status_code != 200:
                print(f"  [warn] {dest.name}: HTTP {r.status_code}", file=sys.stderr)
                return False
            if len(r.content) > _MAX_IMAGE_BYTES:
                return False
            dest.write_bytes(r.content)
            return True
        except Exception as e:
            print(f"  [warn] {dest.name}: {e}", file=sys.stderr)
            return False


async def download_images(
    profile: dict, posts: list[dict], out_dir: Path
) -> tuple[int, int]:
    """Download profile pic + every post image (and carousel children) to disk."""
    img_dir = out_dir / "images"
    img_dir.mkdir(exist_ok=True)
    sem = asyncio.Semaphore(_IMAGE_CONCURRENCY)
    tasks: list = []
    async with httpx.AsyncClient(timeout=20) as client:
        ppic = profile.get("profilePicUrlHD") or profile.get("profilePicUrl")
        if ppic:
            tasks.append(_download_one(client, ppic, out_dir / "profile_pic.jpg", sem))
        for p in posts:
            sc = p.get("shortCode") or p.get("shortcode") or p.get("id") or ""
            if not sc:
                continue
            url = p.get("displayUrl")
            if url:
                tasks.append(_download_one(client, url, img_dir / f"{sc}.jpg", sem))
            for i, child in enumerate(p.get("childPosts") or []):
                child_url = child.get("displayUrl")
                if child_url:
                    tasks.append(_download_one(client, child_url, img_dir / f"{sc}_{i}.jpg", sem))
        results = await asyncio.gather(*tasks, return_exceptions=False)
    return sum(1 for r in results if r), len(tasks)


def build_manifest(handle: str, profile: dict, posts: list[dict], scraped_at_iso: str) -> dict:
    types: dict[str, int] = {}
    likes: list[int] = []
    comments: list[int] = []
    top_post: Optional[dict] = None
    top_likes = -1
    hashtag_counts: dict[str, int] = {}
    mentions_counts: dict[str, int] = {}

    for p in posts:
        t = p.get("type") or "Unknown"
        types[t] = types.get(t, 0) + 1
        l = p.get("likesCount") or 0
        c = p.get("commentsCount") or 0
        likes.append(l)
        comments.append(c)
        if l > top_likes:
            top_likes = l
            top_post = p
        for tag in p.get("hashtags") or []:
            hashtag_counts[tag] = hashtag_counts.get(tag, 0) + 1
        for m in p.get("mentions") or []:
            mentions_counts[m] = mentions_counts.get(m, 0) + 1

    avg_likes = round(sum(likes) / len(likes), 1) if likes else 0
    avg_comments = round(sum(comments) / len(comments), 1) if comments else 0
    followers = profile.get("followersCount") or 0
    eng_rate = round((avg_likes + avg_comments) / followers * 100, 2) if followers else None

    return {
        "handle": handle,
        "scraped_at": scraped_at_iso,
        "profile_summary": {
            "fullName": profile.get("fullName"),
            "biography": profile.get("biography"),
            "followersCount": followers,
            "followsCount": profile.get("followsCount"),
            "postsCount": profile.get("postsCount"),
            "businessCategoryName": profile.get("businessCategoryName"),
            "isBusinessAccount": profile.get("isBusinessAccount"),
            "externalUrl": profile.get("externalUrl"),
            "verified": profile.get("verified"),
        },
        "post_count": len(posts),
        "post_count_by_type": types,
        "engagement": {
            "avg_likes": avg_likes,
            "avg_comments": avg_comments,
            "engagement_rate_pct": eng_rate,
            "top_post_url": (top_post or {}).get("url", ""),
            "top_post_likes": max(top_likes, 0),
        },
        "top_hashtags": sorted(hashtag_counts.items(), key=lambda x: -x[1])[:15],
        "top_mentions": sorted(mentions_counts.items(), key=lambda x: -x[1])[:15],
        "images_dir": "images/",
    }
