"""
macOS local agent. Launches apps and shows notifications on this machine,
driven by commands queued in the cloud orchestrator.

Install:  pip install requests
Run:      python3 macos_agent.py --server http://your-cloud-host:8000 --key change-me
"""
import subprocess

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


if __name__ == "__main__":
    args = cli_args()
    MacAgent(args.server, args.key, args.name).run()
