"""
CLI wrapper around app.scraper — same scrape, no web app needed.

Usage:
    py -3.12 scrape.py @handle [--posts 50] [--out data]
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app import scraper, storage


async def main_async(args) -> int:
    load_dotenv()
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        print("ERROR: APIFY_API_TOKEN missing. Copy .env.example to .env.", file=sys.stderr)
        return 1

    try:
        handle = storage.normalize_handle(args.handle)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Override DATA_ROOT for CLI users who want a custom path
    if args.out != "data":
        storage.DATA_ROOT = Path(args.out).resolve()
    out_dir = storage.ensure_brand_dir(handle)

    print(f"[1/3] Profile @{handle}...")
    try:
        profile = await scraper.fetch_profile(token, handle)
    except scraper.ScrapeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not profile:
        print(f"ERROR: no profile for @{handle}", file=sys.stderr)
        return 2
    storage.write_json(out_dir / "profile.json", profile)
    print(f"  fullName={profile.get('fullName')!r} followers={profile.get('followersCount')}")

    print(f"[2/3] Posts (up to {args.posts})...")
    try:
        posts = await scraper.fetch_posts(token, handle, args.posts)
    except scraper.ScrapeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    storage.write_json(out_dir / "posts.json", posts)
    print(f"  got {len(posts)} posts")

    print(f"[3/3] Downloading images...")
    ok, total = await scraper.download_images(profile, posts, out_dir)
    print(f"  {ok}/{total} images saved")

    manifest = scraper.build_manifest(
        handle, profile, posts,
        scraped_at_iso=datetime.now(timezone.utc).isoformat(),
    )
    storage.write_json(out_dir / "manifest.json", manifest)

    print(f"\nDone. Folder: {out_dir.resolve()}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Scrape an Instagram brand to disk.")
    p.add_argument("handle")
    p.add_argument("--posts", type=int, default=50)
    p.add_argument("--out", default="data")
    args = p.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
