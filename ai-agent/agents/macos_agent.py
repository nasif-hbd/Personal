"""
macOS local agent. Launches apps, shows notifications, reads/writes the
clipboard, speaks text, and captures screenshots — all driven by commands
queued in the cloud orchestrator.

Install:  pip install requests
Run:      python3 macos_agent.py --server http://your-cloud-host:8000 --key change-me --tags home,laptop
"""
import os
import subprocess
import uuid

from base_agent import AgentBase, cli_args


class MacAgent(AgentBase):
    os_name = "macos"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name is the .app's display name, e.g. "Safari", "Notes", "Spotify".
        cmd = ["open", "-a", app_name]
        if args:
            cmd += ["--args", *args]
        subprocess.run(cmd, check=True)
        return f"launched {app_name}"

    def notify(self, title: str, message: str) -> str:
        # AppleScript strings: escape embedded double quotes and backslashes.
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(message)}" with title "{esc(title)}"'
        subprocess.run(["osascript", "-e", script], check=True)
        return "notification shown"

    def get_clipboard(self) -> str:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
        return result.stdout

    def set_clipboard(self, text: str) -> str:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return "clipboard set"

    def speak(self, text: str) -> str:
        subprocess.run(["say", text], check=True)
        return "spoke text"

    def screenshot(self) -> str:
        path = f"/tmp/shot_{uuid.uuid4().hex}.png"
        subprocess.run(["screencapture", "-x", path], check=True)
        file_id = self._upload_file(path, "image/png")
        os.remove(path)
        return f"uploaded screenshot as file {file_id}"


if __name__ == "__main__":
    args = cli_args()
    MacAgent(
        args.server, args.key, args.name,
        tags=args.tags, allow_shell=args.allow_shell, allow_write=args.allow_write,
    ).run()
