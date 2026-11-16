"""백엔드 자가 점검: 실제 네트워크로 조회·다운로드 (py test_downloader.py <저장폴더>)."""
import os
import sys
import time

from downloader import Queue, _format_args, fetch_info

VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # 19초짜리 공개 영상
PLAYLIST = "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"


def wait(q: Queue, job_id: str, timeout: int = 180) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        s = next(j for j in q.state() if j["id"] == job_id)
        if s["status"] in ("done", "error", "canceled"):
            return s
        time.sleep(0.5)
    raise TimeoutError(job_id)


def main(out_dir: str) -> None:
    assert _format_args("mp3")[-1] == "mp3"
    assert "bv*[height<=720]+ba/b[height<=720]" in _format_args("v720")

    v = fetch_info(VIDEO)
    assert v.get("type") == "video" and v["heights"], v
    print("영상 조회 OK:", v["title"], v["heights"])

    pl = fetch_info(PLAYLIST)
    assert pl.get("type") == "playlist" and pl["entries"], pl
    print("재생목록 조회 OK:", pl["title"], len(pl["entries"]), "개")

    bad = fetch_info("https://example.com/not-a-video")
    assert "error" in bad, bad
    print("잘못된 주소 오류 OK:", bad["error"][:80])

    q = Queue()
    for fmt in ("v360", "mp3"):
        jid = q.add(VIDEO, fmt, out_dir, {"title": v["title"]})
        s = wait(q, jid)
        assert s["status"] == "done", s
        print(f"다운로드 {fmt} OK")

    files = os.listdir(out_dir)
    assert any(f.endswith(".mp4") for f in files) and any(f.endswith(".mp3") for f in files), files
    print("파일:", files)


if __name__ == "__main__":
    main(sys.argv[1])
