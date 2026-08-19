#!/usr/bin/env python3
"""Build courses.json — the data file the Mindora web app reads at runtime.

Two inputs, one output:

  data/Global_Learning_Resource_Directory.xlsx  ->  the browsable course catalog
  data/paths.json                               ->  the curated 14-day paths

Editing either input and re-running this script is how course content changes;
it should never require touching application code.

    python tools/build_catalog.py [--check]

--check verifies courses.json is already up to date (used in CI) and exits
non-zero if it would change, instead of writing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "Global_Learning_Resource_Directory.xlsx"
PATHS_JSON = ROOT / "data" / "paths.json"
OUT = ROOT / "courses.json"
THUMBS_DIR = ROOT / "thumbnails"

SHEET = "Master Course Directory"
# Only these five columns are consumed by the app; the rest of the sheet
# (iframe snippets, notes) is editorial and stays in the spreadsheet.
COLUMNS = {
    "Subject": "subject",
    "Level / Grade Band": "level",
    "Platform / Source": "platform",
    "Course Title": "title",
    "Direct Link": "url",
    "Cost": "cost",
}


def youtube_embed(url: str):
    """Return {"kind": "video"|"playlist", "id": ...} when a URL can be iframed.

    Mirrors extractYouTubeEmbed() in the app exactly — channel pages, search
    pages and non-YouTube links are all correctly rejected, because those are
    precisely what cannot be embedded.
    """
    if not url:
        return None
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    qs = parse_qs(u.query or "")
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
        return {"kind": "video", "id": vid} if vid else None
    if host in ("youtube.com", "youtube-nocookie.com"):
        if u.path == "/watch" and qs.get("v"):
            return {"kind": "video", "id": qs["v"][0]}
        if u.path == "/playlist" and qs.get("list"):
            return {"kind": "playlist", "id": qs["list"][0]}
        m = re.match(r"^/(embed|shorts|live)/([^/?]+)", u.path)
        if m:
            if m.group(2) == "videoseries":
                return {"kind": "playlist", "id": qs["list"][0]} if qs.get("list") else None
            return {"kind": "video", "id": m.group(2)}
    return None


def clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def load_catalog() -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        sys.exit("openpyxl is required: pip install -r tools/requirements.txt")

    if not XLSX.exists():
        sys.exit(f"missing spreadsheet: {XLSX}")

    wb = load_workbook(XLSX, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        sys.exit(f"sheet {SHEET!r} not found; have {wb.sheetnames}")
    ws = wb[SHEET]

    rows = ws.iter_rows(values_only=True)
    header = [clean(h) for h in next(rows)]
    idx = {name: header.index(name) for name in COLUMNS if name in header}
    missing = set(COLUMNS) - set(idx)
    if missing:
        sys.exit(f"spreadsheet is missing expected column(s): {sorted(missing)}")

    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        entry = {key: clean(row[idx[col]]) for col, key in COLUMNS.items()}
        if not entry["title"] or not entry["url"]:
            continue
        # The sheet repeats the same course across platform variants; collapse
        # exact duplicates so the app's catalog isn't full of noise.
        fingerprint = (entry["title"], entry["url"], entry["platform"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        embed = youtube_embed(entry["url"])
        entry["embeddable"] = bool(embed)
        if embed:
            entry["embedKind"] = embed["kind"]
            entry["embedId"] = embed["id"]
            # tools/fetch_thumbnails.py populates thumbnails/{videoId}.jpg;
            # attach the field only when the file actually exists, so a
            # course never claims an image it doesn't have.
            if (THUMBS_DIR / f"{embed['id']}.jpg").exists():
                entry["thumbnail"] = f"thumbnails/{embed['id']}.jpg"
        out.append(entry)

    out.sort(key=lambda e: (e["subject"], e["level"], e["title"]))
    wb.close()
    return out


def load_paths() -> list[dict]:
    if not PATHS_JSON.exists():
        sys.exit(f"missing curated paths: {PATHS_JSON}")
    paths = json.loads(PATHS_JSON.read_text(encoding="utf-8"))
    for path in paths:
        for required in ("id", "title", "subject", "days"):
            if required not in path:
                sys.exit(f"path {path.get('id', '?')} is missing {required!r}")
        if len(path["days"]) != 14:
            sys.exit(f"path {path['id']} has {len(path['days'])} days, expected 14")
    return paths


def summarise(catalog: list[dict], paths: list[dict]) -> dict:
    subjects: dict[str, int] = {}
    for entry in catalog:
        subjects[entry["subject"]] = subjects.get(entry["subject"], 0) + 1
    playable = total = 0
    for path in paths:
        for day in path["days"]:
            for cls in day.get("classes", []):
                if cls.get("type") == "rest":
                    continue
                total += 1
                if youtube_embed(cls.get("resourceUrl", "")) or cls.get("altVideoUrl"):
                    playable += 1
    return {
        "courses": len(catalog),
        "subjects": dict(sorted(subjects.items())),
        "embeddable": sum(1 for e in catalog if e["embeddable"]),
        "paths": len(paths),
        "pathLessons": total,
        "pathLessonsPlayable": playable,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="fail if courses.json is stale instead of rewriting it")
    args = ap.parse_args()

    catalog = load_catalog()
    paths = load_paths()
    stats = summarise(catalog, paths)
    payload = {
        "_generated_by": "tools/build_catalog.py — edit data/, not this file",
        "stats": stats,
        "paths": paths,
        "catalog": catalog,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("courses.json is out of date — run: python tools/build_catalog.py")
            return 1
        print("courses.json is up to date")
        return 0

    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  {stats['courses']} courses across {len(stats['subjects'])} subjects "
          f"({stats['embeddable']} embeddable)")
    print(f"  {stats['paths']} curated paths, "
          f"{stats['pathLessonsPlayable']}/{stats['pathLessons']} lessons playable in-app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
