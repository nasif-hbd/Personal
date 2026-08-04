#!/usr/bin/env python3
"""One-time: get a Google refresh token so the server can write to YOUR Drive.

Run this on your own machine (not the server) — it opens a browser, you approve
access once, and it prints a refresh token. Put that token in the server's
environment as GOOGLE_REFRESH_TOKEN.

    python setup_drive.py --client-id XXX --client-secret YYY

The OAuth client must be a **Desktop app** client (Google Cloud Console →
Credentials → Create credentials → OAuth client ID → Desktop app). That's a
different client from the Web one the browser app uses.

Scope requested is drive.file: this server can only ever touch files it
creates itself. It cannot read anything else in your Drive.
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"

_result: dict[str, str] = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _result.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in params
        self.wfile.write(
            b"<h2>All set - you can close this tab.</h2>" if ok
            else b"<h2>Authorization failed. Check the terminal.</h2>"
        )

    def log_message(self, *args):  # silence the default request logging
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    args = ap.parse_args()

    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "client_id": args.client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",        # forces a refresh token to be issued
        "state": state,
    })
    url = f"{AUTH_URL}?{query}"

    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("Opening your browser to authorize Drive access…")
    print(f"If it doesn't open, visit:\n  {url}\n")
    webbrowser.open(url)

    while "code" not in _result and "error" not in _result:
        pass
    server.shutdown()

    if "error" in _result:
        print(f"Authorization failed: {_result['error']}", file=sys.stderr)
        return 1
    if _result.get("state") != state:
        print("State mismatch — aborting.", file=sys.stderr)
        return 1

    resp = requests.post(TOKEN_URL, timeout=30, data={
        "code": _result["code"],
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    })
    if resp.status_code != 200:
        print(f"Token exchange failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        return 1
    token = resp.json().get("refresh_token")
    if not token:
        print("No refresh token returned. Revoke the app's access at "
              "https://myaccount.google.com/permissions and run this again.", file=sys.stderr)
        return 1

    print("\nSuccess. Set these on your server:\n")
    print(f"  GOOGLE_CLIENT_ID={args.client_id}")
    print(f"  GOOGLE_CLIENT_SECRET={args.client_secret}")
    print(f"  GOOGLE_REFRESH_TOKEN={token}")
    print("\nKeep the refresh token secret — it grants write access to files "
          "this app creates in your Drive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
