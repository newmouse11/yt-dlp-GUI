"""E2E: 숨김 pywebview 창에 실제 UI를 띄우고 JS로 조작 (py test_e2e.py <작업폴더>).

작업폴더를 LOCALAPPDATA·저장 폴더로 써서 실제 사용자 데이터는 건드리지 않는다.
시스템 클립보드는 건드리지 않도록 붙여넣기는 paste 이벤트로 흉내 낸다.
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
VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # 19초, 영어·독일어 자막
BIG = "https://www.youtube.com/watch?v=LXb3EKWsInQ"    # 4K — 일시정지 클릭 검사용
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


def paste(w, text):
    js(w, f"""(() => {{ const dt = new DataTransfer(); dt.setData('text/plain', {json.dumps(text)});
        document.dispatchEvent(new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true}})); }})()""")


def open_dialog(w, url, timeout=180):
    paste(w, url)
    return wait_js(w, "!document.querySelector('#dlg').hidden", timeout)


def scenario(w: webview.Window):
    try:
        check("첫 화면: 빈 상태", wait_js(w, "!document.querySelector('#empty').hidden", 30))
        fresh = binaries.progress["active"]  # 새 폴더면 지금 첫 실행 설치 중

        # 1) Ctrl+V(섞인 주소) → '분석 중' 줄 → 영상 다운로드 창 (설치 중이어도 기다렸다 열림)
        paste(w, MIXED)
        check("분석 중 줄 표시", wait_js(w, "!!document.querySelector('#pending .row.pending')", 3))
        check("영상 다운로드 창", wait_js(w, "!document.querySelector('#dlg').hidden", 180),
              js(w, "document.querySelector('#err-text').textContent"))
        print(f"     (첫 실행 설치 중 조회 경로: {fresh})", flush=True)
        check("분석 중 줄 사라짐", js(w, "document.querySelector('#pending').children.length === 0"))
        check("화질별 예상 용량", js(w, "[...document.querySelectorAll('.q-size')].some(s => s.textContent.includes('MB'))"),
              js(w, "document.querySelector('#qlist').innerText"))
        check("재생목록 전체 보기 링크", js(w, "!!document.querySelector('#btn-whole-pl')"))

        # 2) 재생목록 전환 → 첫 항목만 오디오(M4A)
        js(w, "document.querySelector('#btn-whole-pl').click()")
        check("재생목록 창", wait_js(w, "document.querySelectorAll('.pl-list input').length > 5", 120))
        js(w, """(() => { const all = document.querySelector('#pl-all'); all.checked = false;
            all.dispatchEvent(new Event('change'));
            const first = document.querySelector('.pl-list input'); first.checked = true;
            first.dispatchEvent(new Event('change', {bubbles: true}));
            const k = document.querySelector('#sel-kind'); k.value = 'audio'; k.dispatchEvent(new Event('change'));
            document.querySelector('#sel-ext').value = 'm4a'; })()""")
        check("선택 개수 표시", js(w, "document.querySelector('#pl-count').textContent.includes('1개 선택')"),
              js(w, "document.querySelector('#pl-count').textContent"))
        js(w, "document.querySelector('#dlg-ok').click()")
        check("추가 토스트", wait_js(w, "[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('1개'))", 10))
        check("창 닫힘 + 줄 생성", wait_js(w, "document.querySelector('#dlg').hidden && document.querySelectorAll('#list .row').length === 1", 5))

        end = time.time() + 900  # 깨끗한 PC면 ffmpeg(200MB)까지 받는다
        while time.time() < end and (binaries.progress["active"] or not binaries.ffmpeg_path()):
            time.sleep(1)
        check("첫 실행 설치 완료", binaries.ytdlp_path() and binaries.ffmpeg_path(), binaries.progress)
        check("M4A 완료", wait_js(w, "!!document.querySelector('#list .row.done')", 300),
              js(w, "document.querySelector('#list').innerText"))

        # 3) 영상 + 자막: 직전 선택(오디오)이 기억돼 있는지 보고, 영상·영어 자막으로 바꿔 받기
        check("영상 창(자막 있는 영상)", open_dialog(w, VIDEO))
        check("직전 선택(오디오 M4A) 기억", js(w, "document.querySelector('#sel-kind').value === 'audio' && document.querySelector('#sel-ext').value === 'm4a'"))
        check("자막 언어 목록", js(w, "!!document.querySelector('#sel-sub option[value=en]')"),
              js(w, "document.querySelector('.sub-row')?.innerText"))
        js(w, """(() => { const k = document.querySelector('#sel-kind'); k.value = 'video'; k.dispatchEvent(new Event('change'));
            document.querySelector('#qlist input').click();
            const c = document.querySelector('#chk-sub'); c.checked = true; c.dispatchEvent(new Event('change'));
            document.querySelector('#sel-sub').value = 'en';
            document.querySelector('#dlg-ok').click(); })()""")
        check("영상+자막 완료", wait_js(w, "document.querySelectorAll('#list .row.done').length === 2", 300),
              js(w, "document.querySelector('#list').innerText"))
        names = os.listdir(OUT) if os.path.isdir(OUT) else []
        check("m4a·mp4·en.srt 생성", any(n.endswith(".m4a") for n in names) and any(n.endswith(".mp4") for n in names)
              and any(n.endswith(".en.srt") for n in names), names)
        check("최근 항목이 맨 위", js(w, "document.querySelector('#list .row .r-title').textContent.includes('zoo')"))
        check("완료 줄에 '폴더에서 보기'", js(w, "!!document.querySelector('#list .row.done [data-act=reveal]')"))

        # 4) 다운로드 중 버튼 클릭이 씹히지 않는지 — 버튼을 잡고 1.5초(폴링 3회) 뒤 클릭
        check("큰 영상 창", open_dialog(w, BIG))
        js(w, """(() => { document.querySelector('#qlist input').click();
            const c = document.querySelector('#chk-sub'); if (c) c.checked = false;  // 자막 없는 영상이면 체크박스가 없다
            document.querySelector('#dlg-ok').click(); })()""")
        check("진행률 표시", wait_js(w, """[...document.querySelectorAll('#list .row.downloading .bar i')]
            .some(i => parseFloat(i.style.width) > 0)""", 120), js(w, "document.querySelector('#list').innerText"))
        js(w, "window.__btn = document.querySelector('#list .row.downloading [data-act=pause]')")
        time.sleep(1.5)
        js(w, "window.__btn.click()")
        check("1.5초 뒤 일시정지 클릭 반영", wait_js(w, "!!document.querySelector('#list .row.paused')", 5))
        js(w, "window.__btn = document.querySelector('#list .row.paused [data-act=remove]')")
        time.sleep(1.5)
        js(w, "window.__btn.click()")
        check("제거 클릭 반영", wait_js(w, "!document.querySelector('#list .row.paused')", 5))

        # 5) 오류 창 — 문구가 보이고 선택(복사) 가능, Esc로 닫힘
        paste(w, "https://example.com/not-a-video")
        check("오류 창", wait_js(w, "!document.querySelector('#err-dlg').hidden", 120))
        check("오류 문구 선택 가능", js(w, "getComputedStyle(document.querySelector('#err-text')).userSelect === 'text'"))
        js(w, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        check("Esc로 닫기", wait_js(w, "document.querySelector('#err-dlg').hidden", 3))

        # 6) 스마트 모드 — 켜면 창 없이 MP3로 바로 추가
        js(w, """(() => { document.querySelector('#btn-smart').click();
            document.querySelector('#smart-on').checked = true;
            const k = document.querySelector('#smart-kind'); k.value = 'mp3'; k.dispatchEvent(new Event('change'));
            document.querySelector('#smart-ok').click(); })()""")
        check("스마트 모드 켜짐 표시", wait_js(w, "document.querySelector('#btn-smart').classList.contains('on')", 5))
        before = len(main_api._queue.jobs)
        paste(w, VIDEO)
        check("스마트 모드: 창 없이 추가", wait_js(w, f"document.querySelectorAll('#list .row').length > {before}", 120)
              and js(w, "document.querySelector('#dlg').hidden"))
        check("스마트 모드 형식 MP3", main_api._queue.jobs[-1].fmt == "mp3", main_api._queue.jobs[-1].fmt)
        check("스마트 모드 MP3 완료", wait_js(w, "document.querySelectorAll('#list .row.done').length === 3", 300))
        js(w, """(() => { document.querySelector('#btn-smart').click(); document.querySelector('#smart-on').checked = false;
            document.querySelector('#smart-ok').click(); })()""")
        check("스마트 모드 끔", wait_js(w, "!document.querySelector('#btn-smart').classList.contains('on')", 5))

        # 7) 완료 항목 지우기 → 빈 상태, 설정 창 버전
        js(w, "document.querySelector('#btn-clear').click()")
        check("완료 항목 지우기 → 빈 상태", wait_js(w, "!document.querySelector('#empty').hidden", 5))
        js(w, "document.querySelector('#btn-settings').click()")
        check("설정 창 + 버전", wait_js(w, "document.querySelector('#versions').textContent.includes('yt-dlp 20')", 30),
              js(w, "document.querySelector('#versions').textContent"))

        errs = js(w, "window.__errors || []")
        check("JS 런타임 오류 없음", not errs, errs)
    except Exception:
        check("시나리오 예외 없음", False, traceback.format_exc())
    finally:
        w.destroy()


main_api: main.Api


def main_test():
    global main_api
    main.setup_logging()
    main_api = main.Api()
    w = webview.create_window("e2e", main.resource("ui/index.html"), js_api=main_api,
                              width=1000, height=680, min_size=(720, 480), hidden=True)
    main_api._window = w
    w.events.loaded += lambda: js(w, "window.__errors = []; window.addEventListener('error', e => __errors.push(String(e.message)))")
    webview.start(lambda: threading.Thread(target=scenario, args=(w,), daemon=True).start())
    main_api._queue.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main_test()
