"""
Android local agent — runs inside Termux (https://termux.dev), driven by
commands queued in the cloud orchestrator. Requires the Termux:API companion
app (F-Droid) for notifications, clipboard, and text-to-speech.

Install (inside Termux):
    pkg install python
    pip install requests
    termux-setup-storage   # only needed once, if you'll touch the filesystem

Run:
    python android_agent.py --server http://your-cloud-host:8000 --key change-me --tags mobile

Keep it alive in the background with `termux-wake-lock`, or run it as a
Termux:Boot script so it restarts after a reboot.
"""
import os
import subprocess
import uuid

from base_agent import AgentBase, cli_args


class AndroidAgent(AgentBase):
    os_name = "android"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name is the Android package name, e.g. "com.whatsapp", "com.spotify.music".
        subprocess.run(
            ["monkey", "-p", app_name, "-c", "android.intent.category.LAUNCHER", "1"],
            check=True,
        )
        return f"launched package {app_name}"

    def notify(self, title: str, message: str) -> str:
        subprocess.run(["termux-notification", "--title", title, "--content", message], check=True)
        return "notification shown"

    def get_clipboard(self) -> str:
        result = subprocess.run(["termux-clipboard-get"], capture_output=True, text=True, check=True)
        return result.stdout

    def set_clipboard(self, text: str) -> str:
        subprocess.run(["termux-clipboard-set"], input=text, text=True, check=True)
        return "clipboard set"

    def speak(self, text: str) -> str:
        subprocess.run(["termux-tts-speak", text], check=True)
        return "spoke text"

    def screenshot(self) -> str:
        # Plain, non-root Termux cannot capture the screen — `screencap` needs
        # root. Documented here rather than silently failing or faking success.
        path = f"/data/data/com.termux/files/usr/tmp/shot_{uuid.uuid4().hex}.png"
        try:
            subprocess.run(["screencap", "-p", path], check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "screenshot requires a rooted device with 'screencap' on PATH — "
                "not available on a standard, non-root Termux install"
            ) from exc
        file_id = self._upload_file(path, "image/png")
        os.remove(path)
        return f"uploaded screenshot as file {file_id}"


if __name__ == "__main__":
    args = cli_args()
    AndroidAgent(
        args.server, args.key, args.name,
        tags=args.tags, allow_shell=args.allow_shell, allow_write=args.allow_write,
    ).run()
