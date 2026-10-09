"""Native Windows task dialog (TaskDialogIndirect) for the first-run explanation.

Tk cannot lay out bidirectional text; the Windows task dialog renders Hebrew with mixed Latin
correctly when asked for a right-to-left layout, and gives us a verification checkbox and a
hyperlink for free.
"""
import ctypes
from ctypes import wintypes

TDF_ENABLE_HYPERLINKS = 0x0001
TDF_ALLOW_DIALOG_CANCELLATION = 0x0008
TDF_CALLBACK_TIMER = 0x0800
TDF_RTL_LAYOUT = 0x2000
TDF_SIZE_TO_CONTENT = 0x01000000
TDF_VERIFICATION_FLAG_CHECKED = 0x0100
TDN_HYPERLINK_CLICKED = 3
TDN_TIMER = 4
TDM_CLICK_BUTTON = 0x0400 + 102
TD_INFORMATION_ICON = 0xFFFF - 2  # MAKEINTRESOURCEW(-3)

PFTASKDIALOGCALLBACK = ctypes.WINFUNCTYPE(ctypes.HRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                          wintypes.LPARAM, ctypes.c_ssize_t)


class TASKDIALOG_BUTTON(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("nButtonID", ctypes.c_int), ("pszButtonText", wintypes.LPCWSTR)]


class TASKDIALOGCONFIG(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwndParent", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("dwFlags", wintypes.DWORD),
        ("dwCommonButtons", wintypes.DWORD),
        ("pszWindowTitle", wintypes.LPCWSTR),
        ("pszMainIcon", wintypes.LPCWSTR),
        ("pszMainInstruction", wintypes.LPCWSTR),
        ("pszContent", wintypes.LPCWSTR),
        ("cButtons", wintypes.UINT),
        ("pButtons", ctypes.POINTER(TASKDIALOG_BUTTON)),
        ("nDefaultButton", ctypes.c_int),
        ("cRadioButtons", wintypes.UINT),
        ("pRadioButtons", ctypes.c_void_p),
        ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", wintypes.LPCWSTR),
        ("pszExpandedInformation", wintypes.LPCWSTR),
        ("pszExpandedControlText", wintypes.LPCWSTR),
        ("pszCollapsedControlText", wintypes.LPCWSTR),
        ("pszFooterIcon", wintypes.LPCWSTR),
        ("pszFooter", wintypes.LPCWSTR),
        ("pfCallback", PFTASKDIALOGCALLBACK),
        ("lpCallbackData", ctypes.c_ssize_t),
        ("cxWidth", wintypes.UINT),
    ]


def show_task_dialog(title, heading, content, buttons, footer=None, verify_text=None, verify_checked=True,
                     on_hyperlink=None, auto_close_ms=None, width=320):
    """Show a right-to-left task dialog. Returns (button_id, verify_checked) or None if unavailable.

    buttons: list of (id, text); the first one is the default.
    footer: text that may contain <a href="...">links</a> (on_hyperlink gets the href).
    """
    try:
        comctl = ctypes.windll.comctl32
        func = comctl.TaskDialogIndirect
    except (OSError, AttributeError):
        return None
    func.argtypes = [ctypes.POINTER(TASKDIALOGCONFIG), ctypes.POINTER(ctypes.c_int),
                     ctypes.POINTER(ctypes.c_int), ctypes.POINTER(wintypes.BOOL)]
    func.restype = ctypes.HRESULT

    def callback(hwnd, msg, wparam, lparam, data):
        if msg == TDN_HYPERLINK_CLICKED and on_hyperlink:
            try:
                on_hyperlink(ctypes.wstring_at(lparam))
            except Exception:
                pass
        elif msg == TDN_TIMER and auto_close_ms and wparam >= auto_close_ms:
            ctypes.windll.user32.SendMessageW(hwnd, TDM_CLICK_BUTTON, buttons[0][0], 0)
        return 0

    cb = PFTASKDIALOGCALLBACK(callback)
    btn_arr = (TASKDIALOG_BUTTON * len(buttons))(*[TASKDIALOG_BUTTON(i, t) for i, t in buttons])
    cfg = TASKDIALOGCONFIG()
    cfg.cbSize = ctypes.sizeof(TASKDIALOGCONFIG)
    cfg.dwFlags = TDF_RTL_LAYOUT | TDF_ALLOW_DIALOG_CANCELLATION | TDF_ENABLE_HYPERLINKS | TDF_SIZE_TO_CONTENT
    if verify_checked:
        cfg.dwFlags |= TDF_VERIFICATION_FLAG_CHECKED
    if auto_close_ms:
        cfg.dwFlags |= TDF_CALLBACK_TIMER
    cfg.pszWindowTitle = title
    cfg.pszMainIcon = ctypes.cast(ctypes.c_void_p(TD_INFORMATION_ICON), wintypes.LPCWSTR)
    cfg.pszMainInstruction = heading
    cfg.pszContent = content
    cfg.cButtons = len(buttons)
    cfg.pButtons = btn_arr
    cfg.nDefaultButton = buttons[0][0]
    cfg.pszVerificationText = verify_text
    cfg.pszFooter = footer
    cfg.pfCallback = cb
    cfg.cxWidth = width
    pressed = ctypes.c_int(0)
    radio = ctypes.c_int(0)
    checked = wintypes.BOOL(0)
    try:
        func(ctypes.byref(cfg), ctypes.byref(pressed), ctypes.byref(radio), ctypes.byref(checked))
    except OSError:
        return None
    return pressed.value, bool(checked.value)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    r = show_task_dialog("שפה ברורה · Safa Brura", "ברוכים הבאים לשפה ברורה",
                         'הקלדת "akuo" ורווח כשהמקלדת באנגלית? יהפוך ל"שלום".\nמקש Pause או Shift+Backspace מתקן ידנית.',
                         [(100, "הבנתי, התחל")], footer='<a href="https://example.com">הסבר מלא באינטרנט</a>',
                         verify_text="הפעל אוטומטית בכל כניסה לווינדוס", on_hyperlink=print,
                         auto_close_ms=int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print("result:", r)
