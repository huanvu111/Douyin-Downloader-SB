"""Giao diện: chạy bằng lệnh  python3 -m streamlit run app.py"""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

import streamlit as st
from downloader import (extract_link, extract_links, login_browser, download_channel,
                        download_batch, download_root, choose_folder)
from notifier import notify
from watcher import (load_config, save_config, load_watchlist, save_watchlist,
                     add_channel, check_all, remember_channel, display_name)

st.set_page_config(page_title="Tải video Douyin", page_icon="🎬")


# ---------- Tự quét khi mở app (chạy nền, không làm đứng giao diện) ----------
@st.cache_resource
def scan_state() -> dict:
    return {"running": False, "log": [], "new": []}


SCAN = scan_state()


def run_scan() -> None:
    SCAN.update(running=True, log=[], new=[])

    def log(msg: str) -> None:
        if msg.strip().startswith("NEW|"):
            _, name, rest = msg.strip().split("|", 2)
            desc, link = rest.rsplit("|", 1)
            SCAN["new"].append({"name": name, "desc": desc, "link": link})
        else:
            SCAN["log"].append(msg)

    try:
        check_all(log=log)
    except Exception as e:
        SCAN["log"].append(f"Lỗi: {e}")
    finally:
        SCAN["running"] = False
        config = load_config()
        config["last_check"] = time.time()
        save_config(config)


def start_scan() -> bool:
    if SCAN["running"] or not load_watchlist():
        return False
    threading.Thread(target=run_scan, daemon=True).start()
    return True


DEFAULT_TIMES = ["08:00", "20:00"]


def scan_times() -> list[str]:
    return load_config().get("scan_times") or DEFAULT_TIMES


def _slots(now: datetime) -> list[datetime]:
    """Các mốc giờ quét của hôm qua, hôm nay và ngày mai."""
    slots = []
    for d in (-1, 0, 1):
        day = (now + timedelta(days=d)).date()
        for t in scan_times():
            hh, mm = map(int, t.split(":"))
            slots.append(datetime.combine(day, dtime(hh, mm)))
    return sorted(slots)


def scan_due() -> bool:
    """Đến giờ quét chưa: có mốc giờ nào đã qua mà chưa quét không."""
    now = datetime.now()
    passed = [s for s in _slots(now) if s <= now]
    return bool(passed) and load_config().get("last_check", 0) < passed[-1].timestamp()


def next_scan() -> datetime:
    now = datetime.now()
    return next(s for s in _slots(now) if s > now)


@st.cache_resource
def scheduler() -> bool:
    """Chạy nền suốt lúc app mở: mỗi phút kiểm tra xem đã đến giờ quét chưa.
    Nếu lúc đến giờ mà máy tắt, lần mở app tiếp theo sẽ quét bù ngay."""
    def loop() -> None:
        while True:
            try:
                if scan_due():
                    start_scan()
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
    return True


scheduler()


# ---------- Việc tải chạy nền (chuyển trang không mất tiến độ) ----------
@st.cache_resource
def job_state() -> dict:
    return {"running": False, "status": "", "index": 0, "total": 0, "log": [],
            "result": None, "warning": "", "shot": ""}


JOB = job_state()


def run_job(kind: str, stream, follow: bool) -> None:
    JOB.update(running=True, status="Đang mở Chrome...", index=0, total=0, log=[],
               result=None, warning="", shot="")
    try:
        for msg in stream:
            if msg["type"] == "progress":
                if msg.get("stage") == "scan":
                    JOB["status"] = f"Đang lấy danh sách video... đã tìm thấy {msg['found']} video"
                    continue
                JOB["index"], JOB["total"] = msg["index"], msg["total"]
                JOB["status"] = f"Đang tải {msg['index']}/{msg['total']}"
                if kind == "batch":
                    if msg["ok"]:
                        JOB["log"].append(f"✅ {msg['index']}. {msg['title'] or msg['aweme_id']}")
                        if follow and msg.get("sec_uid"):
                            remember_channel(msg["sec_uid"], msg.get("author", ""),
                                             [msg.get("aweme_id", "")], complete=False)
                    else:
                        JOB["log"].append(f"❌ {msg['index']}. {msg['link']} — {msg['message']}")
                else:
                    icon = {"tải xong": "✅", "lỗi": "❌"}.get(msg["status"], "⏭️")
                    JOB["log"].append(f"{icon} {msg['index']}. "
                                      f"{msg['title'] or '(không có tiêu đề)'} — {msg['status']}")
            elif msg["type"] == "result":
                if not msg.get("ok"):
                    JOB["result"] = ("error", msg["message"])
                elif kind == "batch":
                    JOB["result"] = ("success", f"Xong. Tải thành công {msg['done']} video, "
                                     f"lỗi {msg['failed']}. Video lưu trong: {download_root().resolve()}")
                else:
                    remember_channel(msg.get("sec_uid"), msg.get("author"),
                                     msg.get("ids", []), complete=True)
                    JOB["result"] = ("success", f"Xong. Tải mới {msg['done']} video, bỏ qua "
                                     f"{msg['skipped']} video đã có, {msg['images']} bài đăng ảnh, "
                                     f"lỗi {msg['failed']}. Thư mục: {msg['folder']}. "
                                     "Kênh đã được thêm vào danh sách theo dõi.")
                    if msg.get("stopped_early"):
                        JOB["warning"] = ("Douyin ngừng tải thêm danh sách trước khi hết video của "
                                          "kênh, nên có thể chưa lấy đủ. Thường do chưa đăng nhập "
                                          "Douyin, hoặc Douyin hiện ô xác minh. Bấm 'Tải cả kênh' "
                                          "lần nữa: video đã có sẽ được bỏ qua.")
                        JOB["shot"] = msg.get("debug_shot", "")
    except Exception as e:
        JOB["result"] = ("error", f"Lỗi: {e}")
    finally:
        JOB["running"] = False
        JOB["status"] = ""


def start_job(kind: str, stream, follow: bool = False) -> bool:
    if JOB["running"]:
        return False
    JOB["running"] = True
    threading.Thread(target=run_job, args=(kind, stream, follow), daemon=True).start()
    return True


@st.fragment(run_every=1)
def job_panel() -> None:
    if JOB["running"]:
        st.info("Đang tải. Chrome đang làm việc ngầm, nếu thấy cửa sổ Chrome thì đừng đóng nó. "
                "Bạn có thể chuyển sang trang khác, tiến độ vẫn được giữ.")
        st.write(JOB["status"])
        if JOB["total"]:
            st.progress(JOB["index"] / JOB["total"])
    elif JOB["result"]:
        kind, text = JOB["result"]
        (st.success if kind == "success" else st.error)(text)
        if JOB["warning"]:
            st.warning(JOB["warning"])
            if JOB["shot"] and Path(JOB["shot"]).exists():
                st.image(JOB["shot"], caption="Màn hình Chrome lúc dừng")
    if JOB["log"]:
        with st.expander(f"Chi tiết ({len(JOB['log'])} video, mới nhất ở trên)",
                         expanded=True):
            for line in reversed(JOB["log"][-300:]):
                st.write(line)


@st.fragment(run_every=3)
def scan_status() -> None:
    n = len(load_watchlist())
    if JOB["running"]:
        st.write(f"⬇️ {JOB['status']}")
    if SCAN["running"]:
        st.write(f"🔄 Đang quét {n} kênh...")
    else:
        last = load_config().get("last_check")
        st.write(f"Theo dõi {n} kênh")
        if last:
            st.caption(f"Quét gần nhất: {datetime.fromtimestamp(last):%H:%M %d/%m}")
        st.caption(f"Lần quét tới: {next_scan():%H:%M %d/%m}")
    if SCAN["new"]:
        st.success(f"{len(SCAN['new'])} video mới")
        for v in SCAN["new"]:
            st.markdown(f"[{v['name']}: {v['desc'][:40] or 'video'}]({v['link']})")
    if SCAN["log"]:
        with st.expander("Chi tiết lần quét"):
            for line in SCAN["log"]:
                st.text(line)


def folder_bar(key: str) -> None:
    """Hiện thư mục đang lưu video, kèm nút đổi thư mục."""
    c1, c2 = st.columns([4, 1])
    c1.markdown(f"📁 **Lưu vào:** `{download_root().resolve()}`")
    if c2.button("Đổi thư mục", key=f"pick_{key}", disabled=JOB["running"]):
        chosen = choose_folder()
        if chosen:
            config = load_config()
            config["download_dir"] = chosen
            save_config(config)
            st.rerun()
        else:
            st.caption("Chưa chọn thư mục nào. Nếu không thấy hộp thoại, "
                       "nó có thể đang nằm sau cửa sổ trình duyệt.")


page = st.sidebar.radio("Chọn trang", ["Tải video", "Tải cả kênh", "Theo dõi kênh",
                                       "Cài đặt", "Đăng nhập Douyin"])
with st.sidebar:
    st.divider()
    scan_status()
    if st.button("Quét ngay"):
        if not start_scan():
            st.caption("Đang quét rồi, hoặc chưa có kênh nào.")
    st.divider()
    if st.button("Mở thư mục video"):
        folder = download_root().resolve()
        folder.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    if st.button("Tắt app"):
        st.write("App đã tắt. Bạn có thể đóng tab này.")
        threading.Thread(target=lambda: (time.sleep(1), os._exit(0)), daemon=True).start()


# ---------- Các trang ----------
if page == "Đăng nhập Douyin":
    st.title("Đăng nhập Douyin")
    st.write("App dùng một cửa sổ Chrome riêng. Chỉ cần đăng nhập một lần, "
             "app sẽ nhớ cho những lần sau.")
    st.write("Bấm nút bên dưới, một cửa sổ Douyin sẽ mở ra. Bấm đăng nhập và quét mã QR "
             "bằng app Douyin trên điện thoại. **Đăng nhập xong, cửa sổ sẽ tự đóng.** "
             "Nếu bạn đã đăng nhập từ trước, cửa sổ sẽ đóng ngay sau vài giây.")
    if st.button("Đăng nhập Douyin", type="primary", disabled=JOB["running"]):
        with st.spinner("Đang chờ bạn đăng nhập trong cửa sổ Chrome (tối đa 5 phút)..."):
            result = login_browser()
        (st.success if result.get("ok") else st.error)(result["message"])

elif page == "Cài đặt":
    st.title("Cài đặt")
    config = load_config()

    st.subheader("Thông báo trên máy")
    st.write("Khi có video mới, app hiện thông báo ở góc màn hình. "
             "Bấm nút dưới để thử xem máy có hiện thông báo không.")
    if st.button("Gửi thông báo thử"):
        if notify("Douyin Downloader", "Thông báo thử: app đã có thể báo video mới."):
            st.success("Đã gửi. Nếu không thấy thông báo, xem hướng dẫn bên dưới.")
        else:
            st.error("Máy này không hỗ trợ thông báo.")
    with st.expander("Không thấy thông báo?"):
        st.write("**Mac:** vào System Settings → Notifications, tìm **Script Editor** "
                 "(hoặc Douyin Downloader), bật Allow Notifications. Tắt chế độ Focus/"
                 "Không làm phiền nếu đang bật.")
        st.write("**Windows:** vào Settings → System → Notifications, bật thông báo và "
                 "bật cho **Windows PowerShell**. Tắt Focus assist / Do not disturb nếu đang bật.")

    st.subheader("Thư mục lưu video")
    folder_bar("settings")
    typed = st.text_input("Hoặc dán đường dẫn thư mục", value=load_config().get("download_dir", ""),
                          placeholder="Ví dụ: /Users/ten-ban/Movies/Douyin  hoặc  D:\\Video\\Douyin")
    c1, c2 = st.columns(2)
    if c1.button("Lưu đường dẫn"):
        folder = Path(typed.strip()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            config = load_config()
            config["download_dir"] = str(folder)
            save_config(config)
            st.success(f"Đã lưu. Video sẽ được lưu vào {folder}")
        except Exception:
            st.error("Không tạo được thư mục này. Kiểm tra lại đường dẫn.")
    if c2.button("Về thư mục mặc định"):
        config = load_config()
        config.pop("download_dir", None)
        save_config(config)
        st.rerun()

    st.subheader("Cửa sổ Chrome")
    show = st.checkbox("Hiện cửa sổ Chrome khi tải và quét",
                       value=bool(load_config().get("show_browser")),
                       help="Mặc định app giấu cửa sổ Chrome cho đỡ vướng. Bật lên khi tải bị lỗi, "
                            "để xem Douyin có hiện ô xác minh không và tự xử lý.")
    if show != bool(load_config().get("show_browser")):
        config = load_config()
        config["show_browser"] = show
        save_config(config)
        st.toast("Đã lưu")
    st.caption("Trang 'Đăng nhập Douyin' luôn hiện cửa sổ Chrome để bạn quét mã QR.")

    st.subheader("Giờ tự quét")
    st.write("Mỗi ngày app tự quét 2 lần vào giờ bạn chọn (khi app đang mở). "
             "Nếu đến giờ mà máy tắt hoặc app đang đóng, lần mở app tiếp theo sẽ quét bù ngay.")
    times = scan_times()
    c1, c2 = st.columns(2)
    t1 = c1.time_input("Lần 1", value=datetime.strptime(times[0], "%H:%M").time(), step=1800)
    t2 = c2.time_input("Lần 2", value=datetime.strptime(times[-1], "%H:%M").time(), step=1800)
    if st.button("Lưu giờ quét"):
        config = load_config()
        config["scan_times"] = sorted({t1.strftime("%H:%M"), t2.strftime("%H:%M")})
        save_config(config)
        st.success(f"Đã lưu. App sẽ quét lúc {' và '.join(config['scan_times'])} mỗi ngày.")

elif page == "Theo dõi kênh":
    st.title("Theo dõi kênh")
    st.write("Kênh của mọi video bạn tải sẽ tự được thêm vào đây. "
             "Bạn cũng có thể thêm kênh bằng tay.")
    with st.expander("➕ Thêm kênh bằng tay"):
        text = st.text_input("Link kênh")
        alias = st.text_input("Đặt tên cho kênh (không bắt buộc)",
                              placeholder="Ví dụ: Kênh mèo hài")
        if st.button("Thêm kênh"):
            link = extract_link(text or "")
            if not link:
                st.error("Không thấy link Douyin trong đoạn bạn dán.")
            else:
                with st.spinner("Đang đọc danh sách video hiện có của kênh..."):
                    result = add_channel(link, False, alias)
                (st.success if result.get("ok") else st.error)(result["message"])

    channels = load_watchlist()
    st.subheader(f"Đang theo dõi {len(channels)} kênh")
    changed = False
    for i, ch in enumerate(channels):
        with st.container(border=True):
            st.markdown(f"**[{display_name(ch)}]({ch['link']})**"
                        + (f"  ·  tên Douyin: {ch['name']}" if ch.get("alias") else ""))
            new_alias = st.text_input("Tên hiển thị trong thông báo", value=ch.get("alias", ""),
                                      placeholder=ch.get("name") or "Đặt tên cho kênh",
                                      key=f"alias{i}")
            c1, c2, c3 = st.columns([2, 2, 1])
            new_notify = c1.checkbox("🔔 Thông báo", value=ch.get("notify", True), key=f"noti{i}")
            new_auto = c2.checkbox("⬇️ Tự tải video mới", value=ch.get("auto_download", False),
                                   key=f"auto{i}")
            if (new_alias.strip(), new_notify, new_auto) != (ch.get("alias", ""),
                                                             ch.get("notify", True),
                                                             ch.get("auto_download", False)):
                ch.update(alias=new_alias.strip(), notify=new_notify, auto_download=new_auto)
                changed = True
            if c3.button("Xóa", key=f"del{i}"):
                channels.pop(i)
                save_watchlist(channels)
                st.rerun()
    if changed:
        save_watchlist(channels)
        st.toast("Đã lưu thay đổi")

elif page == "Tải cả kênh":
    st.title("Tải cả kênh")
    text = st.text_input("Dán link kênh Douyin",
                         placeholder="https://www.douyin.com/user/... hoặc link chia sẻ kênh")
    limit = st.number_input("Số video tối đa (0 = tải tất cả)", min_value=0, value=0, step=1,
                            help="Lần đầu nên thử 3–5 video để kiểm tra trước.")

    folder_bar("channel")
    st.caption("Mỗi kênh được lưu vào một thư mục con mang tên kênh.")
    if st.button("Tải cả kênh", type="primary", disabled=JOB["running"]):
        link = extract_link(text or "")
        if not link:
            st.error("Không thấy link Douyin trong đoạn bạn dán. Link phải chứa douyin.com.")
        elif not start_job("channel", download_channel(link, int(limit))):
            st.warning("Đang có một lượt tải khác chạy. Chờ xong rồi thử lại.")
    job_panel()

else:
    st.title("Tải video")
    text = st.text_area("Dán một hoặc nhiều link Douyin", height=200,
                        placeholder="Dán thẳng đoạn chia sẻ từ Douyin, kể cả tiêu đề, hashtag... "
                                    "Mỗi link một dòng hoặc dán liền nhau đều được, app tự lọc.")
    links = extract_links(text)
    if text.strip():
        if links:
            with st.expander(f"Tìm thấy {len(links)} link", expanded=len(links) <= 5):
                for i, l in enumerate(links, 1):
                    st.text(f"{i}. {l}")
        else:
            st.warning("Không tìm thấy link Douyin nào trong đoạn bạn dán.")
    follow = st.checkbox("Theo dõi kênh của các video này", value=True)
    folder_bar("batch")

    if st.button("Tải video", type="primary", disabled=not links or JOB["running"]):
        if not start_job("batch", download_batch(links), follow):
            st.warning("Đang có một lượt tải khác chạy. Chờ xong rồi thử lại.")
    job_panel()
