"""End-to-end test: opens a Tk text window, types keys with SendInput and checks that the
running Safa Brura (started with --accept-injected --no-tray) rewrites the words.

    start /b python autolang.py --no-tray --accept-injected
    python test_e2e.py

Run it only while nobody is using the machine: the synthetic keystrokes go to the foreground
window, so the test aborts as soon as its own window is no longer in the foreground.
"""
import ctypes
import json
import os
import sys
import time
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8")
import winapi as W
from engine import build_languages

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
user32.VkKeyScanExW.restype = ctypes.c_short

langs = {l.short: l for l in build_languages()}
EN, HE = langs["en"], langs["he"]
TEST_TAG = 0x5E57
CFG_PATH = os.path.join(os.environ["APPDATA"], "SafaBrura", "config.json")
try:
    IGNORE_BEFORE = json.load(open(CFG_PATH, encoding="utf-8")).get("ignore_words", [])
except Exception:
    IGNORE_BEFORE = []

root = tk.Tk()
root.title("SafaBrura e2e")
root.geometry("500x200+200+200")
text = tk.Text(root, font=("Arial", 14))
text.pack(fill="both", expand=True)
root.attributes("-topmost", True)
root.update()
TOP = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT: Tk's real top-level window
results = []


def key(vk, scan, up=False):
    inp = W.INPUT()
    inp.type = W.INPUT_KEYBOARD
    inp.u.ki = W.KEYBDINPUT(vk, scan, W.KEYEVENTF_KEYUP if up else 0, 0, TEST_TAG)
    return inp


def settle(sec=1.0):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def restore_config():
    """The undo scenarios add words to the user's ignore list; put back what was there before."""
    try:
        cfg = json.load(open(CFG_PATH, encoding="utf-8"))
        cfg["ignore_words"] = IGNORE_BEFORE
        json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        print("config cleanup failed:", e)


def assert_focused():
    if user32.GetForegroundWindow() != TOP:
        print("\nABORT: the test window lost the foreground (someone is using the machine?) - "
              "stopping so no keys go elsewhere")
        restore_config()
        root.destroy()
        raise SystemExit(2)


def type_as(txt, meant_lang):
    """Press the physical keys that would type `txt` in `meant_lang`'s layout (whatever layout is active)."""
    for ch in txt:
        assert_focused()
        if ch == " ":
            vk, shift = W.VK_SPACE, False
        elif ch == "\n":
            vk, shift = W.VK_RETURN, False
        else:
            r = user32.VkKeyScanExW(ch, meant_lang.hkl)
            vk, shift = r & 0xFF, bool((r >> 8) & 1)
        scan = user32.MapVirtualKeyExW(vk, 0, meant_lang.hkl)
        seq = []
        if shift:
            seq.append(key(W.VK_LSHIFT, 0x2A))
        seq += [key(vk, scan), key(vk, scan, up=True)]
        if shift:
            seq.append(key(W.VK_LSHIFT, 0x2A, up=True))
        W.send_inputs(seq)
        time.sleep(0.04)
        root.update()


def hotkey(shift=False):
    assert_focused()
    seq = [key(W.VK_PAUSE, 0x45), key(W.VK_PAUSE, 0x45, up=True)]
    if shift:
        seq = [key(W.VK_LSHIFT, 0x2A)] + seq + [key(W.VK_LSHIFT, 0x2A, up=True)]
    W.send_inputs(seq)


def set_layout(lang):
    W.request_layout(lang.hkl)
    settle(0.3)


def active_layout():
    return W.layout_id(W.current_layout())


def content():
    return text.get("1.0", "end-1c")


def clear():
    text.delete("1.0", "end")
    for attempt in range(10):  # a background process may be refused the foreground
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        me = kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != me:
            user32.AttachThreadInput(me, fg_tid, True)
        user32.SetForegroundWindow(TOP)
        user32.BringWindowToTop(TOP)
        if fg_tid and fg_tid != me:
            user32.AttachThreadInput(me, fg_tid, False)
        if attempt % 2 == 1:
            user32.ShowWindow(TOP, 6)  # SW_MINIMIZE
            settle(0.2)
            user32.ShowWindow(TOP, 9)  # SW_RESTORE
        text.focus_force()
        settle(0.3)
        if user32.GetForegroundWindow() == TOP:
            break
    else:
        restore_config()
        raise SystemExit("could not bring the test window to the foreground; aborting so no keys go elsewhere")
    assert_focused()
    W.send_inputs([key(W.VK_ESCAPE, 0x01), key(W.VK_ESCAPE, 0x01, up=True)])  # resets the app's word buffer
    settle(0.3)


def check(name, got, want, layout_want=None):
    ok = got == want and (layout_want is None or active_layout() == layout_want.id)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}: got {got!r} (layout {W.layout_language_tag(W.current_layout())}) want {want!r}")


# 1. Hebrew words typed while English layout is active
clear(); set_layout(EN)
type_as("akuo nv ", EN)  # the keys for שלום מה
settle(1.5)
check("he-in-en", content(), "שלום מה ", HE)

# 2. English word typed while Hebrew layout is active
clear(); set_layout(HE)
type_as("יקךךם ", HE)     # the keys for hello
settle(1.5)
check("en-in-he", content(), "hello ", EN)

# 3. Correct English must stay untouched
clear(); set_layout(EN)
type_as("this is fine. ", EN)
settle(1.0)
check("en-untouched", content(), "this is fine. ", EN)

# 4. Manual hotkey converts a word the auto rule skipped ('ak' looks like English)
clear(); set_layout(EN)
type_as("ak ", EN)
settle(0.8)
hotkey()
settle(1.2)
check("manual-pause", content(), "של ", HE)

# 5. Undo an automatic fix with the hotkey (word is then remembered in the ignore list)
clear(); set_layout(EN)
type_as("ahrv ", EN)   # שירה
settle(1.2)
mid = content()
hotkey()
settle(1.2)
check("undo", content(), "ahrv ", EN)
print("   (after auto-fix:", repr(mid), ")")

# 6. Shift+hotkey converts everything since the last reset, keeping words an auto-fix already handled
clear(); set_layout(HE)
type_as("שלום ", HE)
type_as("ךםהק ", HE)   # 'love' typed in Hebrew layout -> auto-fixed to English, layout now EN
settle(1.2)
type_as("ak ", EN)      # של typed while EN is active; 'ak' passes as English
settle(0.8)
hotkey(shift=True)
settle(1.5)
check("shift-hotkey-all", content(), "שלום love של ", HE)

# 7. Punctuation boundary: the '/' key is '.' in the Hebrew layout
clear(); set_layout(EN)
type_as("akuo/", EN)
settle(1.2)
check("punct-boundary", content(), "שלום.", HE)

print("\n%d/%d passed" % (sum(results), len(results)))
root.destroy()
restore_config()
