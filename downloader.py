"""다운로드 대기열: yt-dlp 서브프로세스 실행과 진행률 파싱."""
import json
import os
import subprocess
import threading
import uuid

import binaries

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# --progress-template 로 받는 한 줄 형식 (파싱하기 쉬운 | 구분)
PROGRESS_TMPL = (
    "download:PROG|%(progress.status)s|%(progress.downloaded_bytes)s"
    "|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s"
    "|%(progress.speed)s|%(progress.eta)s"
)


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_info(url: str) -> dict:
    """메타데이터 조회. 단일 영상이면 화질 목록, 재생목록이면 항목 목록."""
    exe = binaries.ytdlp_path()
    if not exe:
        return {"error": "yt-dlp가 아직 설치되지 않았습니다"}
    r = subprocess.run(
        [exe, "-J", "--flat-playlist", "--no-warnings", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW, timeout=120,
    )
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return {"error": tail[-1] if tail else "정보를 가져오지 못했습니다"}
    info = json.loads(r.stdout)

    if info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            thumbs = e.get("thumbnails") or []
            entries.append({
                "url": e.get("url") or e.get("webpage_url"),
                "title": e.get("title") or "(제목 없음)",
                "duration": e.get("duration"),
                "thumbnail": thumbs[-1]["url"] if thumbs else None,
            })
        return {"type": "playlist", "title": info.get("title"), "entries": entries}

    heights = sorted(
        {f["height"] for f in info.get("formats") or []
         if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    return {
        "type": "video",
        "url": info.get("webpage_url") or url,
        "title": info.get("title"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader") or info.get("channel"),
        "heights": heights,
    }


def _format_args(fmt: str) -> list[str]:
    """fmt: 'v1080' 같은 화질 선택 또는 'mp3'/'m4a' 오디오 추출."""
    if fmt in ("mp3", "m4a"):
        return ["-f", "bestaudio/best", "-x", "--audio-format", fmt]
    h = fmt.removeprefix("v")
    return ["-f", f"bv*[height<={h}]+ba/b[height<={h}]", "--merge-output-format", "mp4"]


class Job:
    def __init__(self, url: str, fmt: str, out_dir: str, meta: dict):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.fmt = fmt
        self.out_dir = out_dir
        self.meta = meta          # title, thumbnail, duration
        self.status = "queued"    # queued|downloading|processing|paused|done|error|canceled
        self.percent = 0.0
        self.speed = 0.0
        self.eta = 0
        self.error = None
        self.proc: subprocess.Popen | None = None

    def state(self) -> dict:
        return {
            "id": self.id, "url": self.url, "fmt": self.fmt,
            "title": self.meta.get("title"), "thumbnail": self.meta.get("thumbnail"),
            "duration": self.meta.get("duration"),
            "status": self.status, "percent": round(self.percent, 1),
            "speed": self.speed, "eta": self.eta, "error": self.error,
        }


class Queue:
    def __init__(self):
        self.jobs: list[Job] = []
        self.lock = threading.Lock()
        self.max_concurrent = 2

    def add(self, url: str, fmt: str, out_dir: str, meta: dict) -> str:
        job = Job(url, fmt, out_dir, meta)
        with self.lock:
            self.jobs.append(job)
        self._pump()
        return job.id

    def _find(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def _pump(self):
        """빈 자리가 있으면 대기 중인 작업을 시작."""
        with self.lock:
            active = sum(1 for j in self.jobs if j.status in ("downloading", "processing"))
            for j in self.jobs:
                if active >= self.max_concurrent:
                    break
                if j.status == "queued":
                    j.status = "downloading"
                    threading.Thread(target=self._run, args=(j,), daemon=True).start()
                    active += 1

    def _run(self, job: Job):
        os.makedirs(job.out_dir, exist_ok=True)
        cmd = [
            binaries.ytdlp_path(), "--newline", "--no-playlist", "--continue",
            "--progress-template", PROGRESS_TMPL,
            "-o", os.path.join(job.out_dir, "%(title)s [%(id)s].%(ext)s"),
            *_format_args(job.fmt), job.url,
        ]
        ffmpeg = binaries.ffmpeg_path()
        if ffmpeg:
            cmd[1:1] = ["--ffmpeg-location", os.path.dirname(ffmpeg)]
        try:
            job.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
            )
        except Exception as e:
            job.status, job.error = "error", str(e)
            self._pump()
            return

        stderr_tail: list[str] = []
        threading.Thread(
            target=lambda: [stderr_tail.append(l.rstrip()) for l in job.proc.stderr],
            daemon=True,
        ).start()

        for line in job.proc.stdout:
            line = line.strip()
            if line.startswith("download:PROG|"):
                _, st, done, total, est, speed, eta = (line.split("|") + [""] * 7)[:7]
                total_b = _num(total) or _num(est)
                if total_b:
                    job.percent = _num(done) / total_b * 100
                job.speed = _num(speed)
                job.eta = int(_num(eta))
                # 일시정지/취소 직후 남은 출력이 상태를 덮어쓰지 않게
                if job.status in ("downloading", "processing"):
                    # 영상+오디오는 스트림 2개를 차례로 받고, 끝나면 병합/변환 단계
                    job.status = "processing" if st == "finished" else "downloading"
        job.proc.wait()

        if job.status == "paused" or job.status == "canceled":
            pass  # 사용자가 멈춘 것 — 상태 유지
        elif job.proc.returncode == 0:
            job.status, job.percent = "done", 100.0
        else:
            job.status = "error"
            errs = [l for l in stderr_tail if "ERROR" in l] or stderr_tail
            job.error = errs[-1][:300] if errs else f"종료 코드 {job.proc.returncode}"
        job.speed = 0
        self._pump()

    # --- 사용자 조작 ---
    @staticmethod
    def _kill(proc: subprocess.Popen):
        # yt-dlp.exe 는 PyInstaller onefile이라 자식 프로세스가 실제 작업을 한다 → 트리째 종료
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, creationflags=NO_WINDOW,
        )

    def pause(self, job_id: str):
        j = self._find(job_id)
        if j and j.status in ("downloading", "processing") and j.proc:
            j.status = "paused"
            self._kill(j.proc)  # --continue 가 .part 이어받기 처리
        self._pump()

    def resume(self, job_id: str):
        j = self._find(job_id)
        if j and j.status in ("paused", "error"):
            j.status, j.error = "queued", None
            self._pump()

    def cancel(self, job_id: str):
        j = self._find(job_id)
        if j:
            if j.proc and j.status in ("downloading", "processing"):
                j.status = "canceled"
                self._kill(j.proc)
            elif j.status in ("queued", "paused"):
                j.status = "canceled"
        self._pump()

    def remove(self, job_id: str):
        self.cancel(job_id)
        with self.lock:
            self.jobs = [j for j in self.jobs if j.id != job_id]

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j.status not in ("done", "canceled")]

    def state(self) -> list[dict]:
        return [j.state() for j in self.jobs]
