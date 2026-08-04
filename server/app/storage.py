"""Per-visitor storage in the owner's Google Drive.

Each visitor gets their **own file** — `atlas-<hash>.json` — inside one folder
in the owner's Drive. That matters: a single shared file written by many
browsers at once would lose data, because Drive has no merge and the last
write silently wins. One file per visitor removes the conflict entirely.

Auth is the owner's OAuth refresh token, exchanged for short-lived access
tokens. Set it up once with `python setup_drive.py`.

If Drive isn't configured the server falls back to local files, so it still
runs (useful in development) — but on an ephemeral host that disk is wiped on
deploy, which is exactly why Drive is the real target.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class StorageError(RuntimeError):
    pass


class LocalStorage:
    """Development fallback. Not durable on ephemeral hosts."""

    def __init__(self, root: str = "data_store"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, key: str) -> dict | None:
        path = self.root / f"atlas-{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def save(self, key: str, payload: dict) -> None:
        path = self.root / f"atlas-{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)   # atomic, so a crash mid-write can't truncate the file

    @property
    def backend(self) -> str:
        return "local"


class DriveStorage:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, folder_name: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.folder_name = folder_name
        self._token = ""
        self._token_expires = 0.0
        self._folder_id = ""
        self._file_ids: dict[str, str] = {}
        self._lock = threading.Lock()

    # --- auth -----------------------------------------------------------
    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires - 60:
                return self._token
            resp = requests.post(TOKEN_URL, timeout=20, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            })
            if resp.status_code != 200:
                raise StorageError(f"Google token refresh failed ({resp.status_code}). "
                                   "Re-run setup_drive.py to issue a new refresh token.")
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = time.time() + float(data.get("expires_in", 3600))
            return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token()}"}

    # --- folder ---------------------------------------------------------
    def _folder(self) -> str:
        if self._folder_id:
            return self._folder_id
        query = (f"name='{self.folder_name}' and mimeType='application/vnd.google-apps.folder' "
                 "and trashed=false")
        resp = requests.get(FILES_URL, timeout=20, headers=self._headers(),
                            params={"q": query, "fields": "files(id)"})
        if resp.ok and resp.json().get("files"):
            self._folder_id = resp.json()["files"][0]["id"]
            return self._folder_id
        resp = requests.post(FILES_URL, timeout=20, headers=self._headers(), json={
            "name": self.folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        })
        if not resp.ok:
            raise StorageError(f"Could not create Drive folder ({resp.status_code}).")
        self._folder_id = resp.json()["id"]
        return self._folder_id

    def _find_file(self, key: str) -> str | None:
        if key in self._file_ids:
            return self._file_ids[key]
        name = f"atlas-{key}.json"
        query = f"name='{name}' and '{self._folder()}' in parents and trashed=false"
        resp = requests.get(FILES_URL, timeout=20, headers=self._headers(),
                            params={"q": query, "fields": "files(id)"})
        if resp.ok and resp.json().get("files"):
            self._file_ids[key] = resp.json()["files"][0]["id"]
            return self._file_ids[key]
        return None

    # --- api ------------------------------------------------------------
    def load(self, key: str) -> dict | None:
        file_id = self._find_file(key)
        if not file_id:
            return None
        resp = requests.get(f"{FILES_URL}/{file_id}", timeout=25,
                            headers=self._headers(), params={"alt": "media"})
        if not resp.ok:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def save(self, key: str, payload: dict) -> None:
        body = json.dumps(payload)
        file_id = self._find_file(key)
        if file_id:
            resp = requests.patch(
                f"{UPLOAD_URL}/{file_id}", timeout=30, params={"uploadType": "media"},
                headers={**self._headers(), "Content-Type": "application/json"}, data=body)
            if resp.status_code == 404:
                # Owner deleted it from Drive — forget the id and make a new one.
                self._file_ids.pop(key, None)
                file_id = None
            elif not resp.ok:
                raise StorageError(f"Drive write failed ({resp.status_code}).")
        if not file_id:
            meta = {"name": f"atlas-{key}.json", "parents": [self._folder()],
                    "mimeType": "application/json"}
            resp = requests.post(FILES_URL, timeout=25, headers=self._headers(), json=meta)
            if not resp.ok:
                raise StorageError(f"Drive create failed ({resp.status_code}).")
            new_id = resp.json()["id"]
            self._file_ids[key] = new_id
            resp = requests.patch(
                f"{UPLOAD_URL}/{new_id}", timeout=30, params={"uploadType": "media"},
                headers={**self._headers(), "Content-Type": "application/json"}, data=body)
            if not resp.ok:
                raise StorageError(f"Drive write failed ({resp.status_code}).")

    @property
    def backend(self) -> str:
        return "drive"


def build_storage(settings) -> LocalStorage | DriveStorage:
    if settings.drive_enabled:
        return DriveStorage(settings.google_client_id, settings.google_client_secret,
                            settings.google_refresh_token, settings.drive_folder_name)
    return LocalStorage()
