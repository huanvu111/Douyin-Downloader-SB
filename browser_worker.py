"""Điều khiển Chrome thật để đăng nhập và tải video Douyin.
Chạy riêng:  python3 browser_worker.py login
             python3 browser_worker.py download <link>
             python3 browser_worker.py channel <link> <số_video_tối_đa>
"""
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path("browser_profile").resolve()   # Chrome riêng của app, lưu đăng nhập
DEBUG_SHOT = Path("downloads/loi_gan_nhat.png")   # ảnh chụp khi tải thất bại


def download_root() -> Path:
    """Thư mục lưu video: thư mục bạn chọn trong app, mặc định là 'downloads'."""
    try:
        d = json.loads(Path("config.json").read_text(encoding="utf-8")).get("download_dir")
    except Exception:
        d = None
    return Path(d).expanduser() if d else Path("downloads")
POST_API = "/aweme/v1/web/aweme/post/"            # danh sách video của một kênh

SCROLL_JS = """() => {
    const links = document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]');
    if (links.length) links[links.length - 1].scrollIntoView({block: 'end'});
    window.scrollBy(0, 5000);
    for (const e of document.querySelectorAll('div, main, section')) {
        const oy = getComputedStyle(e).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight + 50) {
            e.scrollTop = e.scrollHeight;
        }
    }
}"""


def emit(kind: str, **data) -> None:
    """Gửi thông tin tiến độ về cho giao diện."""
    print(kind + ":" + json.dumps(data, ensure_ascii=False), flush=True)


def safe_name(name: str | None) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r]', "_", name or "").strip()
    return name[:80] or "kenh_khong_ten"


def show_browser_setting() -> bool:
    """Đọc tùy chọn 'Hiện cửa sổ Chrome' trong config.json (mặc định: giấu)."""
    try:
        return bool(json.loads(Path("config.json").read_text(encoding="utf-8")).get("show_browser"))
    except Exception:
        return False


def open_browser(p, visible: bool | None = None):
    """Mở Chrome (hoặc Edge nếu máy không có Chrome).
    Khi không hiện, cửa sổ được đẩy ra ngoài màn hình, Douyin vẫn thấy như trình duyệt thường."""
    if visible is None:
        visible = show_browser_setting()
    args = ["--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            # giữ trang chạy bình thường dù cửa sổ không được nhìn thấy
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling"]
    if not visible:
        args += ["--window-position=-32000,-32000"]

    last_error = None
    for channel in ("chrome", "msedge"):
        try:
            return p.chromium.launch_persistent_context(
                str(PROFILE_DIR), channel=channel, headless=False,
                viewport={"width": 1280, "height": 800},
                ignore_default_args=["--enable-automation"], args=args,
            )
        except Exception as e:
            last_error = e
            # Chỉ thử trình duyệt khác khi trình duyệt này không có trên máy
            if "not found" not in str(e).lower() and "executable" not in str(e).lower():
                raise
    raise RuntimeError("Không tìm thấy Google Chrome hoặc Microsoft Edge trên máy. "
                       "Hãy cài Google Chrome rồi thử lại.") from last_error


def login(max_wait: int = 300) -> dict:
    """Mở Douyin để đăng nhập. Tự nhận biết khi đăng nhập xong và tự đóng cửa sổ."""
    with sync_playwright() as p:
        ctx = open_browser(p, visible=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def logged_in():
            """True: đã đăng nhập, False: chưa, None: cửa sổ đã bị đóng."""
            try:
                return any(c["name"] == "sessionid" and c["value"]
                           for c in ctx.cookies("https://www.douyin.com"))
            except Exception:
                return None

        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

        if logged_in():
            status = "already"
            time.sleep(2)
        else:
            status = "timeout"
            deadline = time.time() + max_wait
            while time.time() < deadline:
                state = logged_in()
                if state is None:
                    status = "closed"
                    break
                if state:
                    status = "ok"
                    time.sleep(3)      # chờ Douyin lưu xong phiên đăng nhập
                    break
                time.sleep(2)
        try:
            ctx.close()
        except Exception:
            pass

    return {
        "already": {"ok": True, "message": "Bạn đã đăng nhập Douyin sẵn rồi, không cần làm gì thêm."},
        "ok": {"ok": True, "message": "Đăng nhập thành công. App đã lưu lại cho những lần sau."},
        "closed": {"ok": False, "message": "Cửa sổ Chrome đã bị đóng trước khi đăng nhập xong. Thử lại nhé."},
        "timeout": {"ok": False, "message": "Quá 5 phút chưa thấy đăng nhập. Bấm nút để thử lại."},
    }[status]


def find_play_url(data, aweme_id=None):
    """Tìm link video trong một khối JSON bất kỳ (cấu trúc Douyin hay thay đổi)."""
    if isinstance(data, dict):
        if data.get("video") and (aweme_id is None or str(data.get("aweme_id") or data.get("awemeId")) == aweme_id):
            video = data["video"]
            for key in ("play_addr_h264", "play_addr", "playAddr"):
                addr = video.get(key)
                author = data.get("author") or data.get("authorInfo") or {}
                if isinstance(addr, dict) and addr.get("url_list"):
                    return addr["url_list"][0], data.get("desc", ""), author
                if isinstance(addr, list) and addr and isinstance(addr[0], dict) and addr[0].get("src"):
                    return addr[0]["src"], data.get("desc", ""), author
        for value in data.values():
            found = find_play_url(value, aweme_id)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_play_url(value, aweme_id)
            if found:
                return found
    return None


def download_in(ctx, link: str, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or download_root()
    out_dir.mkdir(parents=True, exist_ok=True)
    api_json = []      # cách 1: dữ liệu JSON trang tải về
    media_urls = []    # cách 3: link file video trình phát yêu cầu

    def on_response(response):
        url = response.url
        if "douyin.com/aweme/" in url and "json" in (response.headers.get("content-type") or ""):
            try:
                api_json.append(response.json())
            except Exception:
                pass

    def on_request(request):
        url = request.url
        if request.resource_type == "media" or "douyinvod.com" in url or "/video/tos/" in url:
            if url.startswith("http") and url not in media_urls:
                media_urls.append(url)

    if True:
        page = ctx.new_page()
        try:
            page.on("response", on_response)
            page.on("request", on_request)
            page.goto(link, wait_until="domcontentloaded", timeout=60000)

            video_url, title, aweme_id, author = None, "", None, {}
            for _ in range(40):                    # chờ tối đa khoảng 20 giây
                m = re.search(r"/video/(\d+)", page.url)
                aweme_id = m.group(1) if m else None
                for data in api_json:
                    found = find_play_url(data, aweme_id)
                    if found:
                        video_url, title, author = found
                        break
                if not video_url:                  # cách 2: dữ liệu nhúng sẵn trong trang
                    try:
                        raw = page.eval_on_selector("#RENDER_DATA", "e => e.textContent")
                        found = find_play_url(json.loads(unquote(raw)), aweme_id) if raw else None
                        if found:
                            video_url, title, author = found
                    except Exception:
                        pass
                if not video_url and media_urls:
                    video_url = media_urls[0]
                if video_url:
                    break
                page.wait_for_timeout(500)

            if not video_url:
                page.screenshot(path=str(DEBUG_SHOT))
                return {"ok": False, "message": "Không tìm thấy video trên trang. Nếu lỗi lặp lại, vào Cài đặt bật "
                        "'Hiện cửa sổ Chrome' để xem Douyin có đòi xác minh không. Ảnh chụp "
                        f"màn hình Chrome lúc đó, xem file {DEBUG_SHOT}"}
            if video_url.startswith("//"):
                video_url = "https:" + video_url

            resp = ctx.request.get(video_url, headers={"Referer": "https://www.douyin.com/"},
                                   timeout=300000)
            if not resp.ok:
                page.screenshot(path=str(DEBUG_SHOT))
                return {"ok": False, "message": f"Tải file video thất bại (mã lỗi {resp.status})."}

            name = aweme_id or str(int(time.time()))
            path = out_dir / f"{name}.mp4"
            path.write_bytes(resp.body())
            return {"ok": True, "file": str(path), "title": title, "aweme_id": name,
                    "author": author.get("nickname", ""),
                    "sec_uid": author.get("sec_uid") or author.get("secUid") or ""}
        finally:
            try:
                page.close()
            except Exception:
                pass


def download(link: str, out_dir: Path | None = None) -> dict:
    with sync_playwright() as p:
        ctx = open_browser(p)
        try:
            return download_in(ctx, link, out_dir)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def batch(links: list[str]) -> dict:
    """Tải nhiều link, chỉ mở Chrome một lần."""
    results = []
    with sync_playwright() as p:
        ctx = open_browser(p)
        try:
            for i, link in enumerate(links, 1):
                try:
                    r = download_in(ctx, link)
                except Exception as e:
                    r = {"ok": False, "message": f"Lỗi: {e}"}
                r["link"] = link
                results.append(r)
                emit("PROGRESS", index=i, total=len(links), link=link, ok=r.get("ok", False),
                     title=(r.get("title") or "")[:60], message=r.get("message", ""),
                     file=r.get("file", ""), aweme_id=r.get("aweme_id", ""),
                     author=r.get("author", ""), sec_uid=r.get("sec_uid", ""))
                if i < len(links):
                    ctx.pages[0].wait_for_timeout(random.randint(2000, 5000)) if ctx.pages \
                        else time.sleep(random.randint(2, 5))
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "done": ok, "failed": len(results) - ok}


def latest(link: str) -> dict:
    items, state = [], {"author": None, "done": False}

    def on_response(response):
        if POST_API not in response.url or state["done"]:
            return
        try:
            data = response.json()
        except Exception:
            return
        for aweme in data.get("aweme_list") or []:
            aid = str(aweme.get("aweme_id") or "")
            if aid:
                items.append({"id": aid, "desc": (aweme.get("desc") or "")[:100],
                              "is_image": bool(aweme.get("images"))})
                if not state["author"]:
                    state["author"] = (aweme.get("author") or {}).get("nickname")
        state["done"] = True

    with sync_playwright() as p:
        ctx = open_browser(p)
        page = ctx.new_page()
        try:
            page.on("response", on_response)
            page.goto(link, wait_until="domcontentloaded", timeout=60000)
            for _ in range(40):
                if state["done"]:
                    break
                page.wait_for_timeout(500)
            if not items:
                page.screenshot(path=str(DEBUG_SHOT))
                return {"ok": False, "message": "Không lấy được danh sách video của kênh. Nếu lỗi lặp lại, vào Cài đặt "
                        "bật 'Hiện cửa sổ Chrome' để xem Douyin có đòi xác minh không. "
                        f"Xem ảnh chụp màn hình Chrome ở file {DEBUG_SHOT}"}
            return {"ok": True, "author": state["author"] or "", "items": items}
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def channel(link: str, limit: int = 0) -> dict:
    items, order = {}, []
    state = {"has_more": True, "author": None, "sec_uid": None}

    def on_response(response):
        if POST_API not in response.url:
            return
        try:
            data = response.json()
        except Exception:
            return
        for aweme in data.get("aweme_list") or []:
            aid = str(aweme.get("aweme_id") or "")
            if aid and aid not in items:
                items[aid] = aweme
                order.append(aid)
                if not state["author"]:
                    state["author"] = (aweme.get("author") or {}).get("nickname")
                    state["sec_uid"] = (aweme.get("author") or {}).get("sec_uid")
        if data.get("has_more") in (0, False):
            state["has_more"] = False

    with sync_playwright() as p:
        ctx = open_browser(p)
        page = ctx.new_page()
        try:
            page.on("response", on_response)
            page.goto(link, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)

            # Bước 1: cuộn trang để lấy danh sách video
            idle, rounds = 0, 0
            while state["has_more"] and idle < 20:
                if limit and len(order) >= limit:
                    break
                before = len(order)
                rounds += 1
                if idle and idle % 5 == 0:
                    page.keyboard.press("Escape")          # đóng hộp thoại bật lên (nếu có)
                page.evaluate(SCROLL_JS)
                page.mouse.move(640, 500)
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(2000 if idle < 5 else 3000)
                if idle >= 3:                              # thử cuộn lên một chút rồi xuống lại
                    page.mouse.wheel(0, -1500)
                    page.wait_for_timeout(800)
                    page.evaluate(SCROLL_JS)
                    page.wait_for_timeout(1500)
                idle = idle + 1 if len(order) == before else 0
                emit("PROGRESS", stage="scan", found=len(order))

            stopped_early = state["has_more"] and not (limit and len(order) >= limit)
            if stopped_early:
                page.screenshot(path=str(DEBUG_SHOT))

            if not order:
                page.screenshot(path=str(DEBUG_SHOT))
                return {"ok": False, "message": "Không lấy được danh sách video của kênh. Nếu lỗi lặp lại, vào Cài đặt "
                        "bật 'Hiện cửa sổ Chrome' để xem Douyin có đòi xác minh không. "
                        f"Xem ảnh chụp màn hình Chrome lúc đó ở file {DEBUG_SHOT}"}

            # Bước 2: tải từng video
            ids = order[:limit] if limit else order
            folder = download_root() / safe_name(state["author"])
            folder.mkdir(parents=True, exist_ok=True)
            done = skipped = failed = images = 0

            for i, aid in enumerate(ids, 1):
                aweme = items[aid]
                title = (aweme.get("desc") or "")[:60]
                path = folder / f"{aid}.mp4"

                if path.exists():
                    skipped += 1
                    status = "đã có, bỏ qua"
                elif aweme.get("images"):
                    images += 1
                    status = "bài đăng ảnh, bỏ qua"
                else:
                    found = find_play_url(aweme)
                    ok = False
                    if found:
                        url = found[0]
                        if url.startswith("//"):
                            url = "https:" + url
                        try:
                            resp = ctx.request.get(url, headers={"Referer": "https://www.douyin.com/"},
                                                   timeout=300000)
                            body = resp.body() if resp.ok else b""
                            if len(body) > 10000:
                                path.write_bytes(body)
                                ok = True
                        except Exception:
                            pass
                    if ok:
                        done += 1
                        status = "tải xong"
                    else:
                        failed += 1
                        status = "lỗi"
                    page.wait_for_timeout(random.randint(2000, 5000))  # nghỉ để tránh bị chặn

                emit("PROGRESS", stage="download", index=i, total=len(ids),
                     title=title, status=status)

            m = re.search(r"/user/([^/?#]+)", page.url)
            return {"ok": True, "folder": str(folder), "done": done, "skipped": skipped,
                    "failed": failed, "images": images, "author": state["author"] or "",
                    "sec_uid": state["sec_uid"] or (m.group(1) if m else ""), "ids": order,
                    "stopped_early": bool(stopped_early), "debug_shot": str(DEBUG_SHOT.resolve())}
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def acquire_lock():
    """Chờ tới lượt nếu app và bộ theo dõi cùng muốn dùng Chrome một lúc."""
    try:
        import fcntl
    except ImportError:          # Windows: bỏ qua
        return None
    lock = open(PROFILE_DIR.parent / "browser_profile.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def force_utf8_output() -> None:
    """Windows mặc định dùng bảng mã cũ, không in được chữ Việt/Trung. Ép dùng UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if __name__ == "__main__":
    force_utf8_output()
    DEBUG_SHOT.parent.mkdir(parents=True, exist_ok=True)
    _lock = acquire_lock()
    try:
        if sys.argv[1] == "login":
            result = login()
        elif sys.argv[1] == "batch":
            result = batch(json.loads(sys.argv[2]))
        elif sys.argv[1] == "latest":
            result = latest(sys.argv[2])
        elif sys.argv[1] == "channel":
            result = channel(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
        else:
            result = download(sys.argv[2], Path(sys.argv[3]) if len(sys.argv) > 3 else None)
    except Exception as e:
        result = {"ok": False, "message": f"Lỗi: {e}"}
    print("RESULT:" + json.dumps(result, ensure_ascii=False))
