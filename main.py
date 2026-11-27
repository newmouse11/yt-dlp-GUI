"""ytdlp-gui 진입점: pywebview 창 + JS 브리지."""
import ctypes
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
import webbrowser

import webview

import binaries
from downloader import Queue, fetch_info

APP_VERSION = "0.2.1"
CONFIG_PATH = os.path.join(binaries.APP_DIR, "config.json")
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
FMT_RE = re.compile(r"^(v\d{3,4}|mp3|m4a)$")
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

log = logging.getLogger("ytdlp-gui")


def setup_logging() -> None:
    os.makedirs(binaries.LOG_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(binaries.LOG_DIR, "app.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def load_settings() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("out_dir", DEFAULT_DIR)
    s.setdefault("max_concurrent", 2)
    return s


def save_settings(s: dict) -> None:
    try:
        os.makedirs(binaries.APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("설정 저장 실패: %s", CONFIG_PATH)


class Api:
    def __init__(self):
        # 밑줄 접두사: pywebview가 js_api 속성을 JS로 재귀 노출하지 않게
        self._queue = Queue()
        self._settings = load_settings()
        self._queue.max_concurrent = int(self._settings["max_concurrent"])
        self._window: webview.Window | None = None
        self._maximized = False
        # 없는 바이너리는 백그라운드에서 받기 시작 (그동안 추가된 작업은 끝난 뒤 자동 시작)
        if not (binaries.ytdlp_path() and binaries.ffmpeg_path()):
            self.retry_bootstrap()

    # --- 상태 (UI가 0.5초마다 폴링) ---
    def get_state(self):
        return {
            "jobs": self._queue.state(),
            "binaries": binaries.status(),
            "settings": self._settings,
        }

    def app_info(self):
        return {"app": APP_VERSION, "ytdlp": binaries.ytdlp_version()}

    # --- 메타데이터/대기열 ---
    def fetch_info(self, url):
        binaries.wait_ytdlp(timeout=600)  # 첫 실행 설치 중에 붙여넣어도 오류 대신 기다렸다 조회
        return fetch_info(str(url or ""))

    def add_download(self, items, fmt):
        """items: [{url,title,thumbnail,duration}], fmt: 'v1080'|'mp3'|'m4a'"""
        if not isinstance(fmt, str) or not FMT_RE.match(fmt):
            raise ValueError(f"잘못된 형식: {fmt!r}")
        n = 0
        for it in items or []:
            url = str((it or {}).get("url") or "")
            if url.startswith(("http://", "https://")):
                self._queue.add(url, fmt, self._settings["out_dir"], it)
                n += 1
        return n

    def pause(self, job_id): self._queue.pause(job_id)
    def resume(self, job_id): self._queue.resume(job_id)
    def cancel(self, job_id): self._queue.cancel(job_id)
    def remove(self, job_id): self._queue.remove(job_id)
    def clear_finished(self): self._queue.clear_finished()

    # --- 설정 ---
    def choose_folder(self):
        sel = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=self._settings["out_dir"])
        if sel:
            self._settings["out_dir"] = sel[0]
            save_settings(self._settings)
        return self._settings["out_dir"]

    def set_max_concurrent(self, n):
        n = max(1, min(5, int(n)))
        self._settings["max_concurrent"] = n
        self._queue.max_concurrent = n
        save_settings(self._settings)
        self._queue._pump()  # 늘렸으면 대기 중인 작업을 바로 시작
        return n

    def open_folder(self):
        os.makedirs(self._settings["out_dir"], exist_ok=True)  # 첫 다운로드 전이면 아직 없을 수 있음
        os.startfile(self._settings["out_dir"])  # noqa: S606 — 로컬 폴더 열기

    def open_logs(self):
        os.makedirs(binaries.LOG_DIR, exist_ok=True)
        os.startfile(binaries.LOG_DIR)  # noqa: S606

    def update_ytdlp(self):
        return binaries.update_ytdlp()

    def retry_bootstrap(self):
        threading.Thread(target=binaries.ensure, args=(self._queue._pump,), daemon=True).start()

    # --- 창 조작 (트래픽 라이트 버튼, 크기 조절 손잡이) ---
    def win_close(self): self._window.destroy()
    def win_minimize(self): self._window.minimize()

    def win_maximize(self):
        # 초록 버튼: 최대화 ↔ 원래 크기 토글
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized

    def win_resize(self, width, height):
        # 테두리 없는 창은 Windows가 가장자리 크기 조절을 제공하지 않아 직접 처리 (논리 픽셀)
        self._window.resize(max(720, int(width)), max(480, int(height)))


def resource(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def webview2_missing(window: webview.Window | None = None) -> None:
    """WebView2가 없거나 망가졌을 때 — 닫기 버튼도 없는 빈 창 대신 안내하고 종료."""
    log.error("WebView2 런타임 사용 불가 — 안내 후 종료")
    if window:
        window.hide()  # 빈 검은 창이 안내 창을 가리지 않게 (샌드박스에서 실제로 가려짐)
    # MB_YESNO | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
    flags, idyes = 0x4 | 0x10 | 0x10000 | 0x40000, 6
    text = ("화면 표시에 필요한 Microsoft Edge WebView2 런타임을 찾을 수 없습니다.\n\n"
            "설치 페이지를 열까요? (설치 후 다시 실행하세요)")
    if ctypes.windll.user32.MessageBoxW(None, text, "다운로더", flags) == idyes:
        webbrowser.open(WEBVIEW2_URL)
    if window:
        window.destroy()


class WebView2FailWatch(logging.Handler):
    """pywebview는 WebView2 초기화 실패를 로그만 남기고 빈 창을 띄워 둔다 → 그 로그를 감지."""

    def __init__(self, window: webview.Window):
        super().__init__(logging.ERROR)
        self.window = window

    def emit(self, record: logging.LogRecord) -> None:
        if "WebView2 initialization failed" in record.getMessage():
            threading.Thread(target=webview2_missing, args=(self.window,), daemon=True).start()


def main():
    setup_logging()
    log.info("시작 v%s", APP_VERSION)
    try:
        # WebView2가 없으면 pywebview는 구형 IE 엔진으로 넘어가 UI가 깨진다
        from webview.platforms import winforms
        if winforms.renderer != "edgechromium":
            log.error("렌더러가 %s — WebView2 없음", winforms.renderer)
            webview2_missing()
            return
    except Exception:
        log.exception("WebView2 확인 실패")
        webview2_missing()
        return
    api = Api()
    window = webview.create_window(
        "다운로더", resource("ui/index.html"), js_api=api,
        width=1000, height=680, min_size=(720, 480),
        frameless=True, easy_drag=False, background_color="#1C1C1E",
    )
    api._window = window
    logging.getLogger("pywebview").addHandler(WebView2FailWatch(window))
    try:
        webview.start()
    finally:
        api._queue.shutdown()
        log.info("종료")


if __name__ == "__main__":
    main()
