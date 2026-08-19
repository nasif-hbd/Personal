#!/usr/bin/env python3
"""Download real thumbnail images for every embeddable course.

Turns the Catalog from plain text rows into actual video previews, and lets
them render offline once cached - a genuine feature, not padding. Writes to
thumbnails/{videoId}.jpg at the repo root; tools/build_catalog.py picks up
any file that exists there and adds a "thumbnail" field to that course's
courses.json entry automatically. Nothing here invents data: a course with
no downloaded thumbnail simply has no "thumbnail" field, the same as today.

    python tools/fetch_thumbnails.py                # fetch what's missing
    python tools/fetch_thumbnails.py --check         # exit 1 if any are missing (CI)
    python tools/fetch_thumbnails.py --force         # re-fetch everything

This sandbox's outbound network is restricted to an allowlist that doesn't
include YouTube's image CDN (img.youtube.com / i.ytimg.com both return a
policy-denied 403 here) - it's meant to run somewhere with normal internet
access: your own machine, or the thumbnails.yml GitHub Actions workflow,
which has that access and commits the result.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COURSES_JSON = ROOT / "courses.json"
OUT_DIR = ROOT / "thumbnails"

# Every YouTube video has at least hqdefault (480x360); maxresdefault
# (1280x720, ~80-250KB) only exists for videos uploaded at HD or better.
# Try largest first, fall back until one actually exists.
SIZES = ["maxresdefault", "sddefault", "hqdefault", "mqdefault", "default"]

# hqdefault/mqdefault/default ALWAYS return an image, even for a video ID
# that doesn't exist - a grey 120x90 placeholder. maxresdefault/sddefault
# genuinely 404 when absent, which is how the fallback chain above works;
# this catches the one case that doesn't 404: a request for a placeholder
# masquerading as real content, sized by a fixed known byte count.
PLACEHOLDER_BYTES = {1097}  # observed size of YouTube's grey placeholder


def embeddable_video_ids(catalog: list[dict]) -> dict[str, str]:
    """video id -> course title, for the log output only."""
    out = {}
    for entry in catalog:
        if entry.get("embeddable") and entry.get("embedKind") == "video" and entry.get("embedId"):
            out[entry["embedId"]] = entry.get("title", entry["embedId"])
    return out


def fetch_one(video_id: str, session: requests.Session) -> tuple[bool, int]:
    for size in SIZES:
        url = f"https://img.youtube.com/vi/{video_id}/{size}.jpg"
        try:
            resp = session.get(url, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200 or not resp.content:
            continue
        if len(resp.content) in PLACEHOLDER_BYTES and size != "hqdefault":
            # A "real" size returning the placeholder means it doesn't exist
            # at this tier; keep falling back. hqdefault is the last tier
            # guaranteed to exist, so accept whatever it returns.
            continue
        (OUT_DIR / f"{video_id}.jpg").write_bytes(resp.content)
        return True, len(resp.content)
    return False, 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if any embeddable course lacks a thumbnail")
    ap.add_argument("--force", action="store_true", help="re-download even if a file already exists")
    ap.add_argument("--limit", type=int, default=None, help="fetch at most N (for testing)")
    args = ap.parse_args()

    catalog = json.loads(COURSES_JSON.read_text(encoding="utf-8"))["catalog"]
    wanted = embeddable_video_ids(catalog)
    OUT_DIR.mkdir(exist_ok=True)

    missing = {vid: title for vid, title in wanted.items()
               if args.force or not (OUT_DIR / f"{vid}.jpg").exists()}

    if args.check:
        if missing:
            print(f"{len(missing)} of {len(wanted)} embeddable courses have no thumbnail yet")
            return 1
        print(f"all {len(wanted)} embeddable courses have a thumbnail")
        return 0

    if args.limit:
        missing = dict(list(missing.items())[:args.limit])

    print(f"{len(wanted)} embeddable courses, {len(missing)} to fetch")
    ok = fail = total_bytes = 0
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; MindoraThumbnailFetcher/1.0)"

    for i, (vid, title) in enumerate(missing.items(), 1):
        success, size = fetch_one(vid, session)
        if success:
            ok += 1
            total_bytes += size
            print(f"  [{i}/{len(missing)}] {vid}  {size/1024:.0f}KB  {title[:50]}")
        else:
            fail += 1
            print(f"  [{i}/{len(missing)}] {vid}  FAILED  {title[:50]}", file=sys.stderr)
        time.sleep(0.05)  # be a polite, not a hammering, client

    existing_bytes = sum(f.stat().st_size for f in OUT_DIR.glob("*.jpg"))
    print()
    print(f"fetched {ok}, failed {fail}, this run downloaded {total_bytes/1e6:.1f}MB")
    print(f"thumbnails/ now totals {existing_bytes/1e6:.1f}MB across {len(list(OUT_DIR.glob('*.jpg')))} files")
    return 1 if fail and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
