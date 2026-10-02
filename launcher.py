"""Điểm khởi động của app khi đóng gói (PyInstaller)."""
import os
import runpy
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = 8501
URL = f"http://localhost:{PORT}"


def resource_dir() -> Path:
    """Nơi chứa code của app (bên trong gói ứng dụng)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def data_dir() -> Path:
    """Nơi lưu video, đăng nhập, cài đặt: thư mục DouyinDownloader trong thư mục người dùng."""
    d = Path.home() / "DouyinDownloader"
    d.mkdir(exist_ok=True)
    return d


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main() -> None:
    os.chdir(data_dir())

    # App gọi lại chính nó ở chế độ "worker" để điều khiển Chrome
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        sys.argv = ["browser_worker"] + sys.argv[2:]
        runpy.run_module("browser_worker", run_name="__main__")
        return

    # App đang chạy rồi thì chỉ mở lại trình duyệt
    if port_in_use():
        webbrowser.open(URL)
        return

    threading.Thread(target=lambda: (time.sleep(3), webbrowser.open(URL)), daemon=True).start()

    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", str(resource_dir() / "app.py"),
                "--global.developmentMode=false", "--server.headless=true",
                f"--server.port={PORT}", "--server.fileWatcherType=none",
                "--browser.gatherUsageStats=false"]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
