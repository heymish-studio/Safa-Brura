"""Offline simulation of the real key handler and fix routine on a virtual screen.
No keystrokes are sent anywhere: every Win32 output call is replaced by an edit of a Python string.
Run: python test_sentence.py
"""
import ctypes
import sys

sys.stdout.reconfigure(encoding="utf-8")
import winapi as W
import autolang
from engine import build_languages

user32 = ctypes.windll.user32
user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
user32.VkKeyScanExW.restype = ctypes.c_short

langs = {l.short: l for l in build_languages()}
EN, HE = langs["en"], langs["he"]


class Sim:
    def __init__(self):
        self.screen = ""
        self.layout = EN
        self.shift = False
        self.ctrl = False


sim = Sim()

# ---- replace every output / environment call with the simulation -----------
def send_backspaces(n):
    sim.screen = sim.screen[:-n] if n else sim.screen


def send_text(t):
    sim.screen += t


def send_vk(vk, scan=0):
    if vk == W.VK_SPACE:
        sim.screen += " "
    elif vk == W.VK_RETURN:
        sim.screen += "\n"


W.send_backspaces = send_backspaces
W.replace_text = lambda n, t: (send_backspaces(n), send_text(t))
W.send_text = send_text
W.send_vk = send_vk
W.send_inputs = lambda inputs: None
W.send_combo = lambda vks: None
W.beep = lambda: None
W.focus_window = lambda: (1, 1)
W.current_layout = lambda: sim.layout.hkl
W.focused_is_password = lambda: False
W.foreground_process_name = lambda: "notepad.exe"
W.window_class = lambda h: "Edit"
autolang.modifiers_down = lambda: ({"shift"} if sim.shift else set()) | ({"ctrl"} if sim.ctrl else set())
autolang.save_config = lambda cfg: None

app = autolang.AutoLanguage()
app.cfg["ignore_words"] = []
app.init_engine()


def switch_layout(target):
    sim.layout = target
    app.layout_id = target.id


app._switch_layout = switch_layout


def press(vk, scan, char_if_passed):
    kb = W.KBDLLHOOKSTRUCT(vk, scan, 0, 0, 0)
    swallowed = app._handle_key(kb, W.WM_KEYDOWN)
    if not swallowed:
        if vk == W.VK_BACK:
            sim.screen = sim.screen[:-1]
        else:
            sim.screen += char_if_passed
    while not app.jobs.empty():  # run the worker synchronously
        kind, job = app.jobs.get()
        if kind == "fix":
            app._do_fix(job)
        elif kind == "manual":
            app._do_manual(job)
        app._end_fix()


def type_keys(text, meant):
    """Press the keys that type `text` in layout `meant`, whatever layout is active."""
    for ch in text:
        if ch == " ":
            press(W.VK_SPACE, 0x39, " ")
            continue
        r = user32.VkKeyScanExW(ch, meant.hkl)
        vk, shift = r & 0xFF, bool((r >> 8) & 1)
        scan = user32.MapVirtualKeyExW(vk, 0, meant.hkl)
        sim.shift = shift
        shown = W.key_to_char(vk, scan, sim.layout.hkl, shift)
        press(vk, scan, shown)
        sim.shift = False


def hotkey_undo():
    sim.shift = True
    press(W.VK_BACK, 0x0E, "")
    sim.shift = False


def reset(layout):
    sim.screen = ""
    sim.layout = layout
    app.reset()
    app.layout_id = layout.id


results = []


def check(name, want, want_layout=None):
    ok = sim.screen == want and (want_layout is None or sim.layout.id == want_layout.id)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}: {sim.screen!r} (layout {sim.layout.tag})" + ("" if ok else f"  want {want!r}"))


reset(EN); type_keys("אם אתה רוצה לדוגמא ", HE)
check("the reported case: אם at the start", "אם אתה רוצה לדוגמא ", HE)

reset(EN); type_keys("גם אני רוצה ", HE)
check("short word גם (=do) at the start", "גם אני רוצה ", HE)

reset(EN); type_keys("של אבא שלי ", HE)
check("של (=ak) then more words", "של אבא שלי ", HE)

reset(HE); type_keys("do you want ", EN)
check("English in Hebrew layout: do (=גם) at the start", "do you want ", EN)

reset(EN); type_keys("ok ", EN); type_keys("שלום ", HE)
check("ok stays (would be malformed Hebrew)", "ok שלום ", HE)

reset(EN); type_keys("Hi there. ", EN); type_keys("מה שלומך ", HE)
check("previous English sentence stays", "Hi there. מה שלומך ", HE)

reset(EN); type_keys("this is fine ", EN)
check("plain English untouched", "this is fine ", EN)

reset(EN); type_keys("אם אתה ", HE); hotkey_undo()
check("Shift+Backspace undoes the whole sentence fix", "to t,v ", EN)

reset(EN); type_keys("אם 3 אנשים ", HE)
check("digits kept, sentence converted", "אם 3 אנשים ", HE)


# --- safety: a wrong fix in the middle of a correct sentence must not rewrite the sentence --------
def prefix_check(name, typed, typed_lang, target, want_converted):
    reset(typed_lang)
    type_keys(typed, typed_lang)
    start, old, new, converted = app._sentence_prefix(len(app.keys), app.engine.by_id[typed_lang.id],
                                                      app.engine.by_id[target.id])
    ok = bool(converted) == want_converted
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}: converted={bool(converted)} {old!r} -> {new!r}")


prefix_check("the sentence from the incident stays Hebrew",
             "אני כבר מצאתי אחד ויש גירסה מעודכנת באותו הלינק ", HE, EN, False)
prefix_check("correct English sentence stays English", "I already found one and ", EN, HE, False)
prefix_check("'to ' before a Hebrew word is converted", "to ", EN, HE, True)

print(f"\n{sum(results)}/{len(results)} passed")
