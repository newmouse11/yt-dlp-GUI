"""E2E: 숨김 pywebview 창에 실제 UI를 띄우고 JS로 조작 (py test_e2e.py <작업폴더>).

작업폴더를 LOCALAPPDATA·저장 폴더로 써서 실제 사용자 데이터는 건드리지 않는다.
"""
import json
import os
import sys
import threading
import time
import traceback

WORK = os.path.abspath(sys.argv[1])
os.environ["LOCALAPPDATA"] = os.path.join(WORK, "appdata")
OUT = os.path.join(WORK, "out_e2e")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 일본어·이모지 파일명 출력용
os.makedirs(os.path.join(WORK, "appdata", "ytdlp-gui"), exist_ok=True)
with open(os.path.join(WORK, "appdata", "ytdlp-gui", "config.json"), "w", encoding="utf-8") as f:
    json.dump({"out_dir": OUT, "max_concurrent": 2}, f)

import webview  # noqa: E402

import binaries  # noqa: E402
import main  # noqa: E402

MIXED = "https://www.youtube.com/watch?v=cGjgCLuyAro&list=PLTLKMoAcZh5I&index=13"
VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
BIG = "https://www.youtube.com/watch?v=LXb3EKWsInQ"  # 4K — 일시정지 클릭 검사용
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), "" if cond else str(detail)[:300]))
    print(("OK   " if cond else "FAIL ") + name, "" if cond else detail, flush=True)


def js(w, code):
    return w.evaluate_js(code)


def wait_js(w, cond, timeout=120, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if js(w, cond):
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def scenario(w: webview.Window):
    try:
        check("첫 화면: 빈 상태 표시", wait_js(w, "!document.querySelector('#empty').hidden", 30))
        fresh = binaries.progress["active"]  # 새 폴더면 지금 첫 실행 설치 중

        # 1) Ctrl+V 붙여넣기 이벤트 → 모달 열림 + 자동 조회 (섞인 주소)
        #    첫 실행 설치 중이어도 오류 없이 yt-dlp 설치를 기다렸다가 조회돼야 한다
        js(w, f"""(() => {{ const dt = new DataTransfer(); dt.setData('text/plain', {json.dumps(MIXED)});
            document.dispatchEvent(new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true}})); }})()""")
        check("붙여넣기 → 모달 열림", wait_js(w, "!document.querySelector('#modal').hidden", 5))
        check("섞인 주소 → 영상 미리보기", wait_js(w, "!!document.querySelector('#panel-body .preview')", 120),
              js(w, "document.querySelector('#panel-body').innerText"))
        check("재생목록 전체 보기 버튼", js(w, "!!document.querySelector('#btn-whole-pl')"))
        check("화질 선택지 표시", js(w, "document.querySelectorAll('.fmt').length >= 3"),
              js(w, "document.querySelector('#fmt-pills').innerText"))
        print(f"     (첫 실행 설치 중 조회 경로 검사: {fresh})", flush=True)

        # 깨끗한 PC에서는 ffmpeg(200MB)까지 받으므로 넉넉히 기다린다 — 배너는 HTML에서 처음부터
        # hidden이라 DOM만 보면 첫 폴링 전에 통과해 버리므로 실제 설치 상태로 판단
        end = time.time() + 900
        while time.time() < end and (binaries.progress["active"] or not binaries.ffmpeg_path()):
            time.sleep(1)
        check("첫 실행 설치 완료", binaries.ytdlp_path() and binaries.ffmpeg_path(), binaries.progress)
        check("설치 후 배너 숨김", wait_js(w, "document.querySelector('#banner').hidden", 5),
              js(w, "document.querySelector('#banner').innerText"))

        # 2) 재생목록 전환 → 첫 항목만 m4a로 받기
        js(w, "document.querySelector('#btn-whole-pl').click()")
        check("재생목록 항목 표시", wait_js(w, "document.querySelectorAll('.pl-list input').length > 5", 120),
              js(w, "document.querySelector('#panel-body').innerText"))
        js(w, """(() => { const all = document.querySelector('#pl-all'); all.checked = false;
            all.dispatchEvent(new Event('change'));
            document.querySelector('.pl-list input').checked = true;
            document.querySelector('.fmt[data-f=m4a]').click();
            document.querySelector('#btn-download').click(); })()""")
        check("대기열 추가 토스트", wait_js(w, "[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('1개'))", 10),
              js(w, "document.querySelector('#toasts').innerText"))
        check("모달 닫힘", js(w, "document.querySelector('#modal').hidden"))
        check("카드 생성", wait_js(w, "document.querySelectorAll('.card').length === 1", 5))
        check("m4a 완료", wait_js(w, "!!document.querySelector('.card.done')", 300),
              js(w, "document.querySelector('#list').innerText"))

        # 3) 단일 영상 360p — 진행 중 진행률 막대가 움직이는지
        js(w, f"openModal({json.dumps(VIDEO)})")
        check("영상 조회", wait_js(w, "!!document.querySelector('#panel-body .preview')", 120))
        # 이 영상은 최고 240p — 첫 번째 영상 화질 버튼을 고른다 (직전 선택 m4a가 기억돼 있음)
        check("직전 선택(m4a) 기억", js(w, "document.querySelector('.fmt.active').dataset.f === 'm4a'"))
        js(w, "document.querySelector('.fmt[data-f^=v]').click(); document.querySelector('#btn-download').click()")
        check("영상 다운로드 완료", wait_js(w, "document.querySelectorAll('.card.done').length === 2", 300),
              js(w, "document.querySelector('#list').innerText"))
        names = os.listdir(OUT) if os.path.isdir(OUT) else []
        check("저장 폴더에 m4a·mp4", any(n.endswith(".m4a") for n in names) and any(n.endswith(".mp4") for n in names), names)
        check("파일명에 [영상ID] 유지", all(n.rsplit(".", 1)[0].endswith("]") for n in names), names)

        # 3-1) 다운로드 중 버튼 클릭이 씹히지 않는지 — 버튼을 잡고 1.5초(폴링 3회) 뒤 클릭
        js(w, f"openModal({json.dumps(BIG)})")
        check("큰 영상 조회", wait_js(w, "!!document.querySelector('#panel-body .preview')", 120))
        js(w, "document.querySelector('.fmt[data-f^=v]').click(); document.querySelector('#btn-download').click()")
        check("큰 영상 진행률 표시", wait_js(w, """[...document.querySelectorAll('.card.downloading .progress i')]
            .some(i => parseFloat(i.style.width) > 0)""", 120), js(w, "document.querySelector('#list').innerText"))
        js(w, "window.__btn = document.querySelector('.card.downloading [data-act=pause]')")
        time.sleep(1.5)
        js(w, "window.__btn.click()")
        check("1.5초 뒤 일시정지 클릭 반영", wait_js(w, "!!document.querySelector('.card.paused')", 5),
              js(w, "document.querySelector('#list').innerText"))
        js(w, "window.__btn = document.querySelector('.card.paused [data-act=remove]')")
        time.sleep(1.5)
        js(w, "window.__btn.click()")
        check("제거 클릭 반영", wait_js(w, "!document.querySelector('.card.paused')", 5))

        # 4) 필터·지우기
        js(w, "document.querySelector('.pill[data-filter=active]').click()")
        check("진행 중 필터 → 0개", wait_js(w, "document.querySelectorAll('.card').length === 0", 3))
        js(w, "document.querySelector('.pill[data-filter=all]').click()")
        js(w, "document.querySelector('#btn-clear').click()")
        check("완료 항목 지우기 → 빈 상태", wait_js(w, "!document.querySelector('#empty').hidden", 5))

        # 5) 오류 경로 — 문구가 보이고 선택(복사) 가능
        js(w, "openModal('https://example.com/not-a-video')")
        check("오류 표시", wait_js(w, "!!document.querySelector('.hint.err')", 120))
        check("오류 텍스트 선택 가능", js(w, "getComputedStyle(document.querySelector('.hint.err')).userSelect === 'text'"))
        js(w, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        check("Esc로 모달 닫기", wait_js(w, "document.querySelector('#modal').hidden", 3))

        # 6) 설정 — 버전 표시
        js(w, "document.querySelector('#btn-settings').click()")
        check("설정 열림 + 버전", wait_js(w, "document.querySelector('#versions').textContent.includes('yt-dlp 20')", 30),
              js(w, "document.querySelector('#versions').textContent"))
        js(w, "document.querySelector('#settings-modal').dispatchEvent(new MouseEvent('mousedown', {bubbles: true}))")

        # 7) 창 크기 조절 API
        js(w, "pywebview.api.win_resize(900, 600)")
        check("창 크기 조절", wait_js(w, "Math.abs(window.innerWidth - 900) <= 20 && Math.abs(window.innerHeight - 600) <= 20", 5),
              js(w, "[window.innerWidth, window.innerHeight]"))
        js(w, "pywebview.api.win_resize(100, 100)")
        time.sleep(1)
        check("최소 크기 제한", js(w, "window.innerWidth >= 700 && window.innerHeight >= 460"),
              js(w, "[window.innerWidth, window.innerHeight]"))

        errs = js(w, "window.__errors || []")
        check("JS 런타임 오류 없음", not errs, errs)
    except Exception:
        check("시나리오 예외 없음", False, traceback.format_exc())
    finally:
        w.destroy()


def main_test():
    api = main.Api()
    w = webview.create_window("e2e", main.resource("ui/index.html"), js_api=api,
                              width=1000, height=680, min_size=(720, 480), frameless=True, hidden=True)
    api._window = w
    w.events.loaded += lambda: js(w, "window.__errors = []; window.addEventListener('error', e => __errors.push(String(e.message)))")
    main.setup_logging()
    webview.start(lambda: threading.Thread(target=scenario, args=(w,), daemon=True).start())
    api._queue.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main_test()
