"""Win32 / COM plumbing for Safa Brura: keyboard layouts, key-to-char, SendInput,
low-level hooks, foreground window info and UI Automation password detection."""
import ctypes
from ctypes import POINTER, c_int, c_void_p, c_uint, c_long, HRESULT, wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---- types / constants ---------------------------------------------------
ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
HKL = c_void_p

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x201, 0x204, 0x207
WM_INPUTLANGCHANGEREQUEST = 0x50
WM_QUIT = 0x12
LLKHF_INJECTED = 0x10
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x2, 0x4, 0x8
INPUT_KEYBOARD = 1
VK_BACK, VK_TAB, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU, VK_PAUSE, VK_CAPITAL = 0x08, 0x09, 0x0D, 0x10, 0x11, 0x12, 0x13, 0x14
VK_ESCAPE, VK_SPACE, VK_END, VK_HOME, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_DELETE = 0x1B, 0x20, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E
VK_LWIN, VK_RWIN, VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
VK_PRIOR, VK_NEXT = 0x21, 0x22


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", c_long), ("dy", c_long), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, c_int, wintypes.WPARAM, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.GetKeyboardLayout.restype = HKL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayoutList.argtypes = [c_int, POINTER(HKL)]
user32.ToUnicodeEx.argtypes = [c_uint, c_uint, POINTER(ctypes.c_ubyte), wintypes.LPWSTR, c_int, c_uint, HKL]
user32.MapVirtualKeyExW.argtypes = [c_uint, c_uint, HKL]
user32.SendInput.argtypes = [c_uint, POINTER(INPUT), c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, c_int]
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, POINTER(GUITHREADINFO)]
user32.GetKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, POINTER(wintypes.DWORD)]

INJECT_TAG = 0xA7101A96  # dwExtraInfo marker so the hook can recognise our own injected input


# ---- keyboard layouts ------------------------------------------------------
def installed_layouts():
    """Return the list of HKL handles installed for this user."""
    n = user32.GetKeyboardLayoutList(0, None)
    arr = (HKL * n)()
    user32.GetKeyboardLayoutList(n, arr)
    return [h for h in arr]


def layout_language_tag(hkl):
    """'he-IL', 'en-US', ... for a layout handle."""
    lcid = int(hkl or 0) & 0xFFFF
    buf = ctypes.create_unicode_buffer(85)
    kernel32.LCIDToLocaleName(lcid, buf, 85, 0)
    return buf.value


def layout_id(hkl):
    """Low 16 bits identify the language; good enough to compare layouts across threads."""
    return int(hkl or 0) & 0xFFFF


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def focus_window():
    """Best-effort handle of the window that really receives keystrokes (handles UWP frames)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    if window_class(hwnd) == "ApplicationFrameWindow":
        found = []

        def cb(child, _):
            if window_class(child) == "Windows.UI.Core.CoreWindow":
                found.append(child)
                return False
            return True
        user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
        if found:
            hwnd = found[0]
            tid = user32.GetWindowThreadProcessId(hwnd, None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)) and gti.hwndFocus:
        return gti.hwndFocus, tid
    return hwnd, tid


def current_layout():
    """HKL active in the window that has the keyboard focus."""
    hwnd, tid = focus_window()
    if tid is None:
        return user32.GetKeyboardLayout(0)
    return user32.GetKeyboardLayout(tid)


def foreground_process_name():
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def request_layout(hkl):
    """Ask the focused window to switch to the given layout."""
    lp = int(hkl)
    if lp >= 2 ** 63:  # HKL is a sign-extended pointer-sized value; LPARAM is signed
        lp -= 2 ** 64
    hwnd, _ = focus_window()
    if hwnd:
        user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, lp)
    fg = user32.GetForegroundWindow()
    if fg and fg != hwnd:
        user32.PostMessageW(fg, WM_INPUTLANGCHANGEREQUEST, 0, lp)


def key_to_char(vk, scan, hkl, shift=False, caps=False):
    """Character the key would produce under the layout (empty string for non-character keys)."""
    ks = (ctypes.c_ubyte * 256)()
    if shift:
        ks[VK_SHIFT] = 0x80
    if caps:
        ks[VK_CAPITAL] = 0x01
    buf = ctypes.create_unicode_buffer(8)
    n = user32.ToUnicodeEx(vk, scan, ks, buf, 8, 0x4, hkl)  # 0x4: don't touch the keyboard state
    return buf.value[:n] if n > 0 else ""


# ---- input injection -----------------------------------------------------
def _key_input(vk=0, scan=0, flags=0):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, INJECT_TAG)
    return inp


def send_inputs(inputs):
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def send_backspaces(n):
    inputs = []
    for _ in range(n):
        inputs.append(_key_input(VK_BACK, 0, 0))
        inputs.append(_key_input(VK_BACK, 0, KEYEVENTF_KEYUP))
    send_inputs(inputs)


def _text_inputs(text):
    inputs = []
    for ch in text:
        cp = ord(ch)
        units = [cp] if cp < 0x10000 else [0xD800 + ((cp - 0x10000) >> 10), 0xDC00 + ((cp - 0x10000) & 0x3FF)]
        for cu in units:
            inputs.append(_key_input(0, cu, KEYEVENTF_UNICODE))
            inputs.append(_key_input(0, cu, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return inputs


def send_text(text):
    send_inputs(_text_inputs(text))


def replace_text(n_backspaces, text):
    """Delete n characters and type `text` in a single SendInput call (all inputs built up front)."""
    inputs = []
    for _ in range(n_backspaces):
        inputs.append(_key_input(VK_BACK, 0, 0))
        inputs.append(_key_input(VK_BACK, 0, KEYEVENTF_KEYUP))
    inputs.extend(_text_inputs(text))
    send_inputs(inputs)


def send_vk(vk, scan=0):
    send_inputs([_key_input(vk, scan, 0), _key_input(vk, scan, KEYEVENTF_KEYUP)])


def send_combo(vks):
    down = [_key_input(vk, 0, 0) for vk in vks]
    up = [_key_input(vk, 0, KEYEVENTF_KEYUP) for vk in reversed(vks)]
    send_inputs(down + up)


# ---- UI Automation: is the focused control a password box? ----------------
_uia = None
UIA_IsPasswordPropertyId = 30019


def _init_uia():
    global _uia
    import comtypes
    from comtypes import GUID, IUnknown, COMMETHOD, CoCreateInstance
    from comtypes.automation import VARIANT

    class IUIAutomationElement(IUnknown):
        _iid_ = GUID("{d22108aa-8ac5-49a5-837b-37bbb3d7591e}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetFocus"),
            COMMETHOD([], HRESULT, "GetRuntimeId", (["out"], POINTER(c_void_p), "runtimeId")),
            COMMETHOD([], HRESULT, "FindFirst", (["in"], c_int, "scope"), (["in"], c_void_p, "condition"), (["out"], POINTER(c_void_p), "found")),
            COMMETHOD([], HRESULT, "FindAll", (["in"], c_int, "scope"), (["in"], c_void_p, "condition"), (["out"], POINTER(c_void_p), "found")),
            COMMETHOD([], HRESULT, "FindFirstBuildCache", (["in"], c_int, "scope"), (["in"], c_void_p, "condition"), (["in"], c_void_p, "cache"), (["out"], POINTER(c_void_p), "found")),
            COMMETHOD([], HRESULT, "FindAllBuildCache", (["in"], c_int, "scope"), (["in"], c_void_p, "condition"), (["in"], c_void_p, "cache"), (["out"], POINTER(c_void_p), "found")),
            COMMETHOD([], HRESULT, "BuildUpdatedCache", (["in"], c_void_p, "cache"), (["out"], POINTER(c_void_p), "updated")),
            COMMETHOD([], HRESULT, "GetCurrentPropertyValue", (["in"], c_int, "propertyId"), (["out"], POINTER(VARIANT), "retVal")),
        ]

    class IUIAutomation(IUnknown):
        _iid_ = GUID("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")
        _methods_ = [
            COMMETHOD([], HRESULT, "CompareElements", (["in"], c_void_p, "el1"), (["in"], c_void_p, "el2"), (["out"], POINTER(c_int), "same")),
            COMMETHOD([], HRESULT, "CompareRuntimeIds", (["in"], c_void_p, "r1"), (["in"], c_void_p, "r2"), (["out"], POINTER(c_int), "same")),
            COMMETHOD([], HRESULT, "GetRootElement", (["out"], POINTER(POINTER(IUIAutomationElement)), "root")),
            COMMETHOD([], HRESULT, "ElementFromHandle", (["in"], c_void_p, "hwnd"), (["out"], POINTER(POINTER(IUIAutomationElement)), "element")),
            COMMETHOD([], HRESULT, "ElementFromPoint", (["in"], wintypes.POINT, "pt"), (["out"], POINTER(POINTER(IUIAutomationElement)), "element")),
            COMMETHOD([], HRESULT, "GetFocusedElement", (["out"], POINTER(POINTER(IUIAutomationElement)), "element")),
        ]

    comtypes.CoInitialize()
    _uia = CoCreateInstance(GUID("{ff48dba4-60ef-4201-aa87-54103eef594e}"), interface=IUIAutomation)


def focused_is_password():
    """True if UI Automation reports the focused control as a password field. Errors -> False."""
    try:
        if _uia is None:
            _init_uia()
        el = _uia.GetFocusedElement()
        if not el:
            return False
        return bool(el.GetCurrentPropertyValue(UIA_IsPasswordPropertyId))
    except Exception:
        return False


def message_loop():
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def post_quit():
    user32.PostThreadMessageW(kernel32.GetCurrentThreadId(), WM_QUIT, 0, 0)


def beep():
    kernel32.Beep(880, 60)
