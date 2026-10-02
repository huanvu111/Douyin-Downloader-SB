"""Hiện thông báo trên máy tính (Mac và Windows), không cần cài thêm thư viện."""
import os
import subprocess
import sys

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

WINDOWS_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:NT_TITLE)
$b = [Security.SecurityElement]::Escape($env:NT_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""

MAC_SCRIPT = [
    "-e", "on run argv",
    "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
    "-e", "end run",
]


def notify(title: str, body: str) -> bool:
    """Hiện một thông báo. Trả về True nếu gửi được."""
    title, body = (title or "")[:100], (body or "")[:250]
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", *MAC_SCRIPT, title, body], timeout=15,
                           capture_output=True)
            return True
        if os.name == "nt":
            env = dict(os.environ, NT_TITLE=title, NT_BODY=body)
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", WINDOWS_SCRIPT], env=env, timeout=30,
                           capture_output=True, creationflags=NO_WINDOW)
            return True
    except Exception:
        pass
    return False


if __name__ == "__main__":
    notify("Douyin Downloader", "Thông báo thử: app đã có thể báo video mới.")
