"""
Android local agent — runs inside Termux (https://termux.dev), driven by
commands queued in the cloud orchestrator. Requires the Termux:API companion
app (F-Droid) for notifications.

Install (inside Termux):
    pkg install python
    pip install requests
    termux-setup-storage   # only needed once, if you'll touch the filesystem

Run:
    python android_agent.py --server http://your-cloud-host:8000 --key change-me

Keep it alive in the background with `termux-wake-lock`, or run it as a
Termux:Boot script so it restarts after a reboot.
"""
import subprocess

from base_agent import AgentBase, cli_args


class AndroidAgent(AgentBase):
    os_name = "android"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name is the Android package name, e.g. "com.whatsapp", "com.spotify.music".
        # `monkey` is the most reliable no-root way to launch an app by package name.
        subprocess.run(
            ["monkey", "-p", app_name, "-c", "android.intent.category.LAUNCHER", "1"],
            check=True,
        )
        return f"launched package {app_name}"

    def notify(self, title: str, message: str) -> str:
        subprocess.run(["termux-notification", "--title", title, "--content", message], check=True)
        return "notification shown"


if __name__ == "__main__":
    args = cli_args()
    AndroidAgent(args.server, args.key, args.name).run()
