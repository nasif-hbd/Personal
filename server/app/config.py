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
    # Optional shared passcode gating the whole server. Separate from the
    # free-access code below: this one blocks the app entirely, that one
    # unlocks a paid subscription.
    access_code: str = field(default_factory=lambda: os.environ.get("ACCESS_CODE", ""))

    # --- YouTube (finding a video for a lesson that has no link) ---------
    # Set this and every visitor gets video lookup without holding a key.
    # It must never go in index.html: that file is served publicly, and a
    # key in it belongs to whoever reads the page source.
    #
    # A search costs 100 units of a 10,000/day quota, so the ceiling is 100
    # searches a day for everyone combined. The cache is what makes that
    # workable; these caps stop a scripted caller spending it in a minute.
    youtube_key: str = field(default_factory=lambda: os.environ.get("YOUTUBE_API_KEY", ""))
    yt_daily_searches: int = field(default_factory=lambda: _int("YT_DAILY_SEARCHES", 80))
    yt_visitor_daily_searches: int = field(default_factory=lambda: _int("YT_VISITOR_DAILY_SEARCHES", 10))

    # --- Subscriptions ---------------------------------------------------
    # Chat is the only paid feature; plans, catalog and tracking stay free.
    # Set to 0 for a strict paywall, or a small number to let people try the
    # assistant before paying — conversion is usually better with a taste.
    # Open the whole app to everyone: chat works with no subscription and no
    # code. The payment machinery stays in place and switches back on the
    # moment this is false, so turning it off costs nothing to reverse.
    free_for_all: bool = field(
        default_factory=lambda: os.environ.get("FREE_FOR_ALL", "").strip().lower()
        in ("1", "true", "yes", "on"))
    free_trial_messages: int = field(default_factory=lambda: _int("FREE_TRIAL_MESSAGES", 0))
    # The words that unlock free access. Checked server-side only; matching
    # ignores case and extra spaces. Rotate by changing this env var.
    free_access_code: str = field(default_factory=lambda: os.environ.get("FREE_ACCESS_CODE", "Nafia is my Sister"))
    currency: str = field(default_factory=lambda: os.environ.get("CURRENCY", "BDT"))
    price_monthly: int = field(default_factory=lambda: _int("PRICE_MONTHLY", 499))
    price_yearly: int = field(default_factory=lambda: _int("PRICE_YEARLY", 4499))
    # Protects the owner-only payment review endpoints. No token, no admin API.
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", ""))

    # --- Where visitors send money ---------------------------------------
    # Displayed at checkout. Any left empty is hidden, so you only advertise
    # the rails you actually accept.
    pay_bkash: str = field(default_factory=lambda: os.environ.get("PAY_BKASH", ""))
    pay_nagad: str = field(default_factory=lambda: os.environ.get("PAY_NAGAD", ""))
    pay_rocket: str = field(default_factory=lambda: os.environ.get("PAY_ROCKET", ""))
    pay_bank: str = field(default_factory=lambda: os.environ.get("PAY_BANK", ""))
    pay_gpay: str = field(default_factory=lambda: os.environ.get("PAY_GPAY", ""))

    # Optional one-tap checkout URLs. Set one and that rail becomes a button
    # that opens a real payment page instead of instructions to copy a number.
    # This is where a bKash merchant Payment Link, an SSLCommerz or aamarPay
    # link, a PayPal.me or a Stripe payment link goes once you have one.
    pay_bkash_link: str = field(default_factory=lambda: os.environ.get("PAY_BKASH_LINK", ""))
    pay_nagad_link: str = field(default_factory=lambda: os.environ.get("PAY_NAGAD_LINK", ""))
    pay_rocket_link: str = field(default_factory=lambda: os.environ.get("PAY_ROCKET_LINK", ""))
    pay_bank_link: str = field(default_factory=lambda: os.environ.get("PAY_BANK_LINK", ""))
    pay_gpay_link: str = field(default_factory=lambda: os.environ.get("PAY_GPAY_LINK", ""))

    @property
    def plans(self) -> dict:
        return {
            "monthly": {"id": "monthly", "label": "Monthly", "price": self.price_monthly,
                        "currency": self.currency, "days": 30},
            "yearly": {"id": "yearly", "label": "Yearly", "price": self.price_yearly,
                       "currency": self.currency, "days": 365,
                       "note": f"Save {max(0, 100 - round(self.price_yearly * 100 / max(1, self.price_monthly * 12)))}%"},
        }

    @property
    def pay_accounts(self) -> dict:
        return {k: v for k, v in {
            "bkash": self.pay_bkash, "nagad": self.pay_nagad, "rocket": self.pay_rocket,
            "bank": self.pay_bank, "gpay": self.pay_gpay,
        }.items() if v}

    @property
    def pay_links(self) -> dict:
        """Only http(s) links are passed to the browser.

        These are rendered as a link the visitor taps, so anything else — a
        javascript: or data: URL slipped into the environment — would run in
        their page rather than open a checkout.
        """
        raw = {
            "bkash": self.pay_bkash_link, "nagad": self.pay_nagad_link,
            "rocket": self.pay_rocket_link, "bank": self.pay_bank_link,
            "gpay": self.pay_gpay_link,
        }
        return {k: v.strip() for k, v in raw.items()
                if v.strip().lower().startswith(("http://", "https://"))}

    # --- Storage --------------------------------------------------------
    google_client_id: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    google_refresh_token: str = field(default_factory=lambda: os.environ.get("GOOGLE_REFRESH_TOKEN", ""))
    drive_folder_name: str = field(default_factory=lambda: os.environ.get("DRIVE_FOLDER_NAME", "Mindora App Data"))
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
