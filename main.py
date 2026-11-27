"""ytdlp-gui 진입점: pywebview 창 + JS 브리지."""
import ctypes
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import webbrowser
import winreg

import webview

import binaries
import winclip
from downloader import Queue, fetch_info

APP_VERSION = "0.3.0"
CONFIG_PATH = os.path.join(binaries.APP_DIR, "config.json")
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
FMT_RE = re.compile(r"^(v\d{3,4}|mp3|m4a)$")
SUB_LANG_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
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
    # 스마트 모드: 링크를 붙여넣으면 창 없이 이 설정으로 바로 받는다
    s["smart"] = {"enabled": False, "fmt": "v1080", "sub_lang": None, "out_dir": s["out_dir"],
                  **(s.get("smart") or {})}
    return s


def save_settings(s: dict) -> None:
    try:
        os.makedirs(binaries.APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("설정 저장 실패: %s", CONFIG_PATH)


# ─── JS에서 넘어온 값 검증 (형식·자막 언어는 yt-dlp 인자로 들어간다) ───
def _check_fmt(fmt) -> str:
    if not isinstance(fmt, str) or not FMT_RE.match(fmt):
        raise ValueError(f"잘못된 형식: {fmt!r}")
    return fmt


def _check_sub(lang) -> str | None:
    if not lang:
        return None
    if not isinstance(lang, str) or not SUB_LANG_RE.match(lang):
        raise ValueError(f"잘못된 자막 언어: {lang!r}")
    return lang


def _check_dir(path, fallback: str) -> str:
    return path if isinstance(path, str) and os.path.isabs(path) else fallback


class Api:
    def __init__(self):
        # 밑줄 접두사: pywebview가 js_api 속성을 JS로 재귀 노출하지 않게
        self._queue = Queue()
        self._settings = load_settings()
        self._queue.max_concurrent = int(self._settings["max_concurrent"])
        self._window: webview.Window | None = None
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

    def read_clipboard(self):
        return winclip.read_text().strip()[:2048]

    # --- 메타데이터/대기열 ---
    def fetch_info(self, url):
        binaries.wait_ytdlp(timeout=600)  # 첫 실행 설치 중에 붙여넣어도 오류 대신 기다렸다 조회
        return fetch_info(str(url or ""))

    def _add(self, items, fmt: str, out_dir: str, sub_lang: str | None) -> int:
        n = 0
        for it in items or []:
            url = str((it or {}).get("url") or "")
            if url.startswith(("http://", "https://")):
                self._queue.add(url, fmt, out_dir, it, sub_lang)
                n += 1
        return n

    def add_download(self, items, fmt, out_dir=None, sub_lang=None):
        """다운로드 창에서 고른 대로 대기열에 넣는다. 고른 저장 위치는 다음에도 기본값이 된다."""
        fmt, sub_lang = _check_fmt(fmt), _check_sub(sub_lang)
        out_dir = _check_dir(out_dir, self._settings["out_dir"])
        if out_dir != self._settings["out_dir"]:
            self._settings["out_dir"] = out_dir
            save_settings(self._settings)
        return self._add(items, fmt, out_dir, sub_lang)

    def smart_add(self, url):
        """스마트 모드: 창 없이 저장해 둔 설정으로 바로 추가 (재생목록이면 전부)."""
        binaries.wait_ytdlp(timeout=600)
        info = fetch_info(str(url or ""))
        if info.get("error"):
            return info
        s = self._settings["smart"]
        items = info["entries"] if info["type"] == "playlist" else [info]
        n = self._add(items, s["fmt"], _check_dir(s["out_dir"], self._settings["out_dir"]), s["sub_lang"])
        return {"added": n, "title": info.get("title")}

    def set_smart(self, cfg):
        cfg = cfg or {}
        self._settings["smart"] = {
            "enabled": bool(cfg.get("enabled")),
            "fmt": _check_fmt(cfg.get("fmt")),
            "sub_lang": _check_sub(cfg.get("sub_lang")),
            "out_dir": _check_dir(cfg.get("out_dir"), self._settings["out_dir"]),
        }
        save_settings(self._settings)
        return self._settings["smart"]

    def pause(self, job_id): self._queue.pause(job_id)
    def resume(self, job_id): self._queue.resume(job_id)
    def cancel(self, job_id): self._queue.cancel(job_id)
    def remove(self, job_id): self._queue.remove(job_id)
    def clear_finished(self): self._queue.clear_finished()

    def reveal(self, job_id):
        """받은 파일을 탐색기에서 선택해 보여 준다. 파일을 못 찾으면 저장 폴더를 연다."""
        path, folder = self._queue.job_location(job_id)
        if path:
            subprocess.Popen(f'explorer /select,"{path}"')  # Windows 파일명엔 " 가 없다
        elif folder:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)  # noqa: S606

    # --- 설정 ---
    def choose_folder(self, initial=None):
        """폴더 선택 창. 고른 경로를 돌려줄 뿐 저장은 호출한 쪽이 정한다."""
        start = _check_dir(initial, self._settings["out_dir"])
        sel = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return sel[0] if sel else None

    def set_out_dir(self, path):
        self._settings["out_dir"] = _check_dir(path, self._settings["out_dir"])
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


def resource(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def system_dark() -> bool:
    """창이 뜨는 순간의 배경색을 테마에 맞춰 흰/검 번쩍임을 줄인다."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def webview2_missing(window: webview.Window | None = None) -> None:
    """WebView2가 없거나 망가졌을 때 — 빈 창 대신 안내하고 종료."""
    log.error("WebView2 런타임 사용 불가 — 안내 후 종료")
    if window:
        window.hide()  # 빈 창이 안내 창을 가리지 않게 (샌드박스에서 실제로 가려짐)
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
        background_color="#1E1F22" if system_dark() else "#FFFFFF",
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
