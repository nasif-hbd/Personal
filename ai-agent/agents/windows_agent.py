"""
Windows local agent. Launches apps and shows toast notifications on this
machine, driven by commands queued in the cloud orchestrator.

Install:  pip install requests
Run:      python windows_agent.py --server http://your-cloud-host:8000 --key change-me
"""
import os
import subprocess

from base_agent import AgentBase, cli_args


class WindowsAgent(AgentBase):
    os_name = "windows"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name can be an .exe path, a Start-Menu-resolvable name (e.g. "notepad"),
        # or a URI (e.g. "ms-settings:", "spotify:", any registered app protocol).
        if args:
            subprocess.Popen([app_name, *args])
        else:
            os.startfile(app_name)  # type: ignore[attr-defined]  (Windows-only builtin)
        return f"launched {app_name}"

    def notify(self, title: str, message: str) -> str:
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType=WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::"
            "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$texts = $template.GetElementsByTagName('text');"
            f"$texts.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null;"
            f"$texts.Item(1).AppendChild($template.CreateTextNode('{message}')) > $null;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AI Agent').Show($toast)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        return "notification shown"


if __name__ == "__main__":
    args = cli_args()
    WindowsAgent(args.server, args.key, args.name).run()
