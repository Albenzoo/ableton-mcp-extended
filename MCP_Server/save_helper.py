"""Ableton Save Helper - bridges the Remote Script's missing save API.

Ableton Live 12's Python API exposes NO save method (verified by runtime
introspection: Song has only read-only file_path; Application exposes no
save_live_set / save_as / save_live_set_as). Its embedded Python is also
stripped of ctypes/subprocess, so the Remote Script can't send Ctrl+Shift+S
itself.

This helper runs as a normal user process (full Win32 privileges). It:
  1. Creates the destination directory if missing.
  2. Brings Ableton's main window to the foreground.
  3. Sends Ctrl+Shift+S to open the "Save As" dialog.
  4. Pastes the full destination path from the clipboard into the
     file-name field (Ctrl+A then Ctrl+V).
  5. Presses Enter to confirm.

The Remote Script's save_project command POSTs to /save_as. The MCP server
starts this helper automatically if it isn't already running.

Protocol (HTTP on 127.0.0.1:9878):
  GET  /ping    -> {"pong": true}
  POST /save_as -> body {"path": "C:\\...\\file.als"} -> {"status":"ok","path":...}
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HELPER_HOST = "127.0.0.1"
HELPER_PORT = 9878
ABLETON_CLASS = "Ableton Live Window Class"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def find_ableton_main_hwnd():
    """Largest visible window whose class is Ableton's main class."""
    best = [None, 0]

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if cls.value != ABLETON_CLASS:
            return True
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > best[1]:
            best[0] = hwnd
            best[1] = area
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return best[0]


PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong),
                ("wParamL", ctypes.c_short),
                ("wParamH", ctypes.c_ushort)]


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", InputUnion)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_S = 0x53
VK_A = 0x41
VK_V = 0x56
VK_RETURN = 0x0D


def _key(vk, up=False):
    extra = ctypes.c_ulong(0)
    ki = KeyBdInput(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, ctypes.pointer(extra))
    return Input(INPUT_KEYBOARD, InputUnion(ki=ki))


def _unicode_key(char, up=False):
    extra = ctypes.c_ulong(0)
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    ki = KeyBdInput(0, ord(char), flags, 0, ctypes.pointer(extra))
    return Input(INPUT_KEYBOARD, InputUnion(ki=ki))


def _send_keys(*key_descs):
    """Send a sequence of (vk, down/up) or (unicode char, down/up) events."""
    events = []
    for desc in key_descs:
        if isinstance(desc, tuple) and len(desc) == 2 and isinstance(desc[1], bool):
            vk, up = desc
            events.append(_key(vk, up))
        else:
            ch, up = desc
            events.append(_unicode_key(ch, up))
    arr = (Input * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(Input))


def _set_clipboard_text(text):
    """Put text into the Windows clipboard (UTF-16)."""
    CF_UNICODETEXT = 13
    user32.OpenClipboard(0)
    try:
        user32.EmptyClipboard()
        # Allocate global memory and copy the UTF-16 string.
        data = (text + "\x00").encode("utf-16-le")
        hmem = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE|GMEM_ZEROINIT
        ptr = kernel32.GlobalLock(hmem)
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(hmem)
        user32.SetClipboardData(CF_UNICODETEXT, hmem)
    finally:
        user32.CloseClipboard()


def save_as(path):
    """Perform Save As in Ableton by simulating the keyboard shortcut."""
    hwnd = find_ableton_main_hwnd()
    if not hwnd:
        return {"error": "Ableton main window not found"}

    dirpath = os.path.dirname(path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

    # Bring Ableton to foreground.
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.AllowSetForegroundWindow(0xFFFFFFFF)
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except Exception:
        pass
    time.sleep(0.3)

    # Ctrl+Shift+S -> open Save As dialog.
    _send_keys((VK_CONTROL, False), (VK_SHIFT, False), (VK_S, False),
               (VK_S, True), (VK_SHIFT, True), (VK_CONTROL, True))
    time.sleep(0.8)

    # Put the destination path on the clipboard.
    _set_clipboard_text(path)

    # Select-all then paste the full path into the file-name field.
    _send_keys((VK_CONTROL, False), (VK_A, False),
               (VK_A, True), (VK_CONTROL, True))
    time.sleep(0.2)
    _send_keys((VK_CONTROL, False), (VK_V, False),
               (VK_V, True), (VK_CONTROL, True))
    time.sleep(0.3)

    # Confirm.
    _send_keys((VK_RETURN, False), (VK_RETURN, True))
    time.sleep(0.5)

    return {"status": "ok", "path": path, "hwnd": hwnd}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/ping"):
            self._reply(200, {"pong": True})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/save_as"):
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._reply(400, {"error": "bad request: %s" % e})
            return
        path = payload.get("path", "")
        if not path or not path.strip():
            self._reply(400, {"error": "path parameter is required"})
            return
        result = save_as(path)
        code = 200 if result.get("status") == "ok" else 500
        self._reply(code, result)


def main():
    server = ThreadingHTTPServer((HELPER_HOST, HELPER_PORT), Handler)
    # Daemon threads so Ctrl+C still exits cleanly.
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
