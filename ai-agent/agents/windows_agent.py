"""
Windows local agent. Launches apps, shows toast notifications, reads/writes
the clipboard, speaks text, and captures screenshots — all driven by commands
queued in the cloud orchestrator.

Install:  pip install requests
Run:      python windows_agent.py --server http://your-cloud-host:8000 --key change-me --tags home,office
"""
import os
import subprocess
import uuid

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

    def get_clipboard(self) -> str:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    def set_clipboard(self, text: str) -> str:
        subprocess.run(["clip"], input=text, text=True, check=True)
        return "clipboard set"

    def speak(self, text: str) -> str:
        escaped = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer)."
            f"Speak('{escaped}')"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        return "spoke text"

    def screenshot(self) -> str:
        path = os.path.join(os.environ.get("TEMP", "."), f"shot_{uuid.uuid4().hex}.png")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height;"
            "$g = [System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size);"
            f"$bmp.Save('{path}', [System.Drawing.Imaging.ImageFormat]::Png)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        file_id = self._upload_file(path, "image/png")
        os.remove(path)
        return f"uploaded screenshot as file {file_id}"


if __name__ == "__main__":
    args = cli_args()
    WindowsAgent(
        args.server, args.key, args.name,
        tags=args.tags, allow_shell=args.allow_shell, allow_write=args.allow_write,
    ).run()
