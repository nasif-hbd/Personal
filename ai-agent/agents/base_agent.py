"""
Shared polling loop for the local per-OS agents.

Each OS agent subclasses AgentBase and implements launch_app() / notify().
The loop itself — register, poll, dispatch, ack — never changes, so adding a
new OS (or a new command type) means writing one small file, not touching
this one.

Install: pip install requests
"""
from __future__ import annotations

import platform
import time
import uuid

import requests


class AgentBase:
    os_name = "generic"

    def __init__(self, server_url: str, api_key: str, agent_name: str | None = None, poll_interval: int = 5):
        if not api_key:
            raise SystemExit("Missing API key — pass --key or set AGENT_API_KEY")
        self.server_url = server_url.rstrip("/")
        self.agent_id = f"{self.os_name}-{uuid.getnode():x}"
        self.agent_name = agent_name or platform.node()
        self.poll_interval = poll_interval
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

    def register(self) -> None:
        resp = self.session.post(
            f"{self.server_url}/agents/register",
            json={"agent_id": self.agent_id, "os": self.os_name, "name": self.agent_name},
            timeout=10,
        )
        resp.raise_for_status()

    # Subclasses implement these.
    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        raise NotImplementedError

    def notify(self, title: str, message: str) -> str:
        raise NotImplementedError

    def dispatch(self, command: dict) -> dict:
        ctype = command["type"]
        payload = command["payload"]
        try:
            if ctype == "launch_app":
                result = self.launch_app(payload["app_name"], payload.get("args"))
            elif ctype == "notify":
                result = self.notify(payload.get("title", "Notice"), payload.get("message", ""))
            else:
                return {"status": "failed", "result": f"unsupported command type: {ctype}"}
            return {"status": "done", "result": result}
        except Exception as exc:  # tool execution should never crash the poll loop
            return {"status": "failed", "result": str(exc)}

    def run(self) -> None:
        self.register()
        print(f"[{self.agent_id}] registered as '{self.agent_name}', polling {self.server_url}")
        while True:
            try:
                resp = self.session.get(f"{self.server_url}/agents/{self.agent_id}/poll", timeout=10)
                resp.raise_for_status()
                command = resp.json().get("command")
                if command:
                    print(f"[{self.agent_id}] running {command['type']} #{command['id']}")
                    outcome = self.dispatch(command)
                    self.session.post(
                        f"{self.server_url}/agents/{self.agent_id}/ack",
                        json={"command_id": command["id"], **outcome},
                        timeout=10,
                    )
                else:
                    time.sleep(self.poll_interval)
            except requests.RequestException as exc:
                print(f"[{self.agent_id}] poll error: {exc}")
                time.sleep(self.poll_interval)


def cli_args():
    import argparse
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=os.environ.get("AGENT_SERVER_URL", "http://localhost:8000"))
    parser.add_argument("--key", default=os.environ.get("AGENT_API_KEY", ""))
    parser.add_argument("--name", default=None)
    return parser.parse_args()
