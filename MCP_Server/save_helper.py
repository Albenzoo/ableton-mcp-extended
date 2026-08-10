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

# 64-bit pointer handling: without these, ctypes truncates HGLOBAL/LPVOID
# return values to 32 bits, so GlobalLock returns 0.
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
user32.SetClipboardData.argtypes = [wt.UINT, wt.HGLOBAL]

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


WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_COMMAND = 0x0111
BN_CLICKED = 0
BM_CLICK = 0x00F5


def _find_dialog():
    """Find the top-level 'Save Live Set as:' dialog window (#32770)."""
    found = []

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if cls.value != "#32770":
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        title_text = title.value or ""
        if "Salva" in title_text or "Save" in title_text or "Live Set" in title_text:
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


VK_L = 0x4C


def _find_filename_combo(parent):
    """Find the file-name ComboBox (first ComboBox child of the dialog)."""
    found = []

    def cb(hwnd, lp):
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if cls.value == "ComboBox":
            found.append(hwnd)
        return True

    user32.EnumChildWindows(parent, WNDENUMPROC(cb), 0)
    return found[0] if found else None


def _find_button(parent, labels=("salva", "save")):
    """Find a Button child whose label (after removing &) is in labels."""
    WNDENUMPROC2 = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    result = [None]

    def cb(hwnd, lp):
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if cls.value == "Button":
            txt = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, txt, 64)
            label = (txt.value or "").replace("&", "").lower()
            if label in labels:
                result[0] = hwnd
                return False
        return True

    user32.EnumChildWindows(parent, WNDENUMPROC2(cb), 0)
    return result[0]


def _wait_for_dialog(timeout=8.0):
    """Wait up to timeout for a Save As dialog to appear; return it or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        dlg = _find_dialog()
        if dlg:
            return dlg
        time.sleep(0.1)
    return None


def save_as(path):
    """Perform Save As in Ableton by driving the Save As dialog.

    Verified working sequence for Ableton 12 on Windows:
      1. Open the Save As dialog (Ctrl+Shift+S).
      2. Ctrl+L focuses the dialog's address bar; paste the destination
         FOLDER path there and press Enter to navigate the dialog to it.
      3. Set ONLY the file name in the file-name ComboBox.
      4. Click the Save button.

    Note: Ableton always creates a "<name> Project" subfolder next to the
    requested path (standard Ableton behavior).
    """
    hwnd = find_ableton_main_hwnd()
    if not hwnd:
        return {"error": "Ableton main window not found"}

    dirpath = os.path.dirname(path)
    filename = os.path.basename(path)
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

    # Reuse an already-open Save As dialog, else open one.
    dlg = _find_dialog()
    if not dlg:
        _send_keys((VK_CONTROL, False), (VK_SHIFT, False), (VK_S, False),
                   (VK_S, True), (VK_SHIFT, True), (VK_CONTROL, True))
        dlg = _wait_for_dialog()
    if not dlg:
        return {"error": "Save As dialog did not appear"}

    # Make the dialog foreground so keystrokes reach it.
    user32.ShowWindow(dlg, 9)
    user32.SetForegroundWindow(dlg)
    time.sleep(0.4)

    # Ctrl+L -> focus the address bar.
    _send_keys((VK_CONTROL, False), (VK_L, False), (VK_L, True), (VK_CONTROL, True))
    time.sleep(0.5)

    # Paste the destination folder path and press Enter to navigate.
    _set_clipboard_text(dirpath)
    _send_keys((VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True))
    time.sleep(0.3)
    _send_keys((VK_RETURN, False), (VK_RETURN, True))
    time.sleep(1.5)

    # Set only the file name in the file-name ComboBox.
    combo = _find_filename_combo(dlg)
    if not combo:
        return {"error": "file-name field not found"}
    user32.SendMessageW(combo, WM_SETTEXT, 0, ctypes.c_wchar_p(filename))
    time.sleep(0.3)

    # Click the Save button. Give Ableton a moment to finish navigating and
    # enable the button; retry a few times.
    save_btn = None
    for _ in range(10):
        save_btn = _find_button(dlg, ("salva", "save"))
        if save_btn:
            break
        time.sleep(0.3)
    if save_btn:
        user32.SendMessageW(save_btn, BM_CLICK, 0, 0)
    else:
        # Fallback: press Enter.
        _send_keys((VK_RETURN, False), (VK_RETURN, True))
    time.sleep(0.5)

    return {"status": "ok", "path": path, "hwnd": hwnd, "dialog": dlg}


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
