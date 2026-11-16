"""yt-dlp.exe / ffmpeg.exe 확보와 실행 환경.

yt-dlp는 앱 데이터 폴더에 받은 공식 exe만 쓴다.
PATH의 pip판은 -U(자체 업데이트)가 막혀 있고 버전도 제각각이라 동작을 예측할 수 없다.
ffmpeg는 버전에 덜 민감하므로 PATH에 있으면 그대로 쓴다.
"""
import hashlib
import locale
import logging
import os
import shutil
import ssl
import subprocess
import threading
import urllib.request
import zipfile

import truststore

log = logging.getLogger(__name__)

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ytdlp-gui")
BIN_DIR = os.path.join(APP_DIR, "bin")
LOG_DIR = os.path.join(APP_DIR, "logs")

YTDLP_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
# yt-dlp 프로젝트가 직접 배포하는 윈도우용 ffmpeg 빌드
FFMPEG_BASE = "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/"
FFMPEG_ZIP = "ffmpeg-master-latest-win64-gpl.zip"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# 공식 yt-dlp.exe(PyInstaller)는 PYTHONIOENCODING을 무시하고 항상 ANSI 코드페이지(한국어 Windows는 cp949)로 출력한다
CHILD_ENCODING = locale.getencoding()

# UI가 폴링으로 읽어 가는 설치 진행 상태
progress = {"active": False, "label": "", "percent": 0, "error": None}
_ensure_lock = threading.Lock()


def ytdlp_path() -> str | None:
    p = os.path.join(BIN_DIR, "yt-dlp.exe")
    return p if os.path.isfile(p) else None


def ffmpeg_path() -> str | None:
    local = os.path.join(BIN_DIR, "ffmpeg.exe")
    return local if os.path.isfile(local) else shutil.which("ffmpeg")


def status() -> dict:
    return {"ytdlp": ytdlp_path(), "ffmpeg": ffmpeg_path(), "progress": dict(progress)}


def child_env() -> dict:
    env = dict(os.environ)
    # PyInstaller 앱이 다른 PyInstaller 앱(yt-dlp.exe)을 띄울 때 공식 문서가 권장하는 환경 리셋
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def run_ytdlp(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    exe = ytdlp_path()
    if not exe:
        raise FileNotFoundError("yt-dlp가 아직 설치되지 않았습니다")
    return subprocess.run(
        [exe, *args], capture_output=True, stdin=subprocess.DEVNULL,
        text=True, encoding=CHILD_ENCODING, errors="replace",
        env=child_env(), creationflags=NO_WINDOW, timeout=timeout,
    )


# Windows 자체 인증서 검증(브라우저와 같은 방식) 사용:
# - 갓 설치한 PC는 루트 인증서를 필요할 때 받아 오는데, Python 기본(OpenSSL)은 이를 못 해 검증 실패한다
# - 백신·회사망의 HTTPS 검사 인증서도 Windows 저장소에 있으므로 함께 신뢰된다
_SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _http_get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "ytdlp-gui"})
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)


def _expected_sha256(sums_url: str, filename: str) -> str:
    """'<hash>  <파일명>' 형식의 체크섬 파일에서 해당 파일 해시를 찾는다."""
    with _http_get(sums_url) as resp:
        for line in resp.read().decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == filename:
                return parts[0].lower()
    raise ValueError(f"체크섬 목록에 {filename} 없음")


def _download_verified(url: str, sums_url: str, filename: str, dest: str, label: str) -> None:
    """스트리밍으로 받아 SHA-256을 검증한 뒤 dest로 교체한다. 실패 시 dest는 건드리지 않는다."""
    expected = _expected_sha256(sums_url, filename)
    part = dest + ".part"
    h = hashlib.sha256()
    progress.update(label=label, percent=0)
    try:
        with _http_get(url) as resp, open(part, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(256 * 1024):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    progress["percent"] = round(done / total * 100)
        if h.hexdigest() != expected:
            raise ValueError(f"{filename} 체크섬 불일치 (손상되었거나 변조된 파일)")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def ensure(on_done=None) -> None:
    """없는 바이너리를 내려받는다. 백그라운드 스레드에서 호출. 끝나면 on_done() (성공·실패 무관)."""
    if not _ensure_lock.acquire(blocking=False):
        return  # 이미 진행 중
    progress.update(active=True, error=None)
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        if not ytdlp_path():
            _download_verified(
                YTDLP_BASE + "yt-dlp.exe", YTDLP_BASE + "SHA2-256SUMS", "yt-dlp.exe",
                os.path.join(BIN_DIR, "yt-dlp.exe"), "yt-dlp 내려받는 중",
            )
            log.info("yt-dlp 설치 완료")
        if not ffmpeg_path():
            zip_path = os.path.join(BIN_DIR, FFMPEG_ZIP)
            _download_verified(
                FFMPEG_BASE + FFMPEG_ZIP, FFMPEG_BASE + "checksums.sha256", FFMPEG_ZIP,
                zip_path, "ffmpeg 내려받는 중 (최초 1회, 약 200MB)",
            )
            progress.update(label="ffmpeg 압축 푸는 중", percent=100)
            try:
                with zipfile.ZipFile(zip_path) as z:
                    for info in z.infolist():
                        base = os.path.basename(info.filename)
                        if base in ("ffmpeg.exe", "ffprobe.exe"):
                            with z.open(info) as src, open(os.path.join(BIN_DIR, base), "wb") as dst:
                                shutil.copyfileobj(src, dst)
            finally:
                os.remove(zip_path)
            log.info("ffmpeg 설치 완료")
    except Exception as e:  # 네트워크·체크섬 실패 — UI 배너로 알리고 재시도 버튼 제공
        log.exception("필수 구성요소 설치 실패")
        progress["error"] = f"{type(e).__name__}: {e}"
    finally:
        progress["active"] = False
        _ensure_lock.release()
        if on_done:
            on_done()


def ytdlp_version() -> str:
    try:
        r = run_ytdlp(["--version"], timeout=30)
        return r.stdout.strip() or "?"
    except (OSError, subprocess.TimeoutExpired):
        return "-"


def update_ytdlp() -> str:
    """yt-dlp 자체 업데이트(-U). 결과 마지막 줄을 돌려준다."""
    try:
        r = run_ytdlp(["-U"], timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.exception("yt-dlp 업데이트 실패")
        return f"업데이트 실패: {e}"
    lines = [l for l in (r.stdout + "\n" + r.stderr).splitlines() if l.strip()]
    log.info("yt-dlp -U 결과: %s", " | ".join(lines[-5:]))
    return lines[-1] if lines else "완료"
