"""yt-dlp.exe / ffmpeg.exe 탐색·자동 설치.

우선순위: 앱 데이터 폴더(우리가 설치한 것) → PATH.
없으면 공식 릴리스에서 앱 데이터 폴더로 내려받는다(최초 1회).
"""
import io
import os
import shutil
import subprocess
import urllib.request
import zipfile

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ytdlp-gui")
BIN_DIR = os.path.join(APP_DIR, "bin")

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
# yt-dlp 프로젝트가 직접 배포하는 윈도우용 ffmpeg 빌드
FFMPEG_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"

# UI가 폴링으로 읽어 가는 설치 진행 상태
progress = {"active": False, "label": "", "percent": 0, "error": None}


def _find(name: str) -> str | None:
    local = os.path.join(BIN_DIR, name)
    if os.path.isfile(local):
        return local
    return shutil.which(name)


def ytdlp_path() -> str | None:
    return _find("yt-dlp.exe")


def ffmpeg_path() -> str | None:
    return _find("ffmpeg.exe")


def status() -> dict:
    return {"ytdlp": ytdlp_path(), "ffmpeg": ffmpeg_path(), "progress": dict(progress)}


def _download(url: str, label: str) -> bytes:
    progress.update(label=label, percent=0)
    req = urllib.request.Request(url, headers={"User-Agent": "ytdlp-gui"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        while chunk := resp.read(1024 * 256):
            buf.write(chunk)
            if total:
                progress["percent"] = round(buf.tell() / total * 100)
    return buf.getvalue()


def ensure() -> None:
    """없는 바이너리를 내려받는다. 백그라운드 스레드에서 호출."""
    progress.update(active=True, error=None)
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        if not ytdlp_path():
            data = _download(YTDLP_URL, "yt-dlp 내려받는 중")
            with open(os.path.join(BIN_DIR, "yt-dlp.exe"), "wb") as f:
                f.write(data)
        if not ffmpeg_path():
            data = _download(FFMPEG_URL, "ffmpeg 내려받는 중 (최초 1회, 수백 MB)")
            progress.update(label="ffmpeg 압축 푸는 중", percent=100)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    base = os.path.basename(info.filename)
                    if base in ("ffmpeg.exe", "ffprobe.exe"):
                        with z.open(info) as src, open(os.path.join(BIN_DIR, base), "wb") as dst:
                            shutil.copyfileobj(src, dst)
    except Exception as e:  # 네트워크 실패 등 — UI에 그대로 보여 준다
        progress["error"] = f"{type(e).__name__}: {e}"
    finally:
        progress["active"] = False


def update_ytdlp() -> str:
    """yt-dlp 자체 업데이트(-U). 결과 마지막 줄을 돌려준다."""
    exe = ytdlp_path()
    if not exe:
        return "yt-dlp가 없습니다"
    r = subprocess.run(
        [exe, "-U"], capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=300,
    )
    out = (r.stdout or "").strip().splitlines()
    return out[-1] if out else (r.stderr or "").strip()[-200:] or "완료"
