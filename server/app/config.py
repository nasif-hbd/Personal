"""Configuration, entirely from environment variables.

Nothing secret is ever committed: the Anthropic key and the Google refresh
token are set on the host (Render/Fly/your own box), never in this repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()] or default


@dataclass(frozen=True)
class Settings:
    # --- Anthropic ------------------------------------------------------
    anthropic_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    # Clients pick from this list only; anything else is rejected, so a caller
    # can't quietly upgrade themselves to the priciest model on your account.
    allowed_models: list[str] = field(default_factory=lambda: _list(
        "ALLOWED_MODELS", ["claude-sonnet-5", "claude-haiku-4-5"]))
    default_model: str = field(default_factory=lambda: os.environ.get("DEFAULT_MODEL", "claude-sonnet-5"))
    max_tokens: int = field(default_factory=lambda: _int("MAX_TOKENS", 12000))

    # --- Spend controls (the whole point — this bills to your account) ---
    daily_request_cap: int = field(default_factory=lambda: _int("DAILY_REQUEST_CAP", 300))
    per_visitor_daily_cap: int = field(default_factory=lambda: _int("PER_VISITOR_DAILY_CAP", 25))
    per_minute_cap: int = field(default_factory=lambda: _int("PER_MINUTE_CAP", 4))
    # Optional shared passcode. Empty = open to anyone who finds the URL.
    access_code: str = field(default_factory=lambda: os.environ.get("ACCESS_CODE", ""))

    # --- Storage --------------------------------------------------------
    google_client_id: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    google_refresh_token: str = field(default_factory=lambda: os.environ.get("GOOGLE_REFRESH_TOKEN", ""))
    drive_folder_name: str = field(default_factory=lambda: os.environ.get("DRIVE_FOLDER_NAME", "Atlas App Data"))
    # Where quota counters and the visitor→file index live.
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "atlas.db"))
    # Signs anonymous visitor ids so they can't be forged to steal someone's data.
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))

    # --- CORS -----------------------------------------------------------
    allowed_origins: list[str] = field(default_factory=lambda: _list(
        "ALLOWED_ORIGINS", ["https://nasif-hbd.github.io"]))

    @property
    def chat_enabled(self) -> bool:
        return bool(self.anthropic_key)

    @property
    def drive_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)


settings = Settings()
