"""다운로드 대기열: yt-dlp 서브프로세스 실행과 진행률 파싱."""
import collections
import json
import logging
import os
import re
import subprocess
import threading
import urllib.parse
import uuid

import binaries

log = logging.getLogger(__name__)

# --progress-template 로 받는 한 줄 형식 (파싱하기 쉬운 | 구분).
# 앞의 "download:"는 템플릿 종류 지정자라 출력에는 "PROG|…"만 찍힌다.
PROGRESS_TMPL = (
    "download:PROG|%(progress.status)s|%(progress.downloaded_bytes)s"
    "|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s"
    "|%(progress.speed)s|%(progress.eta)s|%(progress.filename)s"  # Windows 파일명엔 | 가 없다
)
SUB_EXTS = (".vtt", ".srt", ".ttml", ".srv1", ".srv2", ".srv3", ".json3", ".ass", ".lrc")
FORMATS_RE = re.compile(r"^\[info\] .*Downloading \d+ format\(s\): (\S+)")
# 일시적인 네트워크 문제로 보이는 오류 — 한 번 더 시도할 가치가 있다
RETRYABLE = ("Unable to handle request", "timed out", "Connection", "HTTP Error 5", "Temporary failure")
RETRYABLE_DOWNLOAD = RETRYABLE + ("HTTP Error 403",)
MAX_AUTO_RETRIES = 2


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def _error_text(stderr: str) -> str:
    """stderr에서 사용자에게 보여 줄 오류 줄들을 고른다 (마지막 ERROR 최대 3줄)."""
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    errs = [l for l in lines if l.startswith("ERROR") or "Unexpected error" in l]
    picked = errs[-3:] if errs else lines[-1:]
    return "\n".join(picked)[:600] or "정보를 가져오지 못했습니다"


def _split_youtube_mixed(url: str) -> tuple[str, str | None]:
    """watch?v=…&list=… 처럼 영상과 재생목록이 함께 든 주소 → (영상만 주소, 재생목록 주소)."""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    if not (host.endswith("youtube.com") and p.path == "/watch"):
        return url, None
    q = urllib.parse.parse_qs(p.query)
    if "v" in q and "list" in q:
        return (f"https://www.youtube.com/watch?v={q['v'][0]}",
                f"https://www.youtube.com/playlist?list={q['list'][0]}")
    return url, None


def _run_json(url: str, no_playlist: bool) -> tuple[dict | None, str]:
    args = ["-J", "--flat-playlist", "--no-warnings", url]
    if no_playlist:
        args.insert(0, "--no-playlist")
    for attempt in (1, 2):
        try:
            r = binaries.run_ytdlp(args, timeout=300)
        except subprocess.TimeoutExpired:
            log.warning("조회 시간 초과: %s", url)
            return None, "응답이 너무 오래 걸립니다 (5분 초과)"
        except FileNotFoundError as e:
            return None, str(e)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout), ""
            except ValueError:
                log.error("조회 결과 JSON 파싱 실패: %s\nstdout 앞부분=%r", url, r.stdout[:300])
                return None, "yt-dlp 출력을 해석하지 못했습니다"
        msg = _error_text(r.stderr)
        log.error("조회 실패(시도 %d): %s\n%s", attempt, url, r.stderr[-4000:])
        if attempt == 1 and any(k in msg for k in RETRYABLE):
            continue
        return None, msg
    return None, msg


def fetch_info(url: str) -> dict:
    """메타데이터 조회. 단일 영상이면 화질 목록, 재생목록이면 항목 목록."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"error": "http(s)로 시작하는 주소를 넣어 주세요"}

    video_url, playlist_url = _split_youtube_mixed(url)
    info, err = _run_json(video_url, no_playlist=playlist_url is not None)
    if info is None:
        return {"error": err}

    if info.get("_type") == "playlist":
        entries, skipped = [], 0
        for e in info.get("entries") or []:
            u = (e or {}).get("url") or (e or {}).get("webpage_url") or ""
            if not u.startswith(("http://", "https://")):
                skipped += 1  # 비공개·삭제된 항목 등
                continue
            thumbs = e.get("thumbnails") or []
            entries.append({
                "url": u,
                "id": e.get("id"),
                "title": e.get("title") or "(제목 없음)",
                "duration": e.get("duration"),
                "thumbnail": thumbs[-1].get("url") if thumbs else None,
            })
        if skipped:
            log.info("재생목록 %s: 받을 수 없는 항목 %d개 제외", url, skipped)
        if not entries:
            return {"error": "재생목록에 받을 수 있는 항목이 없습니다"}
        return {"type": "playlist", "title": info.get("title"), "entries": entries, "skipped": skipped}

    if info.get("is_live"):
        return {"error": "진행 중인 라이브 방송은 받을 수 없습니다"}

    formats = info.get("formats") or []
    duration = info.get("duration")
    heights = sorted(
        {f["height"] for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    audio = _best_audio(formats)
    audio_size = _size(audio, duration) if audio else None
    return {
        "type": "video",
        "id": info.get("id"),
        "url": info.get("webpage_url") or video_url,
        "title": info.get("title"),
        "duration": duration,
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader") or info.get("channel"),
        "heights": heights,
        "qualities": [{"height": h, "size": _video_size(formats, h, duration, audio_size)} for h in heights],
        "audio_size": audio_size,
        "subtitles": _subtitle_options(info),
        "playlist_url": playlist_url,
    }


# ─── 화질별 예상 용량 ───
# yt-dlp가 실제로 고를 형식(_format_args의 -S res,+codec:avc:m4a)을 흉내 내 대략 계산한다.

def _size(f: dict, duration) -> float | None:
    """파일 크기. 없으면(HLS 등) 비트레이트 × 길이로 추정."""
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and duration:
        s = f["tbr"] * 1000 / 8 * duration
    return s or None


def _best_audio(formats: list[dict]) -> dict | None:
    audio = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    m4a = [f for f in audio if str(f.get("acodec")).startswith("mp4a")] or audio
    return max(m4a, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)


def _video_size(formats: list[dict], height: int, duration, audio_size) -> float | None:
    vids = [f for f in formats if f.get("height") == height and f.get("vcodec") not in (None, "none")]
    avc = [f for f in vids if str(f.get("vcodec")).startswith("avc1")]
    pool = avc or vids
    # 크기가 적힌 형식(https)이 실제 선택되는 쪽 — HLS보다 우선
    known = [f for f in pool if f.get("filesize") or f.get("filesize_approx")] or pool
    best = max(known, key=lambda f: f.get("tbr") or 0, default=None)
    if not best:
        return None
    size = _size(best, duration)
    if size and best.get("acodec") in (None, "none") and audio_size:
        size += audio_size  # 영상만 있는 형식이면 오디오가 합쳐진다
    return size


# ─── 자막 ───
LANG_NAMES = {"ko": "한국어", "en": "영어", "ja": "일본어", "zh-Hans": "중국어(간체)",
              "zh-Hant": "중국어(번체)", "es": "스페인어", "fr": "프랑스어", "de": "독일어"}


def _subtitle_options(info: dict) -> list[dict]:
    """수동 자막 + 자동 생성 원본 + (없으면) 한국어 자동 번역. 자동 번역 150여 개 언어는 생략."""
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    opts = [{"code": k, "name": LANG_NAMES.get(k) or (v[0].get("name") if v else "") or k}
            for k, v in subs.items()]
    for k in auto:
        if k.endswith("-orig"):
            base = k.removesuffix("-orig")
            opts.append({"code": k, "name": f"{LANG_NAMES.get(base, base)} (자동 생성)"})
    if "ko" not in subs and "ko" in auto:
        opts.append({"code": "ko", "name": "한국어 (자동 번역)"})
    return opts


def _format_args(fmt: str) -> list[str]:
    """fmt: 'v1080' 같은 화질 선택 또는 'mp3'/'m4a' 오디오 추출."""
    if fmt in ("mp3", "m4a"):
        return ["-f", "bestaudio/best", "-x", "--audio-format", fmt]
    h = int(fmt.removeprefix("v"))
    # 같은 해상도면 H.264+AAC 우선 → Windows 기본 플레이어에서도 바로 재생
    return ["-f", f"bv*[height<={h}]+ba/b[height<={h}]/b",
            "-S", f"res:{h},+codec:avc:m4a", "--merge-output-format", "mp4"]


def _subtitle_args(sub_lang: str | None) -> list[str]:
    if not sub_lang:
        return []
    # 수동 자막이 있으면 그것을, 없으면 자동 생성/번역 자막을 받아 .srt로 저장
    return ["--write-subs", "--write-auto-subs", "--sub-langs", sub_lang, "--convert-subs", "srt"]


class Job:
    def __init__(self, url: str, fmt: str, out_dir: str, meta: dict, sub_lang: str | None = None):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.fmt = fmt
        self.out_dir = out_dir
        self.sub_lang = sub_lang
        self.meta = meta          # id(영상), title, thumbnail, duration
        self.size = 0.0           # 받은(받을) 스트림 크기 합 — 목록에 표시
        self.status = "queued"    # queued|downloading|processing|paused|done|error|canceled
        self.percent = 0.0
        self.speed = 0.0
        self.eta = 0
        self.error = None
        self.proc: subprocess.Popen | None = None
        self.run_no = 0           # 재시작마다 증가 — 이전 실행 스레드가 상태를 덮어쓰지 않게
        self.auto_retries = 0     # 일시적 오류로 자동 재시도한 횟수

    def state(self) -> dict:
        return {
            "id": self.id, "url": self.url, "fmt": self.fmt,
            "title": self.meta.get("title"), "thumbnail": self.meta.get("thumbnail"),
            "duration": self.meta.get("duration"), "size": self.size, "sub_lang": self.sub_lang,
            "status": self.status, "percent": round(self.percent, 1),
            "speed": self.speed, "eta": self.eta, "error": self.error,
        }

    def output_path(self) -> str | None:
        """받은 파일 경로 — 파일명 끝의 [영상ID]와 형식의 확장자로 찾는다.
        같은 영상을 mp4·mp3로 따로 받았을 수 있어 확장자까지 맞춰야 한다."""
        vid = self.meta.get("id")
        if not vid or not os.path.isdir(self.out_dir):
            return None
        exts = (f".{self.fmt}",) if self.fmt in ("mp3", "m4a") else (".mp4", ".mkv", ".webm")
        tag = f"[{vid}]"
        cands = [os.path.join(self.out_dir, n) for n in os.listdir(self.out_dir)
                 if tag in n and n.endswith(exts)]
        return max(cands, key=os.path.getmtime, default=None)


ACTIVE = ("downloading", "processing")
UNFINISHED = ("queued", "downloading", "processing", "paused")


class Queue:
    def __init__(self):
        self.jobs: list[Job] = []
        self.lock = threading.Lock()
        self.max_concurrent = 2

    def add(self, url: str, fmt: str, out_dir: str, meta: dict, sub_lang: str | None = None) -> str:
        with self.lock:
            # 같은 영상·형식이 이미 진행 중이면 중복으로 받지 않는다 (같은 파일에 동시에 쓰는 충돌 방지)
            dup = next((j for j in self.jobs
                        if j.url == url and j.fmt == fmt and j.status in UNFINISHED), None)
            if dup:
                return dup.id
            job = Job(url, fmt, out_dir, meta, sub_lang)
            self.jobs.append(job)
        self._pump()
        return job.id

    def _find(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def job_location(self, job_id: str) -> tuple[str | None, str | None]:
        """(받은 파일 경로, 저장 폴더) — '폴더에서 보기'용."""
        j = self._find(job_id)
        return (j.output_path(), j.out_dir) if j else (None, None)

    def _pump(self):
        """빈 자리가 있으면 대기 중인 작업을 시작."""
        # 첫 실행 설치(yt-dlp·ffmpeg) 중에는 대기 — 끝나면 binaries.ensure의 on_done이 다시 부른다.
        # ffmpeg 없이 시작하면 영상+오디오 병합이 안 된다.
        if binaries.progress["active"] or not binaries.ytdlp_path():
            return
        with self.lock:
            active = sum(1 for j in self.jobs if j.status in ACTIVE)
            for j in self.jobs:
                if active >= self.max_concurrent:
                    break
                if j.status == "queued":
                    j.status = "downloading"
                    j.run_no += 1
                    threading.Thread(target=self._run, args=(j, j.run_no), daemon=True).start()
                    active += 1

    def _run(self, job: Job, run_no: int):
        exe = binaries.ytdlp_path()
        if not exe:
            job.status, job.error = "error", "yt-dlp가 아직 설치되지 않았습니다"
            self._pump()
            return
        cmd = [
            exe, "--newline", "--no-playlist", "--continue", "--no-colors",
            "--progress-template", PROGRESS_TMPL,
            # 제목만 150바이트로 잘라 Windows 경로 제한을 피하고 [id]는 항상 남긴다
            # (--trim-filenames는 폴더 경로까지 세어 깊은 폴더에서 제목과 id가 잘린다)
            "-o", os.path.join(job.out_dir, "%(title).150B [%(id)s].%(ext)s"),
            *_format_args(job.fmt), *_subtitle_args(job.sub_lang), job.url,
        ]
        ffmpeg = binaries.ffmpeg_path()
        if ffmpeg:
            cmd[1:1] = ["--ffmpeg-location", os.path.dirname(ffmpeg)]
        try:
            os.makedirs(job.out_dir, exist_ok=True)
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                text=True, encoding=binaries.CHILD_ENCODING, errors="replace",
                env=binaries.child_env(), creationflags=binaries.NO_WINDOW,
            )
        except OSError as e:
            log.exception("다운로드 시작 실패: %s", job.url)
            job.status, job.error = "error", str(e)
            self._pump()
            return
        job.proc = proc
        if job.run_no != run_no or job.status not in ACTIVE:
            self._kill(proc)  # 프로세스가 뜨기 직전에 일시정지/취소된 경우

        stderr_tail: collections.deque[str] = collections.deque(maxlen=60)
        reader = threading.Thread(
            target=lambda: [stderr_tail.append(l.rstrip()) for l in proc.stderr], daemon=True)
        reader.start()

        n_streams, stream_idx, done_bytes = 1, 0, 0.0
        for line in proc.stdout:
            line = line.strip()
            m = FORMATS_RE.match(line)
            if m:  # "135+140" → 영상·오디오 2개 스트림을 차례로 받는다
                n_streams, stream_idx, done_bytes = m.group(1).count("+") + 1, 0, 0.0
                continue
            if not line.startswith("PROG|"):
                continue
            _, st, done, total, est, speed, eta, fname = (line.split("|") + [""] * 8)[:8]
            if fname.lower().endswith(SUB_EXTS):
                continue  # 자막 파일 진행률은 영상 스트림 계산에서 뺀다
            stream_total = _num(total) or _num(est)
            job.size = done_bytes + stream_total
            if st == "finished":
                done_bytes += stream_total
            frac = min(_num(done) / stream_total, 1.0) if stream_total else 0.0
            # ponytail: 영상 0~90%, 오디오 90~100%로 고정 배분 — 실제 크기 비율은 모름.
            #           정확히 하려면 조회 단계의 format filesize 합을 Job에 넘겨 바이트 기준으로 계산
            if n_streams >= 2:
                pct = frac * 90 if stream_idx == 0 else 90 + frac * 10
            else:
                pct = frac * 100
            job.percent = max(job.percent if stream_idx else 0, min(pct, 100.0))
            job.speed = _num(speed)
            job.eta = int(_num(eta))
            if st == "finished":
                stream_idx += 1
            # 일시정지/취소 직후 남은 출력이나 이전 실행이 상태를 덮어쓰지 않게
            if job.run_no == run_no and job.status in ACTIVE:
                job.status = "processing" if st == "finished" else "downloading"
        proc.wait()
        reader.join(timeout=5)

        if job.run_no != run_no:
            return  # 이미 새로 시작된 실행이 있음
        if job.status in ("paused", "canceled"):
            pass  # 사용자가 멈춘 것 — 상태 유지
        elif proc.returncode == 0:
            path = job.output_path()
            if path:  # 이미 받은 파일이면 진행률 줄이 없어 크기를 모른다 → 실제 파일 크기
                job.size = os.path.getsize(path)
            job.status, job.percent, job.error = "done", 100.0, None
            log.info("다운로드 완료: %s (%s)", job.url, job.fmt)
        else:
            err = _error_text("\n".join(stderr_tail)) if stderr_tail else f"종료 코드 {proc.returncode}"
            log.error("다운로드 실패(자동 재시도 %d회째): %s (%s) code=%s\n%s",
                      job.auto_retries, job.url, job.fmt, proc.returncode, "\n".join(stderr_tail))
            # YouTube는 다운로드 주소에 간헐적으로 403을 준다 → 다시 받으면 새 주소로 대개 성공
            if job.auto_retries < MAX_AUTO_RETRIES and any(k in err for k in RETRYABLE_DOWNLOAD):
                job.auto_retries += 1
                job.status = "queued"
            else:
                job.status, job.error = "error", err
        job.speed = 0
        self._pump()

    # --- 사용자 조작 ---
    @staticmethod
    def _kill(proc: subprocess.Popen):
        # yt-dlp.exe 는 PyInstaller onefile이라 자식 프로세스가 실제 작업을 한다 → 트리째 종료
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, creationflags=binaries.NO_WINDOW,
        )

    def pause(self, job_id: str):
        j = self._find(job_id)
        if j and j.status in ACTIVE:
            j.status = "paused"
            if j.proc and j.proc.poll() is None:
                self._kill(j.proc)  # --continue 가 .part 이어받기 처리
        self._pump()

    def resume(self, job_id: str):
        j = self._find(job_id)
        if j and j.status in ("paused", "error", "canceled"):
            j.status, j.error, j.auto_retries = "queued", None, 0
            self._pump()

    def cancel(self, job_id: str):
        j = self._find(job_id)
        if j and j.status in UNFINISHED:
            was_active = j.status in ACTIVE
            j.status = "canceled"
            if was_active and j.proc and j.proc.poll() is None:
                self._kill(j.proc)
        self._pump()

    def remove(self, job_id: str):
        self.cancel(job_id)
        with self.lock:
            self.jobs = [j for j in self.jobs if j.id != job_id]

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j.status not in ("done", "canceled")]

    def shutdown(self):
        """앱 종료 시 실행 중인 yt-dlp를 정리 (고아 프로세스가 계속 받는 것 방지)."""
        for j in self.jobs:
            if j.status in ACTIVE and j.proc and j.proc.poll() is None:
                self._kill(j.proc)

    def state(self) -> list[dict]:
        return [j.state() for j in self.jobs]
