#!/usr/bin/env python3
"""
Screen Time Limiter — a minimal Digital Wellbeing-style app time capper.

Set a daily time limit (in minutes) for any app by process name. Once an
app's usage for the day reaches its limit, the limiter closes it. Limits
can only be changed up to 3 times per rolling 7-day window per app, so you
can't just keep raising the limit to dodge it.

Requires: pip install psutil

Usage:
    python screentime_limiter.py set-limit chrome.exe 60
    python screentime_limiter.py status
    python screentime_limiter.py monitor
"""

import argparse
import json
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import psutil

DATA_FILE = Path(__file__).with_name("screentime_data.json")
MAX_CHANGES_PER_WEEK = 3
CHANGE_WINDOW_DAYS = 7
POLL_INTERVAL_SECONDS = 10


class LimitChangeBlocked(Exception):
    pass


def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"apps": {}}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _recent_changes(change_log):
    cutoff = datetime.now() - timedelta(days=CHANGE_WINDOW_DAYS)
    return [t for t in change_log if datetime.fromisoformat(t) > cutoff]


def set_limit(data, app_name, minutes):
    entry = data["apps"].setdefault(
        app_name, {"daily_limit_minutes": None, "usage": {}, "limit_change_log": []}
    )

    recent_changes = _recent_changes(entry["limit_change_log"])
    if len(recent_changes) >= MAX_CHANGES_PER_WEEK:
        next_allowed = datetime.fromisoformat(recent_changes[0]) + timedelta(days=CHANGE_WINDOW_DAYS)
        raise LimitChangeBlocked(
            f"'{app_name}' limit already changed {MAX_CHANGES_PER_WEEK} times in the last "
            f"{CHANGE_WINDOW_DAYS} days. Next change allowed after {next_allowed:%Y-%m-%d %H:%M}."
        )

    entry["daily_limit_minutes"] = minutes
    recent_changes.append(datetime.now().isoformat())
    entry["limit_change_log"] = recent_changes
    save_data(data)


def status(data):
    if not data["apps"]:
        print("No apps configured yet.")
        return

    today = date.today().isoformat()
    for app_name, entry in data["apps"].items():
        limit = entry["daily_limit_minutes"]
        used_seconds = entry["usage"].get(today, 0)
        changes_left = MAX_CHANGES_PER_WEEK - len(_recent_changes(entry["limit_change_log"]))
        print(
            f"{app_name}: limit={limit} min, used today={used_seconds // 60} min, "
            f"limit changes left this week={changes_left}"
        )


def _running_process_names():
    return {p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]}


def _kill_app(app_name):
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] == app_name:
            try:
                proc.terminate()
            except psutil.Error:
                pass


def monitor(data, poll_interval=POLL_INTERVAL_SECONDS):
    print(f"Monitoring {len(data['apps'])} app(s). Press Ctrl+C to stop.")
    try:
        while True:
            today = date.today().isoformat()
            running = _running_process_names()

            for app_name, entry in data["apps"].items():
                if entry["daily_limit_minutes"] is None:
                    continue

                used_seconds = entry["usage"].get(today, 0)
                if app_name in running:
                    used_seconds += poll_interval
                    entry["usage"][today] = used_seconds

                limit_seconds = entry["daily_limit_minutes"] * 60
                if used_seconds >= limit_seconds and app_name in running:
                    print(f"[limit reached] closing '{app_name}'")
                    _kill_app(app_name)

            save_data(data)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nStopped monitoring.")


def main():
    parser = argparse.ArgumentParser(description="Limit daily screen time for apps.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_limit_parser = subparsers.add_parser("set-limit", help="Set an app's daily time limit")
    set_limit_parser.add_argument("app_name", help="Process name, e.g. chrome.exe or Discord")
    set_limit_parser.add_argument("minutes", type=int, help="Daily limit in minutes")

    subparsers.add_parser("status", help="Show configured limits and today's usage")
    subparsers.add_parser("monitor", help="Start monitoring and enforcing limits")

    args = parser.parse_args()
    data = load_data()

    if args.command == "set-limit":
        try:
            set_limit(data, args.app_name, args.minutes)
            print(f"Set '{args.app_name}' limit to {args.minutes} minutes/day.")
        except LimitChangeBlocked as e:
            print(f"Blocked: {e}")
    elif args.command == "status":
        status(data)
    elif args.command == "monitor":
        monitor(data)


if __name__ == "__main__":
    main()
