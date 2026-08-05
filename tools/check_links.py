#!/usr/bin/env python3
"""Verify every link Mindora ships still works.

Checks three sets of URLs:
  * every YouTube video/playlist used by the curated 14-day paths
    (both the lesson's own resourceUrl and its altVideoUrl stand-in)
  * every course link in the catalog built from the spreadsheet
  * YouTube IDs are checked via the oEmbed endpoint, which is authoritative:
    a 404 there means the video is gone or private, not merely slow.

    python tools/check_links.py [--paths-only] [--timeout 15] [--workers 12]

Writes link-report.json and exits non-zero **only for definitive failures**
(a video that is gone, or one that forbids embedding). Network problems are
reported as "unreachable" and never fail the run, so a flaky runner or a
firewalled sandbox can't produce a false alarm.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
COURSES = ROOT / "courses.json"
REPORT = ROOT / "link-report.json"

OEMBED = "https://www.youtube.com/oembed?url={}&format=json"

# Definitive problems — these should break the build.
FATAL = {"dead", "embedding-disabled"}


def session():
    import requests
    s = requests.Session()
    s.headers.update({
        # Some hosts serve 403 to unknown agents; identify honestly instead.
        "User-Agent": "atlas-link-checker/1.0 (+https://github.com/nasif-hbd/Personal)"
    })
    return s


def check_youtube(sess, url: str, timeout: int) -> dict:
    import requests
    target = OEMBED.format(quote(url, safe=""))
    try:
        r = sess.get(target, timeout=timeout)
    except requests.RequestException as exc:
        return {"status": "unreachable", "detail": type(exc).__name__}
    if r.status_code == 200:
        try:
            return {"status": "ok", "title": r.json().get("title", "")}
        except ValueError:
            return {"status": "ok", "title": ""}
    if r.status_code in (401, 403):
        return {"status": "embedding-disabled", "detail": f"HTTP {r.status_code}"}
    if r.status_code in (404, 410):
        return {"status": "dead", "detail": f"HTTP {r.status_code}"}
    return {"status": "unreachable", "detail": f"HTTP {r.status_code}"}


def check_plain(sess, url: str, timeout: int) -> dict:
    import requests
    try:
        r = sess.head(url, timeout=timeout, allow_redirects=True)
        # Plenty of sites reject HEAD outright; retry those with a ranged GET.
        if r.status_code in (403, 405, 501):
            r = sess.get(url, timeout=timeout, allow_redirects=True,
                         stream=True, headers={"Range": "bytes=0-2048"})
            r.close()
    except requests.RequestException as exc:
        return {"status": "unreachable", "detail": type(exc).__name__}
    if r.status_code in (404, 410):
        return {"status": "dead", "detail": f"HTTP {r.status_code}"}
    if r.status_code >= 400:
        # 401/403 on a course page usually means bot-blocking, not a dead link.
        return {"status": "unreachable", "detail": f"HTTP {r.status_code}"}
    return {"status": "ok"}


def collect_targets(data: dict, paths_only: bool) -> list[dict]:
    targets: list[dict] = []
    seen: set[str] = set()

    def add(url: str, kind: str, where: str):
        if not url or url in seen:
            return
        seen.add(url)
        targets.append({"url": url, "kind": kind, "where": where})

    for path in data.get("paths", []):
        for day_no, day in enumerate(path.get("days", []), 1):
            for cls in day.get("classes", []):
                where = f"path:{path['id']} day{day_no} — {cls.get('title', '')}"
                url = cls.get("resourceUrl", "")
                if "youtube.com" in url or "youtu.be" in url:
                    add(url, "youtube", where)
                elif url:
                    add(url, "web", where)
                if cls.get("altVideoUrl"):
                    add(cls["altVideoUrl"], "youtube", where + " (alt)")

    if not paths_only:
        for entry in data.get("catalog", []):
            where = f"catalog:{entry.get('subject', '')} — {entry.get('title', '')}"
            add(entry.get("url", ""), "youtube" if entry.get("embeddable") else "web", where)

    return targets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paths-only", action="store_true",
                    help="check only the curated paths, skipping the wider catalog")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    if not COURSES.exists():
        sys.exit("courses.json not found — run tools/build_catalog.py first")
    data = json.loads(COURSES.read_text(encoding="utf-8"))
    targets = collect_targets(data, args.paths_only)
    print(f"checking {len(targets)} unique links…")

    sess = session()

    def run(t: dict) -> dict:
        checker = check_youtube if t["kind"] == "youtube" else check_plain
        return {**t, **checker(sess, t["url"], args.timeout)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, targets))

    tally: dict[str, int] = {}
    for r in results:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    problems = [r for r in results if r["status"] in FATAL]

    REPORT.write_text(json.dumps({
        "summary": tally,
        "problems": problems,
        "results": results,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n".join(f"  {status:>18}: {count}" for status, count in sorted(tally.items())))
    if problems:
        print(f"\n{len(problems)} link(s) need attention:")
        for p in problems:
            print(f"  [{p['status']}] {p['where']}\n      {p['url']}  ({p.get('detail','')})")
    unreachable = tally.get("unreachable", 0)
    if unreachable:
        print(f"\n{unreachable} link(s) were unreachable (network/bot-blocking) — not treated as failures.")
    print(f"\nreport written to {REPORT.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
