# ytdlp-gui

yt-dlp 기반 Windows 동영상 다운로더. 링크를 붙여넣고 화질을 고르면 끝.

- 화질 선택 (최대 4K) / MP3·M4A 오디오 추출
- 재생목록: 항목을 골라서 일괄 다운로드
- 대기열: 동시 다운로드 수 설정, 일시정지·이어받기·재시도
- 썸네일·제목·길이 미리보기
- 라이트/다크 모드 자동 전환

## 사용

`ytdlp-gui.exe`를 실행하세요. 처음 실행하면 yt-dlp와 ffmpeg를
`%LOCALAPPDATA%\ytdlp-gui\bin`에 자동으로 내려받아요 (1회).

- `Ctrl+V`: 클립보드의 링크로 바로 조회
- `Ctrl+,`: 설정
- `Esc`: 팝업 닫기

사이트가 바뀌어 다운로드가 안 되면 설정 → **yt-dlp 업데이트**를 누르세요.

요구 사항: Windows 10/11 (WebView2 런타임, Windows 11에는 기본 포함)

## 빌드

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name ytdlp-gui --add-data "ui;ui" main.py
```

결과물: `dist\ytdlp-gui.exe`

## 라이선스

MIT. yt-dlp(Unlicense)와 ffmpeg(GPL 빌드)는 별도 프로그램으로 실행 시 내려받으며
이 저장소에 포함되지 않습니다.
