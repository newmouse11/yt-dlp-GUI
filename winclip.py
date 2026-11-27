"""Windows 클립보드 텍스트 읽기.

WebView2의 navigator.clipboard.readText()는 권한 요청이 뜨거나 실패할 수 있어
'링크 붙여넣기' 버튼은 Python 쪽에서 직접 읽는다.
"""
import ctypes
import logging

log = logging.getLogger(__name__)

CF_UNICODETEXT = 13
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.GetClipboardData.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]


def read_text() -> str:
    if not _user32.OpenClipboard(None):
        log.warning("클립보드 열기 실패 (다른 프로그램이 사용 중)")
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ""
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()
