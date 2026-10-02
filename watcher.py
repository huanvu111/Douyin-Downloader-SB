"""Theo dõi kênh Douyin, báo video mới bằng thông báo trên máy.
Chạy liên tục:  python3 watcher.py
"""
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

from downloader import get_latest, download_single, download_root
from notifier import notify

CONFIG_FILE = Path("config.json")
WATCH_FILE = Path("watchlist.json")


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    return load_json(CONFIG_FILE, {"interval_minutes": 30})


def save_config(config: dict) -> None:
    save_json(CONFIG_FILE, config)


def load_watchlist() -> list:
    return load_json(WATCH_FILE, [])


def save_watchlist(channels: list) -> None:
    save_json(WATCH_FILE, channels)


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r]', "_", name or "").strip()
    return name[:80] or "kenh_khong_ten"


def display_name(ch: dict) -> str:
    """Tên hiển thị: tên bạn tự đặt, nếu không có thì dùng tên kênh trên Douyin."""
    return (ch.get("alias") or "").strip() or ch.get("name") or "Kênh"


def add_channel(link: str, auto_download: bool, alias: str = "") -> dict:
    """Thêm kênh. Video hiện có được ghi nhận là 'đã biết' để không báo nhầm."""
    if any(c["link"] == link for c in load_watchlist()):
        return {"ok": False, "message": "Kênh này đã có trong danh sách theo dõi."}
    res = get_latest(link)
    if not res.get("ok"):
        return res
    channels = load_watchlist()
    channels.append({"link": link, "name": res["author"], "alias": alias.strip(),
                     "notify": True, "auto_download": auto_download,
                     "known_ids": [it["id"] for it in res["items"]]})
    save_watchlist(channels)
    return {"ok": True, "message": f"Đã thêm kênh {alias.strip() or res['author']}."}


def remember_channel(sec_uid: str, name: str, ids: list, complete: bool) -> None:
    """Tự lưu kênh khi bạn tải video. complete=True nghĩa là 'ids' là toàn bộ video hiện có."""
    if not sec_uid:
        return
    link = f"https://www.douyin.com/user/{sec_uid}"
    channels = load_watchlist()
    ch = next((c for c in channels if c.get("sec_uid") == sec_uid or c["link"] == link), None)
    if ch is None:
        ch = {"link": link, "name": name, "alias": "", "notify": True, "sec_uid": sec_uid,
              "auto_download": False, "known_ids": [], "initialized": False}
        channels.append(ch)
    ch["name"] = name or ch["name"]
    ch["known_ids"] = list(dict.fromkeys(ch["known_ids"] + [str(i) for i in ids]))[-500:]
    if complete:
        ch["initialized"] = True
    save_watchlist(channels)


def check_all(log=print) -> None:
    channels = load_watchlist()

    for ch in channels:
        name = display_name(ch)
        log(f"Kiểm tra kênh {name}...")
        res = get_latest(ch["link"])
        if not res.get("ok"):
            log(f"  Lỗi: {res.get('message')}")
            continue

        if not ch.get("initialized", True):
            # Lần đầu quét kênh vừa được tự lưu: chỉ ghi nhận video hiện có, không báo
            ch["known_ids"] = list(dict.fromkeys(ch["known_ids"] + [it["id"] for it in res["items"]]))
            ch["initialized"] = True
            save_watchlist(channels)
            log(f"  Đã ghi nhận {len(res['items'])} video hiện có. Từ lần sau sẽ báo video mới.")
            continue

        new_items = [it for it in res["items"] if it["id"] not in ch["known_ids"]]
        if not new_items:
            log("  Không có video mới.")
        downloaded = 0
        for it in new_items:
            video_link = f"https://www.douyin.com/video/{it['id']}"
            log(f"  Video mới: {it['desc'] or it['id']}")
            if ch.get("auto_download") and not it["is_image"]:
                folder = str(download_root() / safe_name(name))
                dl = download_single(video_link, folder)
                downloaded += 1 if dl.get("ok") else 0
                log("  " + ("Đã tải." if dl.get("ok") else "Tải thất bại."))
            ch["known_ids"].append(it["id"])
            log(f"  NEW|{name}|{it['desc']}|{video_link}")

        if new_items and ch.get("notify", True):
            if len(new_items) == 1:
                body = new_items[0]["desc"] or "Có 1 video mới"
            else:
                body = f"Có {len(new_items)} video mới"
            if downloaded:
                body += f" (đã tự tải {downloaded})"
            notify(f"📢 {name}", body)

        ch["known_ids"] = ch["known_ids"][-500:]
        save_watchlist(channels)
        time.sleep(random.randint(5, 15))   # nghỉ giữa các kênh


def main() -> None:
    print("Bộ theo dõi đang chạy. Muốn dừng: giữ phím control rồi nhấn C.")
    while True:
        config = load_config()
        print(f"\n[{datetime.now():%H:%M}] Bắt đầu kiểm tra")
        try:
            check_all()
        except Exception as e:
            print(f"Lỗi: {e}")
        minutes = max(10, int(config.get("interval_minutes", 30)))
        wait = minutes * 60 + random.randint(0, 300)
        print(f"Lần kiểm tra tiếp theo sau khoảng {wait // 60} phút.")
        time.sleep(wait)


if __name__ == "__main__":
    main()
