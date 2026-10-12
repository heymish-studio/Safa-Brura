"""macOS stand-in for winapi.py.

The shared code (engine.py, autolang.py) talks to the operating system through a module it imports as
`winapi`. On a Mac this module is registered under that name instead, exposing the same functions and
constants on top of Quartz event taps, Text Input Sources (TIS) and AppKit.

Everything that needs macOS is imported lazily or guarded, so the pure parts (key tables, constants,
the INPUT shim) can be exercised by tests on any OS.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keymap as K  # noqa: E402

try:  # macOS only
    import Quartz
    from AppKit import NSWorkspace, NSSound
    from Foundation import NSThread
    from PyObjCTools import AppHelper
    HAVE_MAC = True
except Exception:  # pragma: no cover - lets tests import this module on other systems
    Quartz = None
    HAVE_MAC = False

# ---- constants the shared code expects (Windows values; only used as identifiers) --------------
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x201, 0x204, 0x207
WM_QUIT = 0x12
WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
LLKHF_INJECTED = 0x10
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
INPUT_KEYBOARD = 1
VK_BACK, VK_TAB, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU, VK_PAUSE, VK_CAPITAL = 0x08, 0x09, 0x0D, 0x10, 0x11, 0x12, 0x13, 0x14
VK_ESCAPE, VK_SPACE, VK_END, VK_HOME, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_DELETE = 0x1B, 0x20, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E
VK_LWIN, VK_RWIN, VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
VK_PRIOR, VK_NEXT = 0x21, 0x22
INJECT_TAG = 0xA7101A96   # same marker values as on Windows
REPLAY_TAG = 0xA7101A97
HOOKPROC = None


class KEYBDINPUT:
    def __init__(self, wVk=0, wScan=0, dwFlags=0, time=0, dwExtraInfo=0):
        self.wVk, self.wScan, self.dwFlags, self.time, self.dwExtraInfo = wVk, wScan, dwFlags, time, dwExtraInfo


class _Union:
    def __init__(self):
        self.ki = KEYBDINPUT()


class INPUT:
    def __init__(self):
        self.type = INPUT_KEYBOARD
        self.u = _Union()


class KeyEvent:
    """What the shared key handler reads from a hook event."""
    def __init__(self, vkCode, scanCode, flags=0, dwExtraInfo=0):
        self.vkCode, self.scanCode, self.flags, self.dwExtraInfo = vkCode, scanCode, flags, dwExtraInfo
        self.time = 0


# ---- modifier state of the event being handled ------------------------------------------------
_state = {"flags": 0}
SHIFT_MASK, CTRL_MASK, ALT_MASK, CMD_MASK, CAPS_MASK = 0x20000, 0x40000, 0x80000, 0x100000, 0x10000


def set_event_flags(flags):
    _state["flags"] = int(flags)


def modifiers_down():
    f = _state["flags"]
    mods = set()
    if f & SHIFT_MASK:
        mods.add("shift")
    if f & CTRL_MASK:
        mods.add("ctrl")
    if f & ALT_MASK:
        mods.add("alt")
    if f & CMD_MASK:
        mods.add("win")
    return mods


class _User32:
    @staticmethod
    def GetKeyState(vk):
        return 1 if (vk == VK_CAPITAL and _state["flags"] & CAPS_MASK) else 0

    @staticmethod
    def GetAsyncKeyState(vk):
        return 0

    @staticmethod
    def PostThreadMessageW(*a):
        return 0

    @staticmethod
    def GetForegroundWindow():
        return focus_window()[0]


class _Kernel32:
    @staticmethod
    def GetCurrentThreadId():
        return threading.get_ident()


user32 = _User32()
kernel32 = _Kernel32()


# ---- main-thread helper (TIS and AppKit must be used on the main thread on macOS 14+) ----------
def on_main(fn, timeout=1.5):
    if not HAVE_MAC or NSThread.isMainThread():
        return fn()
    box, done = {}, threading.Event()

    def run():
        try:
            box["v"] = fn()
        except Exception as e:  # pragma: no cover
            box["e"] = e
        finally:
            done.set()
    AppHelper.callAfter(run)
    done.wait(timeout)
    if "e" in box:
        raise box["e"]
    return box.get("v")


# ---- Text Input Sources -----------------------------------------------------------------------
_tis = {}


def _load_tis():
    if _tis:
        return _tis
    import objc
    from Foundation import NSBundle
    bundle = NSBundle.bundleWithIdentifier_("com.apple.HIToolbox")
    objc.loadBundleFunctions(bundle, _tis, [
        ("TISCopyCurrentKeyboardInputSource", b"@"),
        ("TISGetInputSourceProperty", b"@@@"),
        ("TISSelectInputSource", b"i@"),
        ("TISCreateInputSourceList", b"@@Z"),
        ("IsSecureEventInputEnabled", b"Z"),
    ])
    objc.loadBundleVariables(bundle, _tis, [
        ("kTISPropertyInputSourceID", b"@"),
        ("kTISPropertyInputSourceCategory", b"@"),
        ("kTISCategoryKeyboardInputSource", b"@"),
        ("kTISPropertyInputSourceIsSelectCapable", b"@"),
    ])
    return _tis


def _source_id(src):
    t = _load_tis()
    return str(t["TISGetInputSourceProperty"](src, t["kTISPropertyInputSourceID"]) or "")


def enabled_sources():
    """[(source_id, layout_id, source)] for every enabled keyboard input source."""
    def run():
        t = _load_tis()
        out = []
        for src in t["TISCreateInputSourceList"](None, False) or []:
            cat = t["TISGetInputSourceProperty"](src, t["kTISPropertyInputSourceCategory"])
            if cat != t["kTISCategoryKeyboardInputSource"]:
                continue
            sid = _source_id(src)
            out.append((sid, K.classify_source(sid), src))
        return out
    return on_main(run) or []


def installed_layouts():
    ids = []
    for _, lid, _ in enabled_sources():
        if lid and lid not in ids:
            ids.append(lid)
    return ids


def layout_language_tag(hkl):
    return K.TAGS.get(layout_id(hkl), "")


def layout_id(hkl):
    return int(hkl or 0) & 0xFFFF


def current_source_id():
    def run():
        t = _load_tis()
        return _source_id(t["TISCopyCurrentKeyboardInputSource"]())
    return on_main(run) or ""


def current_layout():
    return K.classify_source(current_source_id())


def request_layout(hkl):
    want = layout_id(hkl)

    def run():
        t = _load_tis()
        for sid, lid, src in enabled_sources():
            if lid == want:
                t["TISSelectInputSource"](src)
                return True
        return False
    return on_main(run)


def focused_is_password():
    """macOS turns on Secure Input for password fields (event taps then see nothing anyway)."""
    try:
        return bool(_load_tis()["IsSecureEventInputEnabled"]())
    except Exception:
        return False


# ---- windows / apps ---------------------------------------------------------------------------
def _front_app():
    if not HAVE_MAC:
        return None
    return NSWorkspace.sharedWorkspace().frontmostApplication()


def focus_window():
    app = _front_app()
    pid = int(app.processIdentifier()) if app is not None else 0
    return pid, pid


def foreground_process_name():
    app = _front_app()
    if app is None:
        return ""
    return str(app.bundleIdentifier() or app.localizedName() or "").lower()


def window_class(hwnd):
    return ""


def key_to_char(vk, scan, hkl, shift=False, caps=False):
    return K.char_for(vk, layout_id(hkl), shift, caps)


# ---- posting keyboard events ------------------------------------------------------------------
def _post(keycode, down, tag=INJECT_TAG, text=None, flags=0):
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    ev = Quartz.CGEventCreateKeyboardEvent(src, keycode, down)
    if text is not None:
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
    Quartz.CGEventSetFlags(ev, flags)
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGEventSourceUserData, tag)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _events_for_text(text):
    return [(0, True, ch) for ch in text]


def replace_text(n_backspaces, text):
    """Delete n characters and type `text`. All events are prepared before the first one is posted."""
    plan = [(0x33, True, None), (0x33, False, None)] * n_backspaces
    for ch in text:
        plan += [(0, True, ch), (0, False, ch)]
    for keycode, down, ch in plan:
        _post(keycode, down, text=ch)
        time.sleep(0.002)


def send_backspaces(n):
    replace_text(n, "")


def send_text(text):
    replace_text(0, text)


def send_vk(vk, scan=0):
    keycode = scan if scan else K.VK_TO_MAC.get(vk)
    if keycode is None:
        return
    _post(keycode, True)
    _post(keycode, False)


def send_inputs(inputs):
    """Replay queued keystrokes (used for keys pressed while a fix was in progress)."""
    held = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState) if HAVE_MAC else 0
    for inp in inputs:
        ki = inp.u.ki
        keycode = ki.wScan if ki.wScan else K.VK_TO_MAC.get(ki.wVk)
        if keycode is None:
            continue
        _post(keycode, not (ki.dwFlags & KEYEVENTF_KEYUP), tag=ki.dwExtraInfo or INJECT_TAG, flags=held)
        time.sleep(0.002)


def send_combo(vks):
    pass  # no fallback layout-switch shortcut on macOS; TISSelectInputSource is used instead


def beep():
    try:
        on_main(lambda: NSSound.soundNamed_("Tink").play())
    except Exception:
        pass


def message_loop():
    pass


def post_quit():
    pass
