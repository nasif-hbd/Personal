"""
Shared polling loop for the local per-OS agents.

Each OS agent subclasses AgentBase and implements the OS-specific primitives
(launch_app, notify, get_clipboard, set_clipboard, speak, screenshot). The
loop itself — register, poll, dispatch, ack — and the two OS-agnostic
commands (write_file, run_command) live here once, not duplicated per OS.

Command dispatch is a pluggable registry (register_command), not a fixed
if/elif chain — adding a new command type to a running agent is one call,
not a new branch here.

write_file and run_command are opt-in (--allow-write / --allow-shell) because
they're the two commands that can do real damage: writing to an arbitrary
path or running an arbitrary shell command. Everything else (launch/notify/
clipboard/speak/screenshot) is on by default.

Install: pip install requests
"""
from __future__ import annotations

import base64
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable


class AgentBase:
    os_name = "generic"

    def __init__(
        self,
        server_url: str,
        api_key: str,
        agent_name: str | None = None,
        poll_interval: int = 5,
        tags: str = "",
        allow_shell: bool = False,
        allow_write: bool = False,
    ):
        if not api_key:
            raise SystemExit("Missing API key — pass --key or set AGENT_API_KEY")
        import requests  # local import: only agent modes need this dependency

        self.server_url = server_url.rstrip("/")
        self.agent_id = f"{self.os_name}-{uuid.getnode():x}"
        self.agent_name = agent_name or platform.node()
        self.poll_interval = poll_interval
        self.tags = tags
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
        self._requests = requests

        self.commands: dict[str, Callable[[dict], str]] = {}
        self.register_command("launch_app", lambda p: self.launch_app(p["app_name"], p.get("args")))
        self.register_command("notify", lambda p: self.notify(p.get("title", "Notice"), p.get("message", "")))
        self.register_command("get_clipboard", lambda p: self.get_clipboard())
        self.register_command("set_clipboard", lambda p: self.set_clipboard(p["text"]))
        self.register_command("speak", lambda p: self.speak(p["text"]))
        self.register_command("screenshot", lambda p: self.screenshot())
        if allow_write:
            self.register_command("write_file", lambda p: self.write_file(p["path"], p["content_base64"]))
        if allow_shell:
            self.register_command("run_command", lambda p: self.run_command(p["command"]))

    def register_command(self, name: str, handler: Callable[[dict], str]) -> None:
        """Extend the dispatch table at runtime — a subclass or a caller of
        run() can add new command types without touching dispatch()."""
        self.commands[name] = handler

    def register(self) -> None:
        resp = self.session.post(
            f"{self.server_url}/agents/register",
            json={"agent_id": self.agent_id, "os": self.os_name, "name": self.agent_name, "tags": self.tags},
            timeout=10,
        )
        resp.raise_for_status()

    # ---- OS-specific primitives — subclasses implement these -----------

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        raise NotImplementedError

    def notify(self, title: str, message: str) -> str:
        raise NotImplementedError

    def get_clipboard(self) -> str:
        raise NotImplementedError

    def set_clipboard(self, text: str) -> str:
        raise NotImplementedError

    def speak(self, text: str) -> str:
        raise NotImplementedError

    def screenshot(self) -> str:
        raise NotImplementedError

    # ---- OS-agnostic, opt-in commands ------------------------------------

    def write_file(self, path: str, content_base64: str) -> str:
        data = base64.b64decode(content_base64)
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"wrote {len(data)} bytes to {target}"

    def run_command(self, command: str) -> str:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        output = (result.stdout or "") + (result.stderr or "")
        return f"exit={result.returncode}: {output[:2000]}"

    def _upload_file(self, path: str, content_type: str) -> int:
        with open(path, "rb") as f:
            data = f.read()
        resp = self.session.post(
            f"{self.server_url}/files/upload",
            json={
                "agent_id": self.agent_id,
                "filename": os.path.basename(path),
                "content_type": content_type,
                "content_base64": base64.b64encode(data).decode(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["file_id"]

    # ---- Poll loop ---------------------------------------------------------

    def dispatch(self, command: dict) -> dict:
        ctype = command["type"]
        handler = self.commands.get(ctype)
        if not handler:
            return {"status": "failed", "result": f"unsupported command type: {ctype}"}
        try:
            return {"status": "done", "result": handler(command["payload"])}
        except Exception as exc:  # tool execution should never crash the poll loop
            return {"status": "failed", "result": str(exc)}

    def run(self) -> None:
        self.register()
        print(f"[{self.agent_id}] registered as '{self.agent_name}' (tags: {self.tags or 'none'}), polling {self.server_url}")
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
            except self._requests.RequestException as exc:
                print(f"[{self.agent_id}] poll error: {exc}")
                time.sleep(self.poll_interval)


def cli_args():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=os.environ.get("AGENT_SERVER_URL", "http://localhost:8000"))
    parser.add_argument("--key", default=os.environ.get("AGENT_API_KEY", ""))
    parser.add_argument("--name", default=None)
    parser.add_argument("--tags", default="", help="comma-separated tags, e.g. home,laptop")
    parser.add_argument(
        "--allow-write", action="store_true",
        help="enable the write_file command (can write to any path this process can reach — off by default)",
    )
    parser.add_argument(
        "--allow-shell", action="store_true",
        help="enable the run_command command (arbitrary shell execution — off by default)",
    )
    return parser.parse_args()
