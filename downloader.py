"""Gọi browser_worker.py và đọc kết quả."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows: không bật cửa sổ đen cho tiến trình phụ
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _worker_cmd(args: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):          # đang chạy dạng app đã đóng gói
        return [sys.executable, "worker", *args]
    return [sys.executable, str(Path(__file__).resolve().with_name("browser_worker.py")), *args]


LINK_RE = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)*douyin\.com/[A-Za-z0-9_\-./?=&%#]*",
                     re.IGNORECASE)


def extract_links(text: str) -> list[str]:
    """Lọc tất cả link Douyin ra khỏi đoạn chữ lộn xộn (tiêu đề, hashtag, mã...)."""
    links = []
    for raw in LINK_RE.findall(text or ""):
        link = raw.rstrip(".,;:!?)]}>")
        if not link.lower().startswith("http"):
            link = "https://" + link
        if link not in links:
            links.append(link)
    return links


def extract_link(text: str) -> str | None:
    """Lấy link Douyin đầu tiên trong đoạn chữ."""
    links = extract_links(text)
    return links[0] if links else None


def _run(args: list[str], timeout: int) -> dict:
    try:
        proc = subprocess.run(_worker_cmd(args), capture_output=True, text=True,
                              encoding="utf-8", errors="ignore", timeout=timeout,
                              creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Quá thời gian chờ. Thử lại."}
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    return {"ok": False, "message": "Lỗi không rõ:\n\n" + (proc.stderr or proc.stdout)[-1500:]}


def login_browser() -> dict:
    return _run(["login"], timeout=1800)


def download_single(link: str, folder: str | None = None) -> dict:
    return _run(["download", link] + ([folder] if folder else []), timeout=900)


def get_latest(link: str) -> dict:
    """Lấy danh sách video mới nhất của một kênh."""
    return _run(["latest", link], timeout=900)


def _stream(args: list[str]):
    proc = subprocess.Popen(_worker_cmd(args), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="ignore", creationflags=NO_WINDOW)
    tail, got_result = [], False
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line.startswith("PROGRESS:"):
            yield {"type": "progress", **json.loads(line[len("PROGRESS:"):])}
        elif line.startswith("RESULT:"):
            got_result = True
            yield {"type": "result", **json.loads(line[len("RESULT:"):])}
        else:
            tail = (tail + [line])[-30:]
    proc.wait()
    if not got_result:
        yield {"type": "result", "ok": False, "message": "Lỗi không rõ:\n\n" + "\n".join(tail)}


def download_batch(links: list[str]):
    """Tải nhiều video. Trả về từng thông báo tiến độ, cuối cùng là kết quả."""
    yield from _stream(["batch", json.dumps(links)])


def download_channel(link: str, limit: int = 0):
    """Tải cả kênh. Trả về từng thông báo tiến độ, cuối cùng là kết quả."""
    yield from _stream(["channel", link, str(limit)])


def download_root() -> Path:
    """Thư mục lưu video: thư mục bạn chọn trong app, mặc định là 'downloads'."""
    try:
        folder = json.loads(Path("config.json").read_text(encoding="utf-8")).get("download_dir")
    except Exception:
        folder = None
    return Path(folder).expanduser() if folder else Path("downloads")


_WIN_PICKER = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Chon thu muc luu video'
if ($d.ShowDialog($owner) -eq 'OK') { Write-Output $d.SelectedPath }
"""


def choose_folder() -> str | None:
    """Mở hộp thoại chọn thư mục của hệ điều hành. Trả về đường dẫn, hoặc None nếu bấm Hủy."""
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(
                ["osascript", "-e", "activate",
                 "-e", 'POSIX path of (choose folder with prompt "Chọn thư mục lưu video")'],
                capture_output=True, text=True, encoding="utf-8", timeout=600)
        elif os.name == "nt":
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                 "-Command", _WIN_PICKER],
                capture_output=True, text=True, encoding="utf-8", timeout=600,
                creationflags=NO_WINDOW)
        else:
            return None
    except Exception:
        return None
    path = (proc.stdout or "").strip()
    return path or None
