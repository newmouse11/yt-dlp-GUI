"""ytdlp-gui 진입점: pywebview 창 + JS 브리지."""
import json
import os
import sys
import threading

import webview

import binaries
from downloader import Queue, fetch_info

CONFIG_PATH = os.path.join(binaries.APP_DIR, "config.json")
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")


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
    os.makedirs(binaries.APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


class Api:
    def __init__(self):
        self._queue = Queue()
        self._settings = load_settings()
        self._queue.max_concurrent = int(self._settings["max_concurrent"])
        # 밑줄 접두사: pywebview가 js_api 속성을 JS로 재귀 노출하지 않게
        self._window: webview.Window | None = None
        self._maximized = False
        # 없는 바이너리는 백그라운드에서 받기 시작
        if not (binaries.ytdlp_path() and binaries.ffmpeg_path()):
            threading.Thread(target=binaries.ensure, daemon=True).start()

    # --- 상태 (UI가 0.5초마다 폴링) ---
    def get_state(self):
        return {
            "jobs": self._queue.state(),
            "binaries": binaries.status(),
            "settings": self._settings,
        }

    # --- 메타데이터/대기열 ---
    def fetch_info(self, url):
        return fetch_info(url.strip())

    def add_download(self, items, fmt):
        """items: [{url,title,thumbnail,duration}], fmt: 'v1080'|'mp3'|'m4a'"""
        for it in items:
            self._queue.add(it["url"], fmt, self._settings["out_dir"], it)
        return len(items)

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
        return n

    def open_folder(self):
        os.makedirs(self._settings["out_dir"], exist_ok=True)  # 첫 다운로드 전이면 아직 없을 수 있음
        os.startfile(self._settings["out_dir"])  # noqa: S606 — 로컬 폴더 열기

    def update_ytdlp(self):
        return binaries.update_ytdlp()

    def retry_bootstrap(self):
        if not binaries.progress["active"]:
            threading.Thread(target=binaries.ensure, daemon=True).start()

    # --- 창 조작 (트래픽 라이트 버튼) ---
    def win_close(self): self._window.destroy()
    def win_minimize(self): self._window.minimize()

    def win_maximize(self):
        # 초록 버튼: 최대화 ↔ 원래 크기 토글
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized


def resource(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def main():
    api = Api()
    window = webview.create_window(
        "다운로더", resource("ui/index.html"), js_api=api,
        width=1000, height=680, min_size=(720, 480),
        frameless=True, easy_drag=False, background_color="#1C1C1E",
    )
    api._window = window
    webview.start()


if __name__ == "__main__":
    main()
