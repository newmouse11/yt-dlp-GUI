# ytdlp-gui

yt-dlp 기반 Windows 동영상 다운로더. 링크를 붙여넣고 화질을 고르면 끝.

- 화질 선택 (최대 4K) / MP3·M4A 오디오 추출 — 1080p 이하는 H.264+AAC 우선이라 Windows 기본 플레이어에서 바로 재생
- 재생목록: 항목을 골라서 일괄 다운로드. `watch?v=…&list=…` 주소는 영상 하나를 먼저 보여 주고 "재생목록 전체 보기" 제공
- 대기열: 동시 다운로드 수 설정, 일시정지·이어받기·재시도, 일시적 오류(403 등) 자동 재시도
- 썸네일·제목·길이 미리보기
- 라이트/다크 모드 자동 전환

## 사용

`ytdlp-gui.exe`를 실행하세요. 처음 실행하면 yt-dlp와 ffmpeg를
`%LOCALAPPDATA%\ytdlp-gui\bin`에 자동으로 내려받아요 (1회, SHA-256 검증).
ffmpeg가 이미 PATH에 있으면 그것을 씁니다.

- `Ctrl+V`: 클립보드의 링크로 바로 조회
- `Ctrl+,`: 설정
- `Esc`: 팝업 닫기
- 오른쪽 아래 모서리: 창 크기 조절

사이트가 바뀌어 다운로드가 안 되면 설정 → **yt-dlp 업데이트**를 누르세요.

요구 사항: Windows 10/11 + [WebView2 런타임](https://developer.microsoft.com/microsoft-edge/webview2/)
(Windows 11에는 기본 포함, 없으면 실행 시 설치 안내가 뜹니다)

## 문제가 생기면

설정 → **로그 폴더**(`%LOCALAPPDATA%\ytdlp-gui\logs\app.log`)의 내용을 이슈에 첨부해 주세요.
yt-dlp의 전체 오류 메시지가 기록됩니다. 오류 문구는 앱 화면에서 드래그해 복사할 수 있어요.

- 로그인이 필요한 사이트(Vimeo 일부 등)는 아직 지원하지 않습니다.
- 진행 중인 라이브 방송은 받을 수 없습니다.

## 빌드

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name ytdlp-gui --add-data "ui;ui" main.py
```

결과물: `dist\ytdlp-gui.exe`

## 테스트

실제 네트워크를 쓰며, 인자로 준 작업 폴더를 앱 데이터·저장 폴더로 써서 실제 사용자 데이터는 건드리지 않습니다.

```powershell
venv\Scripts\python test_downloader.py <작업폴더>   # 설치·조회·다운로드·일시정지·취소
venv\Scripts\python test_e2e.py <작업폴더>          # 숨김 창으로 실제 UI 조작
```

## 라이선스

MIT. yt-dlp(Unlicense)와 ffmpeg(GPL 빌드)는 별도 프로그램으로 실행 시 내려받으며
이 저장소에 포함되지 않습니다.
