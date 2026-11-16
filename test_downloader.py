"""백엔드 자가 점검: 실제 네트워크로 설치·조회·다운로드 (py test_downloader.py <작업폴더>).

작업폴더를 LOCALAPPDATA로 써서 실제 사용자 앱 데이터는 건드리지 않는다.
"""
import os
import sys
import time

WORK = os.path.abspath(sys.argv[1])
os.environ["LOCALAPPDATA"] = os.path.join(WORK, "appdata")  # binaries 임포트 전에 바꿔야 함
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # exe로 돌려도 일본어·이모지 출력 가능

import binaries  # noqa: E402
from downloader import Queue, _format_args, _split_youtube_mixed, fetch_info  # noqa: E402

VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # 19초짜리 공개 영상
BIG = "https://www.youtube.com/watch?v=LXb3EKWsInQ"     # 수백 MB — 일시정지 검사용
MIXED = "https://www.youtube.com/watch?v=cGjgCLuyAro&list=PLTLKMoAcZh5I&index=13"
PLAYLIST = "https://www.youtube.com/playlist?list=PLTLKMoAcZh5I"


def wait(q: Queue, job_id: str, until=("done", "error", "canceled"), timeout: int = 300) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        s = next(j for j in q.state() if j["id"] == job_id)
        if s["status"] in until:
            return s
        time.sleep(0.5)
    raise TimeoutError(f"{job_id}: {s}")


def check(name: str, cond: bool, detail=""):
    print(("OK   " if cond else "FAIL ") + name, detail if not cond else "")
    if not cond:
        sys.exit(1)


def main() -> None:
    out_dir = os.path.join(WORK, "out")
    os.makedirs(out_dir, exist_ok=True)

    # 단위 검사
    check("mp3 인자", _format_args("mp3")[-1] == "mp3")
    check("화질 인자", "res:720,+codec:avc:m4a" in _format_args("v720"))
    v_only, pl = _split_youtube_mixed(MIXED)
    check("섞인 주소 분리", v_only.endswith("v=cGjgCLuyAro") and pl.endswith("list=PLTLKMoAcZh5I"), (v_only, pl))
    check("일반 주소 유지", _split_youtube_mixed(VIDEO) == (VIDEO, None))
    check("http 아닌 주소 거부", "error" in fetch_info("ftp://x"))

    # 설치 (체크섬 검증 포함)
    t = time.time()
    binaries.ensure()
    check("yt-dlp 설치", binaries.ytdlp_path() is not None, binaries.progress)
    check("ffmpeg 확보", binaries.ffmpeg_path() is not None, binaries.progress)
    print(f"     설치 {time.time() - t:.0f}초, yt-dlp {binaries.ytdlp_version()}")

    # 조회
    v = fetch_info(VIDEO)
    check("영상 조회", v.get("type") == "video" and v["heights"], v)
    m = fetch_info(MIXED)
    check("섞인 주소 → 영상 + 재생목록 링크", m.get("type") == "video" and m.get("playlist_url"), m)
    p = fetch_info(PLAYLIST)
    check("재생목록 조회", p.get("type") == "playlist" and len(p["entries"]) > 5, p.get("error"))
    print(f"     재생목록 '{p['title']}' {len(p['entries'])}개, 제외 {p['skipped']}개")
    bad = fetch_info("https://example.com/not-a-video")
    check("잘못된 주소 오류", "error" in bad, bad)
    print("     오류 문구:", bad["error"][:100])

    # 설치 중에 추가된 작업은 대기하다가 설치가 끝나면 시작
    q = Queue()
    binaries.progress["active"] = True
    jid = q.add(VIDEO, "v240", out_dir, {"title": "gate"})
    check("설치 중 추가 → 대기", q._find(jid).status == "queued")
    binaries.progress["active"] = False
    q._pump()  # 실제로는 ensure(on_done=q._pump)가 부른다
    check("설치 끝 → 시작", q._find(jid).status in ("downloading", "processing", "done"))
    check("대기했던 작업 완료", wait(q, jid)["status"] == "done")

    # 다운로드
    ids = {}
    for fmt in ("v360", "mp3"):
        ids[fmt] = q.add(VIDEO, fmt, out_dir, {"title": v["title"]})
    check("중복 추가 무시", q.add(VIDEO, "v360", out_dir, {}) == ids["v360"])
    for fmt, jid in ids.items():
        s = wait(q, jid)
        check(f"다운로드 {fmt}", s["status"] == "done", s)
    files = os.listdir(out_dir)
    check("파일 생성", any(f.endswith(".mp4") for f in files) and any(f.endswith(".mp3") for f in files), files)

    # 일시정지 → 이어받기 → 취소
    jid = q.add(BIG, "v2160", out_dir, {"title": "big"})  # 회선이 빨라도 수 초 안에 끝나지 않을 크기
    wait(q, jid, until=("downloading",), timeout=60)
    end = time.time() + 120
    while q._find(jid).percent < 0.3 and time.time() < end:
        time.sleep(0.2)
    p1 = q._find(jid).percent
    q.pause(jid)
    time.sleep(3)
    s = next(j for j in q.state() if j["id"] == jid)
    check("일시정지 상태", s["status"] == "paused", s)
    check("일시정지 후 프로세스 종료", q._find(jid).proc.poll() is not None)
    parts = [f for f in os.listdir(out_dir) if f.endswith(".part")]
    check(".part 남음 (이어받기용)", bool(parts), os.listdir(out_dir))
    q.resume(jid)
    wait(q, jid, until=("downloading",), timeout=30)
    time.sleep(8)
    p2 = q._find(jid).percent
    check("이어받기 후 진행률 유지", p2 >= p1 * 0.9, (p1, p2))
    print(f"     일시정지 {p1:.1f}% → 이어받기 후 {p2:.1f}%")
    q.cancel(jid)
    time.sleep(2)
    check("취소", q._find(jid).status == "canceled" and q._find(jid).proc.poll() is not None)
    print("ALL PASS")


if __name__ == "__main__":
    main()
