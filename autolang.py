"""Safa Brura (שפה ברורה): fixes words typed in the wrong keyboard layout (Hebrew <-> English) on Windows.

Runs in the system tray. A low-level keyboard hook watches the word being typed; when a word
boundary (space, Enter, punctuation) is pressed and the word is gibberish in the active layout but
a common word in another installed layout, the word is retyped in the right language and the
keyboard layout is switched. A hotkey (default: Pause) converts the last words manually, and also
undoes an automatic fix (and remembers the word so it is not fixed again).
"""
import json
import logging
import os
import queue
import sys
import threading
import time
import ctypes
from logging.handlers import RotatingFileHandler

import winapi as W
from engine import Engine, KeyRec, build_languages

APP_NAME = "SafaBrura"
NAME_HE = "שפה ברורה"
NAME_EN = "Safa Brura"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "log.txt")
REPLAY_TAG = 0xA7101A97
GITHUB_URL = "https://github.com/heymish-studio/Safa-Brura"
VERSION = "1.3.0"
ACCEPT_INJECTED = "--accept-injected" in sys.argv  # testing only: treat synthetic input as typed
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "Programs", APP_NAME)
INSTALLED_EXE = os.path.join(INSTALL_DIR, f"{APP_NAME}.exe")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + APP_NAME
QUIT_EVENT = f"Local\\{APP_NAME}.Quit"
LANG_NAMES = {"he": "עברית", "en": "אנגלית", "ru": "רוסית", "ar": "ערבית", "fr": "צרפתית"}
REVERSAL_WINDOW = 90      # seconds: a fix undone by the user's own typing within this window was wrong
REJECT_WINDOW = 15        # seconds: the user switching the layout back right after a fix rejects it
STRIKES_TO_PAUSE = 3      # wrong fixes within STRIKE_WINDOW pause automatic fixing...
STRIKE_WINDOW = 600
PAUSE_SECONDS = 600       # ...for this long

DEFAULT_CONFIG = {
    "enabled": True,
    "min_word_len": 2,
    "min_target_count": 200,
    "min_short_count": 5000,
    "beep_on_fix": True,
    "hotkeys": ["shift+backspace", "pause"],                 # convert the last word / undo an auto-fix
    "hotkeys_all": ["ctrl+shift+backspace", "shift+pause"],   # convert everything since the last reset
    "fallback_switch_hotkey": "alt+shift",
    "excluded_processes": [
        "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "mstsc.exe",
        "vmware-vmx.exe", "vmware.exe", "vmconnect.exe", "virtualboxvm.exe", "anydesk.exe", "teamviewer.exe",
    ],
    "excluded_window_classes": ["ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"],
    "ignore_words": [],
    "check_updates": True,
    "notified_version": "",
}

VK_NAMES = {
    "pause": W.VK_PAUSE, "space": W.VK_SPACE, "esc": W.VK_ESCAPE, "escape": W.VK_ESCAPE, "tab": W.VK_TAB,
    "enter": W.VK_RETURN, "capslock": W.VK_CAPITAL, "scrolllock": 0x91, "insert": 0x2D, "delete": W.VK_DELETE,
    "home": W.VK_HOME, "end": W.VK_END, "backspace": W.VK_BACK, "`": 0xC0, "oem_3": 0xC0,
}
VK_NAMES.update({f"f{i}": 0x6F + i for i in range(1, 25)})
VK_NAMES.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})

MODIFIER_VKS = {W.VK_SHIFT, W.VK_LSHIFT, W.VK_RSHIFT, W.VK_CONTROL, W.VK_LCONTROL, W.VK_RCONTROL,
                W.VK_MENU, W.VK_LMENU, W.VK_RMENU, W.VK_LWIN, W.VK_RWIN, W.VK_CAPITAL}
RESET_VKS = {W.VK_ESCAPE, W.VK_END, W.VK_HOME, W.VK_LEFT, W.VK_UP, W.VK_RIGHT, W.VK_DOWN, W.VK_DELETE,
             W.VK_PRIOR, W.VK_NEXT}
LINE_VKS = {W.VK_RETURN, W.VK_TAB}

log = logging.getLogger(APP_NAME)


# ---------------------------------------------------------------- config --
def migrate_old_settings():
    """The first build was called AutoLanguage; carry its settings folder over to the new name."""
    old_dir = os.path.join(os.path.dirname(APP_DIR), "AutoLanguage")
    if os.path.isdir(old_dir) and not os.path.isdir(APP_DIR):
        try:
            os.rename(old_dir, APP_DIR)
        except OSError:
            pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def parse_hotkey(spec):
    """'ctrl+shift+space' -> (set of modifier names, vk)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mods = set()
    vk = None
    for p in parts:
        if p in ("ctrl", "control"):
            mods.add("ctrl")
        elif p in ("alt", "menu"):
            mods.add("alt")
        elif p == "shift":
            mods.add("shift")
        elif p in ("win", "windows"):
            mods.add("win")
        else:
            vk = VK_NAMES.get(p)
    return mods, vk


def modifiers_down():
    mods = set()
    if W.user32.GetAsyncKeyState(W.VK_CONTROL) & 0x8000:
        mods.add("ctrl")
    if W.user32.GetAsyncKeyState(W.VK_MENU) & 0x8000:
        mods.add("alt")
    if W.user32.GetAsyncKeyState(W.VK_SHIFT) & 0x8000:
        mods.add("shift")
    if (W.user32.GetAsyncKeyState(W.VK_LWIN) | W.user32.GetAsyncKeyState(W.VK_RWIN)) & 0x8000:
        mods.add("win")
    return mods


# ------------------------------------------------------------- the app ----
class AutoLanguage:
    def __init__(self):
        self.cfg = load_config()
        self.engine = None
        self.languages = []
        self.lock = threading.Lock()
        self.jobs = queue.Queue()

        # typing state (only touched from the hook thread)
        self.keys = []            # KeyRec since last reset (word keys and boundary keys)
        self.word_start = 0       # index in self.keys where the current word starts
        self.layout_id = None     # layout the current buffer is typed in
        self.focus_hwnd = None
        self.skip_until_reset = False
        self.last_fix = None      # info about the last automatic fix, for undo
        self.fixing = False
        self.pending = []         # real key events received while a fix is in progress
        self.recent_fix = None    # last automatic fix, kept while the user keeps typing (ping-pong guard)
        self.buffer_gen = 0       # bumped on every reset: tells whether recent_fix text is still reachable
        self.strikes = []         # times of fixes that turned out wrong
        self.paused_until = 0.0   # automatic fixing suspended until this time
        self.hotkeys = [parse_hotkey(h) for h in self.cfg.get("hotkeys", []) + ([self.cfg["hotkey"]] if self.cfg.get("hotkey") else [])]
        self.hotkeys_all = [parse_hotkey(h) for h in self.cfg.get("hotkeys_all", [])]
        self.hook_thread_id = None
        self.kb_hook = self.mouse_hook = None
        self._kb_proc = self._mouse_proc = None
        self.icon = None
        self.update_info = None   # set when GitHub has a newer release

    # ------------------------------------------------------------ setup --
    def init_engine(self):
        self.languages = build_languages(self.cfg["min_target_count"], self.cfg["min_short_count"])
        self.engine = Engine(self.languages, self.cfg["min_word_len"], self.cfg.get("ignore_words", []))
        log.info("languages: %s", ", ".join(f"{l.tag}({len(l.freq)} words, spell={'yes' if l.spell else 'no'})" for l in self.languages))

    # -------------------------------------------------------- hook thread --
    def run_hooks(self):
        self.hook_thread_id = W.kernel32.GetCurrentThreadId()
        self.init_engine()
        self._kb_proc = W.HOOKPROC(self._keyboard_proc)
        self._mouse_proc = W.HOOKPROC(self._mouse_proc_impl)
        self.kb_hook = W.user32.SetWindowsHookExW(W.WH_KEYBOARD_LL, self._kb_proc, None, 0)
        self.mouse_hook = W.user32.SetWindowsHookExW(W.WH_MOUSE_LL, self._mouse_proc, None, 0)
        if not self.kb_hook:
            log.error("keyboard hook failed: %s", ctypes.GetLastError())
        log.info("hooks installed; hotkeys=%s all=%s", self.cfg.get("hotkeys"), self.cfg.get("hotkeys_all"))
        W.message_loop()
        if self.kb_hook:
            W.user32.UnhookWindowsHookEx(self.kb_hook)
        if self.mouse_hook:
            W.user32.UnhookWindowsHookEx(self.mouse_hook)

    def _mouse_proc_impl(self, code, wparam, lparam):
        if code >= 0 and wparam in (W.WM_LBUTTONDOWN, W.WM_RBUTTONDOWN, W.WM_MBUTTONDOWN):
            self.reset("mouse")
        return W.user32.CallNextHookEx(self.mouse_hook, code, wparam, lparam)

    def reset(self, why=""):
        self.keys = []
        self.word_start = 0
        self.skip_until_reset = False
        self.last_fix = None
        self.buffer_gen += 1

    def _keyboard_proc(self, code, wparam, lparam):
        if code < 0:
            return W.user32.CallNextHookEx(self.kb_hook, code, wparam, lparam)
        kb = ctypes.cast(lparam, ctypes.POINTER(W.KBDLLHOOKSTRUCT)).contents
        try:
            if self._handle_key(kb, wparam):
                return 1
        except Exception:
            log.exception("hook error")
        return W.user32.CallNextHookEx(self.kb_hook, code, wparam, lparam)

    def _handle_key(self, kb, wparam):
        """Return True to swallow the event."""
        vk, scan, flags = kb.vkCode, kb.scanCode, kb.flags
        if (flags & W.LLKHF_INJECTED) and kb.dwExtraInfo != REPLAY_TAG:
            if not (ACCEPT_INJECTED and kb.dwExtraInfo != W.INJECT_TAG):
                return False  # our own injected text, or another tool's input
        down = wparam in (W.WM_KEYDOWN, W.WM_SYSKEYDOWN)

        if self.fixing:
            self.pending.append((vk, scan, down))
            return True

        if not down:
            return False
        if vk in MODIFIER_VKS:
            return False

        mods = modifiers_down()
        for hmods, hvk in self.hotkeys_all:
            if hvk and vk == hvk and mods == hmods and self.manual_convert(everything=True):
                return True
        for hmods, hvk in self.hotkeys:
            if hvk and vk == hvk and mods == hmods and self.manual_convert(everything=False):
                return True  # nothing to convert -> the key keeps its normal meaning (e.g. Shift+Backspace deletes)
        if mods & {"ctrl", "alt", "win"}:
            self.reset("shortcut")
            return False

        hwnd, _ = W.focus_window()
        hkl = W.current_layout()
        lid = W.layout_id(hkl)
        if hwnd != self.focus_hwnd or lid != self.layout_id:
            rf = self.recent_fix
            if (rf and hwnd == self.focus_hwnd and self.layout_id == rf["to_id"] and lid == rf["from_id"]
                    and time.time() - rf["time"] < REJECT_WINDOW):
                # the user switched the layout straight back by hand: our fix was unwanted
                log.info("fix rejected by manual layout switch: %r -> %r", rf["source_text"], rf["text"])
                self.recent_fix = None
                self.remember_ignore(rf["source_text"], rf["text"])
                self.strike()
            self.reset("focus/layout")
            self.focus_hwnd, self.layout_id = hwnd, lid
        cur = self.engine.language_for(hkl)

        if vk == W.VK_BACK:
            if self.keys:
                self.keys.pop()
                if self.word_start > len(self.keys):
                    self.word_start = len(self.keys)
                if self.word_start == len(self.keys):
                    self._recompute_word_start()
            return False
        if vk in RESET_VKS:
            self.reset("navigation")
            return False

        rec = KeyRec(vk, scan, "shift" in mods, bool(W.user32.GetKeyState(W.VK_CAPITAL) & 1), lid)
        if cur is None:
            return False
        if self.engine.is_word_key(rec, cur):
            self.keys.append(rec)
            self.last_fix = None  # typing continues: the undo window for the last auto-fix is over
            return False

        # boundary key (space / Enter / Tab / punctuation): evaluate the word that just ended
        if not (vk in LINE_VKS or vk == W.VK_SPACE or cur.render([rec])):
            return False  # non-character key (F-keys, media keys...): not part of the text
        word = self.keys[self.word_start:]
        decision = None
        if self.cfg["enabled"] and word and not self.skip_until_reset and time.time() >= self.paused_until:
            decision = self.engine.decide(word, cur)
        reversal = None
        rf = self.recent_fix
        if rf and word:
            if decision is None:
                if cur.plausible(cur.render(word), morph=True):
                    self.recent_fix = None  # a real word in the fixed language: the fix was right
            elif (decision.target.id == rf["from_id"] and cur.id == rf["to_id"]
                  and time.time() - rf["time"] < REVERSAL_WINDOW):
                # the user went on typing in the language we switched away from: the earlier fix was
                # wrong. Fix this word (it is in that language) and, if the earlier text is still in
                # reach, restore it too.
                reversal = rf
                self.recent_fix = None
        if decision:
            log.info("auto-fix %r -> %r (%s)", decision.source_text, decision.text, decision.target.tag)
            self.fixing = True
            self.pending = []
            self.jobs.put(("fix", dict(word=list(word), start=self.word_start, decision=decision,
                                       boundary=(vk, scan, rec), cur=cur, reversal=reversal)))
            return True

        if vk in LINE_VKS:
            self.reset("line")
            return False
        if cur.render([rec]):
            self.keys.append(rec)
            self.word_start = len(self.keys)
        return False

    def _recompute_word_start(self):
        """After backspacing into the previous word, make it the current word again."""
        i = len(self.keys)
        cur = self.engine.by_id.get(self.layout_id)
        while i > 0 and cur and self.engine.is_word_key(self.keys[i - 1], cur):
            i -= 1
        self.word_start = i

    # --------------------------------------------------------- actions ----
    def last_word_start(self):
        """Index of the last word typed (skipping trailing spaces/punctuation)."""
        cur = self.engine.by_id.get(self.layout_id)
        i = len(self.keys)
        while i > 0 and cur and not self.engine.is_word_key(self.keys[i - 1], cur):
            i -= 1
        while i > 0 and cur and self.engine.is_word_key(self.keys[i - 1], cur):
            i -= 1
        return i

    def manual_convert(self, everything=False):
        """Hotkey: convert the last word (Shift: everything since the last reset) to the other
        layout. Right after an automatic fix it undoes that fix and remembers the word.
        Returns True when a conversion was started."""
        if not self.keys:
            return False
        cur = self.engine.by_id.get(self.layout_id)
        if cur is None:
            return False
        undo = self.last_fix is not None and not everything
        start = 0 if everything else (self.last_fix["start"] if undo else self.last_word_start())
        keys = self.keys[start:]
        if not keys:
            return False
        target = self.engine.other_language(cur, keys)
        if target is None:
            return False
        if undo and self.last_fix.get("source_text"):
            self.remember_ignore(self.last_fix["source_text"], self.last_fix["text"])
        self.fixing = True
        self.pending = []
        self.jobs.put(("manual", dict(keys=keys, cur=cur, target=target, undo=undo)))
        return True

    def remember_ignore(self, *words):
        with self.lock:
            lst = self.cfg.setdefault("ignore_words", [])
            for w in words:
                if w and w.lower() not in (x.lower() for x in lst):
                    lst.append(w)
                    self.engine.ignore_words.add(w.lower())
            save_config(self.cfg)
        log.info("ignore list += %s", words)

    def excluded_here(self):
        proc = W.foreground_process_name()
        if proc in [p.lower() for p in self.cfg.get("excluded_processes", [])]:
            return f"process {proc}"
        hwnd, _ = W.focus_window()
        fg = W.user32.GetForegroundWindow()
        classes = {W.window_class(h) for h in (hwnd, fg) if h}
        if classes & set(self.cfg.get("excluded_window_classes", [])):
            return f"window class {classes}"
        if W.focused_is_password():
            return "password field"
        return None

    # -------------------------------------------------------- worker ------
    def run_worker(self):
        import comtypes
        comtypes.CoInitialize()
        while True:
            kind, job = self.jobs.get()
            if kind == "quit":
                return
            try:
                if kind == "fix":
                    self._do_fix(job)
                elif kind == "manual":
                    self._do_manual(job)
            except Exception:
                log.exception("job failed")
            finally:
                self._end_fix()

    def _end_fix(self):
        self.fixing = False
        pending, self.pending = self.pending, []
        if pending:
            inputs = []
            for vk, scan, down in pending:
                inp = W.INPUT()
                inp.type = W.INPUT_KEYBOARD
                inp.u.ki = W.KEYBDINPUT(vk, scan, 0 if down else W.KEYEVENTF_KEYUP, 0, REPLAY_TAG)
                inputs.append(inp)
            W.send_inputs(inputs)

    def _switch_layout(self, target):
        W.request_layout(target.hkl)
        time.sleep(0.12)
        if W.layout_id(W.current_layout()) != target.id:
            spec = self.cfg.get("fallback_switch_hotkey")
            if spec:
                mods, vk = parse_hotkey(spec)
                vks = [{"ctrl": W.VK_CONTROL, "alt": W.VK_MENU, "shift": W.VK_SHIFT, "win": W.VK_LWIN}[m] for m in ("ctrl", "alt", "shift", "win") if m in mods]
                if vk:
                    vks.append(vk)
                if vks:
                    log.info("layout switch via message failed; sending %s", spec)
                    W.send_combo(vks)
                    time.sleep(0.08)
        actual = W.layout_id(W.current_layout())
        if actual != target.id:
            log.warning("layout switch to %s failed (active layout %04x)", target.tag, actual)
        self.layout_id = actual

    def _do_fix(self, job):
        reason = self.excluded_here()
        word, decision, (bvk, bscan, brec), cur = job["word"], job["decision"], job["boundary"], job["cur"]
        if reason:
            log.info("skip (%s)", reason)
            self.skip_until_reset = True
            W.send_vk(bvk, bscan)
            self._after_boundary(bvk, brec, cur)
            return
        target = decision.target
        rf = job.get("reversal")
        if rf:
            log.info("earlier fix %r -> %r was wrong (user kept typing %s)", rf["source_text"], rf["text"], target.tag)
            self.remember_ignore(rf["source_text"], rf["text"])
            self.strike()
            if rf["gen"] == self.buffer_gen and rf["start"] < job["start"]:
                self._repair(rf["start"], target, (bvk, bscan, brec))
                return
        # One sentence, one language: the fixed word proves which language the user is writing in, so
        # everything typed since the start of the sentence moves to that language too (short words like
        # "to" = אם are real words in both languages and never trigger a fix on their own).
        span_start, prefix_old, prefix_new, converted = self._sentence_prefix(job["start"], cur, target)
        if converted:
            log.info("sentence %r -> %r", prefix_old, prefix_new)
        W.replace_text(len(prefix_old) + len(decision.source_text), prefix_new + decision.text)
        self._switch_layout(target)
        if bvk in LINE_VKS or bvk == W.VK_SPACE:
            W.send_vk(bvk, bscan)
        else:
            W.send_text(target.render([brec]) or cur.render([brec]))
        if self.cfg.get("beep_on_fix"):
            W.beep()
        for k in word + converted:
            k.layout = target.id
            k.fixed = True
        self.last_fix = dict(source_text=decision.source_text, text=decision.text,
                             start=span_start if converted else job["start"])
        self.recent_fix = None if rf else dict(
            time=time.time(), from_id=cur.id, to_id=target.id, start=span_start if converted else job["start"],
            gen=self.buffer_gen, source_text=decision.source_text, text=decision.text)
        self._after_boundary(bvk, brec, target)

    def _repair(self, start, lang, boundary):
        """Everything from a wrong fix onwards was meant in `lang`: retype it that way in one go."""
        bvk, bscan, brec = boundary
        span = self.keys[start:]
        old = "".join(self.engine.by_id.get(k.layout, lang).render([k]) for k in span)
        new = lang.render(span)
        log.info("repair %r -> %r", old, new)
        W.replace_text(len(old), new)
        self._switch_layout(lang)
        if bvk in LINE_VKS or bvk == W.VK_SPACE:
            W.send_vk(bvk, bscan)
        else:
            W.send_text(lang.render([brec]))
        if self.cfg.get("beep_on_fix"):
            W.beep()
            W.beep()
        for k in span:
            k.layout = lang.id
            k.fixed = True
        self.last_fix = None
        self.notify(f"תיקנתי בטעות ותיקנתי בחזרה ל{LANG_NAMES.get(lang.short, lang.tag)}. "
                    f"המילה נוספה לרשימת ההחרגה ולא תתוקן שוב.")
        self._after_boundary(bvk, brec, lang)

    def strike(self):
        """A fix turned out wrong. Too many of those in a short time: stop fixing for a while."""
        now = time.time()
        self.strikes = [t for t in self.strikes if now - t < STRIKE_WINDOW] + [now]
        if len(self.strikes) >= STRIKES_TO_PAUSE and now >= self.paused_until:
            self.paused_until = now + PAUSE_SECONDS
            self.strikes = []
            log.warning("too many wrong fixes: automatic fixing paused for %d s", PAUSE_SECONDS)
            self.notify(f"יותר מדי תיקוני-שווא, התיקון האוטומטי מושהה ל-{PAUSE_SECONDS // 60} דקות. "
                        "לכיבוי קבוע: לחיצה על האייקון ליד השעון.")
            self.refresh_icon()
            t = threading.Timer(PAUSE_SECONDS + 1, self.refresh_icon)
            t.daemon = True
            t.start()

    def refresh_icon(self):
        if self.icon is not None:
            try:
                self.icon.icon = self.make_icon_image()
            except Exception:
                pass

    def _sentence_prefix(self, word_start, cur, target):
        """Words typed before the fixed word in the same sentence, re-rendered in the target language.

        Goes back from the fixed word to the start of the buffer (start of typing in this field) or to
        the last sentence end ('.', '!', '?' as it actually appeared on screen). A word is converted
        only if it is a real word in the target language ('to' -> 'אם'); 'ok' -> 'םל' stays.
        Safety: if any earlier word is a real word as typed but NOT in the target language
        ('אני' -> 'tbh'), the sentence really is in the current language and the prefix is left alone,
        so one wrong fix can never turn a whole correct sentence into gibberish.
        Returns (span_start, old_text, new_text, converted_keys).
        """
        def lang_of(k):
            return self.engine.by_id.get(k.layout, cur)

        start = word_start
        while start > 0:
            k = self.keys[start - 1]
            if lang_of(k).render([k]) in (".", "!", "?"):
                break
            start -= 1
        prefix = self.keys[start:word_start]
        if not prefix:
            return word_start, "", "", []

        # split the prefix into words (letter keys) and separators (spaces, digits, punctuation)
        groups, buf = [], []
        for k in prefix:
            is_word = self.engine.is_word_key(k, lang_of(k)) and not lang_of(k).render([k]).isdigit()
            if is_word:
                buf.append(k)
            else:
                if buf:
                    groups.append(("w", buf))
                    buf = []
                groups.append(("s", [k]))
        if buf:
            groups.append(("w", buf))

        old_parts, new_parts, converted = [], [], []
        for kind, keys in groups:
            old = "".join(lang_of(k).render([k]) for k in keys)
            new = old
            if kind == "w" and not any(k.fixed for k in keys) and all(lang_of(k) is not target for k in keys):
                candidate = target.render(keys)
                if old.lower() in self.engine.ignore_words or candidate.lower() in self.engine.ignore_words:
                    pass
                elif target.plausible(candidate):
                    new = candidate
                    converted.extend(keys)
                elif lang_of(keys[0]).plausible(old):
                    log.info("sentence left alone: %r is a real word as typed", old)
                    return word_start, "", "", []
            old_parts.append(old)
            new_parts.append(new)
        if not converted:
            return word_start, "", "", []
        # retype from the first changed character only, to keep the backspace run short
        old_text, new_text = "".join(old_parts), "".join(new_parts)
        i = 0
        while i < min(len(old_text), len(new_text)) and old_text[i] == new_text[i]:
            i += 1
        return start, old_text[i:], new_text[i:], converted

    def _after_boundary(self, bvk, brec, lang):
        if bvk in LINE_VKS:
            self.keys = []
            self.word_start = 0
            return
        brec.layout = lang.id
        self.keys.append(brec)
        self.word_start = len(self.keys)

    def _do_manual(self, job):
        keys, cur, target = job["keys"], job["cur"], job["target"]
        if self.excluded_here() == "password field":
            log.info("manual convert skipped: password field")
            return
        def lang_of(k):
            return self.engine.by_id.get(k.layout, cur)

        def flips(k):
            return job.get("undo") or not k.fixed
        old_text = "".join(lang_of(k).render([k]) for k in keys)
        # unfixed keys flip to the target layout; words an auto-fix already rewrote stay as they are
        # (unless this is an undo of that very fix)
        new_text = "".join(target.render([k]) if flips(k) else lang_of(k).render([k]) for k in keys)
        log.info("manual %r -> %r", old_text, new_text)
        W.replace_text(len(old_text), new_text)
        self._switch_layout(target)
        for k in keys:
            if flips(k):
                k.layout = target.id
                k.fixed = False
        self.last_fix = None
        # any typing from now on happens in the new layout, so the buffer must not be reset by the
        # layout-change check in the hook
        self.layout_id = target.id

    # ----------------------------------------------------------- tray -----
    def make_icon_image(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        on = self.cfg["enabled"] and time.time() >= self.paused_until
        d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(37, 99, 235, 255) if on else (120, 120, 120, 255))
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 30)
        except OSError:
            font = ImageFont.load_default()
        d.text((9, 12), "A", font=font, fill="white")
        d.text((33, 12), "\u05d0", font=font, fill="white")
        return img

    # --------------------------------------------------------- updates ----
    def notify(self, message):
        try:
            if self.icon is not None:
                self.icon.notify(message, f"{NAME_HE} · {NAME_EN}")
        except Exception:
            log.exception("notify")

    def check_updates(self, manual=False):
        import updater
        try:
            info = updater.check_latest(VERSION)
        except Exception as e:
            log.info("update check failed: %s", e)
            if manual:
                self.notify("לא הצלחתי לבדוק עדכונים. בדקו את החיבור לאינטרנט.")
            return
        self.update_info = info
        if self.icon is not None:
            self.icon.update_menu()
        if info is None:
            log.info("up to date (%s)", VERSION)
            if manual:
                self.notify(f"יש לכם את הגרסה האחרונה ({VERSION}).")
            return
        log.info("update available: %s", info["version"])
        if manual or self.cfg.get("notified_version") != info["version"]:
            self.notify(f"גרסה {info['version']} זמינה. לעדכון: לחיצה ימנית על האייקון, ואז \"עדכון\".")
            with self.lock:
                self.cfg["notified_version"] = info["version"]
                save_config(self.cfg)

    def run_update_checker(self):
        time.sleep(20)  # let the tray icon appear first
        while True:
            if self.cfg.get("check_updates", True):
                self.check_updates()
            time.sleep(24 * 3600)

    def start_update(self):
        """Ask, then download and swap the exe. Runs on its own thread."""
        import updater
        import webbrowser
        info = self.update_info
        if not info:
            return
        if "--update-now" not in sys.argv:
            from welcome import show_task_dialog
            body = (info.get("notes") or "") + "\n\nהעדכון לוקח כמה שניות. התוכנה תיסגר ותיפתח מחדש לבד."
            result = show_task_dialog(f"{NAME_HE} · {NAME_EN}", f"גרסה {info['version']} זמינה",
                                      body.strip(), [(101, "עדכנו עכשיו"), (102, "אחר כך")],
                                      footer=f'<a href="{info["page"]}">מה השתנה, בפירוט</a>',
                                      on_hyperlink=lambda href: webbrowser.open(href))
            if result is None or result[0] != 101:
                return
        if not updater.can_self_update():
            webbrowser.open(info["page"])
            return
        try:
            log.info("installing %s", info["version"])
            updater.install(info)
        except Exception as e:
            log.exception("update failed")
            self.notify(f"העדכון נכשל ({e}). אפשר להוריד את הגרסה החדשה מהאתר.")
            webbrowser.open(info["page"])
            return
        self.shutdown()
        os._exit(0)

    def shutdown(self):
        self.jobs.put(("quit", None))
        if self.hook_thread_id:
            W.user32.PostThreadMessageW(self.hook_thread_id, W.WM_QUIT, 0, 0)
        release_single_instance()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def _update_now(self):
        """Testing aid (--update-now): check and install without asking."""
        time.sleep(3)
        self.check_updates()
        if self.update_info:
            self.start_update()
        else:
            log.info("--update-now: nothing newer than %s", VERSION)

    def run_tray(self):
        import pystray
        from pystray import MenuItem as Item

        def toggle_enabled(icon, item):
            with self.lock:
                self.cfg["enabled"] = not self.cfg["enabled"]
                save_config(self.cfg)
            self.paused_until = 0.0
            icon.icon = self.make_icon_image()
            log.info("enabled=%s", self.cfg["enabled"])
            self.notify("התיקון האוטומטי פועל." if self.cfg["enabled"] else
                        "התיקון האוטומטי כבוי. המקשים הידניים עדיין עובדים. לחיצה נוספת על האייקון מדליקה.")

        def do_uninstall(icon, item):
            threading.Thread(target=uninstall, kwargs={"app": self}, daemon=True).start()

        def toggle_startup(icon, item):
            set_startup(not startup_enabled())

        def open_config(icon, item):
            save_config(self.cfg)
            os.startfile(CONFIG_PATH)

        def open_log(icon, item):
            os.startfile(LOG_PATH)

        def show_help(icon, item):
            threading.Thread(target=show_welcome, args=(self,), daemon=True).start()

        def open_site(icon, item):
            import webbrowser
            webbrowser.open(GITHUB_URL)

        def quit_app(icon, item):
            self.shutdown()

        def do_update(icon, item):
            threading.Thread(target=self.start_update, daemon=True).start()

        def manual_check(icon, item):
            threading.Thread(target=self.check_updates, kwargs={"manual": True}, daemon=True).start()

        def on_ready(icon):
            icon.visible = True
            threading.Thread(target=wait_quit_event, args=(self,), daemon=True).start()
            if "--after-update" in sys.argv:
                self.notify(f"עודכן לגרסה {VERSION}. אפשר להמשיך להקליד.")
            if "--update-now" in sys.argv:
                threading.Thread(target=self._update_now, daemon=True).start()
            else:
                threading.Thread(target=self.run_update_checker, daemon=True).start()

        hot = " / ".join(self.cfg.get("hotkeys", []))
        hot_all = " / ".join(self.cfg.get("hotkeys_all", []))
        menu = pystray.Menu(
            Item(f"{NAME_HE} · {NAME_EN}  {VERSION}", None, enabled=False),
            Item(lambda item: f"עדכון לגרסה {self.update_info['version']} זמין, לחצו לעדכון" if self.update_info else "",
                 do_update, visible=lambda item: self.update_info is not None),
            Item("תיקון אוטומטי פעיל", toggle_enabled, checked=lambda item: self.cfg["enabled"], default=True),
            Item(f"המרת המילה האחרונה / ביטול: {hot}", None, enabled=False),
            Item(f"המרת הכול: {hot_all}", None, enabled=False),
            Item("הפעלה עם ווינדוס", toggle_startup, checked=lambda item: startup_enabled()),
            Item("הסבר קצר", show_help),
            Item("בדיקת עדכונים", manual_check),
            Item("דף התוכנה באינטרנט", open_site),
            Item("פתח קובץ הגדרות", open_config),
            Item("פתח יומן", open_log),
            Item("הסרת התוכנה מהמחשב...", do_uninstall),
            Item("יציאה", quit_app),
        )
        self.icon = pystray.Icon(APP_NAME, self.make_icon_image(), f"{NAME_HE} · {NAME_EN}", menu)
        self.icon.run(setup=on_ready)


# --------------------------------------------------------- welcome window -
WELCOME_TEXT = """התוכנה רצה ברקע ומתקנת מילים שהקלדת בשפה הלא נכונה.

•  הקלדת "akuo" ורווח כשהמקלדת באנגלית? יהפוך ל"שלום" והמקלדת תעבור לעברית.
•  הקלדת "יקךךם" ורווח כשהמקלדת בעברית? יהפוך ל"hello" והמקלדת תעבור לאנגלית.
•  Shift+Backspace (או Pause) ממיר ידנית את המילה האחרונה. מיד אחרי תיקון אוטומטי זה מבטל אותו.
•  Ctrl+Shift+Backspace (או Shift+Pause) ממיר את כל מה שהקלדת מאז הלחיצה האחרונה.
•  בשדות סיסמה ובטרמינלים התוכנה לא נוגעת.
•  טעתה? המשך להקליד כרגיל: היא תזהה את זה, תחזיר את הטקסט ותלמד. ואם היא טועה הרבה, היא עוצרת לבד.

האייקון הכחול Aא נמצא במגש המערכת, לפעמים מאחורי החץ ^ ליד השעון. לחיצה אחת עליו מכבה ומדליקה את התיקון האוטומטי.
לחיצה ימנית: הגדרות, הסרה מהמחשב, יציאה. אפשר להסיר גם דרך הגדרות ← אפליקציות."""


def show_welcome(app, auto_close_ms=None):
    """First-run explanation (Hebrew). Native Windows task dialog, which lays out bidirectional
    text correctly; the Tk window below is only a fallback. Runs in its own thread."""
    try:
        from welcome import show_task_dialog
        import webbrowser
        result = show_task_dialog(
            f"{NAME_HE} · {NAME_EN}", f"ברוכים הבאים ל{NAME_HE}", WELCOME_TEXT, [(100, "הבנתי, התחל")],
            footer=f'<a href="{GITHUB_URL}">הסבר מלא באינטרנט</a>',
            verify_text="הפעל אוטומטית בכל כניסה לווינדוס", verify_checked=startup_enabled() or not app.cfg.get("welcomed"),
            on_hyperlink=lambda href: webbrowser.open(href), auto_close_ms=auto_close_ms)
    except Exception:
        log.exception("native welcome dialog")
        result = None
    if result is not None:
        _, checked = result
        try:
            set_startup(checked)
        except Exception:
            log.exception("startup shortcut")
        with app.lock:
            app.cfg["welcomed"] = True
            save_config(app.cfg)
        return
    show_welcome_tk(app, auto_close_ms)


def show_welcome_tk(app, auto_close_ms=None):
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title(f"{NAME_HE} · {NAME_EN} {VERSION}")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="ברוכים הבאים לשפה ברורה", font=("Segoe UI", 15, "bold"), anchor="e", justify="right").pack(fill="x")
    ttk.Label(frame, text=WELCOME_TEXT, font=("Segoe UI", 11), anchor="e", justify="right", wraplength=560).pack(fill="x", pady=(12, 16))
    startup_var = tk.BooleanVar(value=True)
    cb = ttk.Checkbutton(frame, text="הפעל אוטומטית בכל כניסה לווינדוס", variable=startup_var)
    cb.pack(anchor="e")
    bottom = ttk.Frame(frame)
    bottom.pack(fill="x", pady=(16, 0))

    def close():
        try:
            set_startup(startup_var.get())
        except Exception:
            log.exception("startup shortcut")
        with app.lock:
            app.cfg["welcomed"] = True
            save_config(app.cfg)
        root.destroy()

    def help_page():
        import webbrowser
        webbrowser.open(GITHUB_URL)

    ttk.Button(bottom, text="הבנתי, התחל", command=close).pack(side="right")
    ttk.Button(bottom, text="הסבר מלא באינטרנט", command=help_page).pack(side="right", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", close)
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    if auto_close_ms:
        root.after(auto_close_ms, close)
    root.mainloop()


# ------------------------------------------------------- startup shortcut -
def startup_dir():
    return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")


def startup_link():
    return os.path.join(startup_dir(), f"{APP_NAME}.lnk")


def startup_enabled():
    return os.path.exists(startup_link())


def set_startup(enable):
    link = startup_link()
    if not enable:
        try:
            os.remove(link)
        except OSError:
            pass
        return
    if getattr(sys, "frozen", False):
        exe = INSTALLED_EXE if os.path.exists(INSTALLED_EXE) else sys.executable
        target, args, workdir = exe, "", os.path.dirname(exe)
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        target = pyw if os.path.exists(pyw) else sys.executable
        args = f'"{os.path.abspath(__file__)}"'
        workdir = os.path.dirname(os.path.abspath(__file__))
    import subprocess
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath = '%s'; "
        "$s.Arguments = '%s'; $s.WorkingDirectory = '%s'; $s.Description = 'Safa Brura keyboard layout fixer'; $s.Save()"
        % (link.replace("'", "''"), target.replace("'", "''"), args.replace("'", "''"), workdir.replace("'", "''"))
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   creationflags=0x08000000, check=False)  # CREATE_NO_WINDOW


# ------------------------------------------------- install / uninstall -
def is_frozen():
    return getattr(sys, "frozen", False)


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def register_uninstall(exe):
    """List the app under Settings > Apps (per-user, no admin needed) so it can be removed like any program."""
    import winreg
    try:
        size_kb = os.path.getsize(exe) // 1024
    except OSError:
        size_kb = 0
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in [
            ("DisplayName", f"{NAME_HE} ({NAME_EN})"), ("DisplayVersion", VERSION), ("Publisher", "Heymish Studio"),
            ("DisplayIcon", exe), ("InstallLocation", os.path.dirname(exe)),
            ("UninstallString", f'"{exe}" --uninstall'), ("QuietUninstallString", f'"{exe}" --uninstall --quiet'),
            ("URLInfoAbout", GITHUB_URL), ("HelpLink", GITHUB_URL), ("InstallDate", time.strftime("%Y%m%d")),
        ]:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in [("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)]:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def unregister_uninstall():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def _quit_event():
    W.kernel32.CreateEventW.restype = ctypes.c_void_p
    W.kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    return W.kernel32.CreateEventW(None, 0, 0, QUIT_EVENT)  # auto-reset


def wait_quit_event(app):
    """Running instance: another copy (installer or uninstaller) asks us to exit."""
    h = _quit_event()
    if not h:
        return
    W.kernel32.ResetEvent(ctypes.c_void_p(h))
    W.kernel32.WaitForSingleObject(ctypes.c_void_p(h), 0xFFFFFFFF)
    log.info("asked to quit by another instance")
    app.shutdown()


def stop_other_instances(wait_seconds=10):
    """Ask the running instance to exit and wait until the single-instance mutex is free. Falls back
    to killing older versions that do not understand the request. Returns True when the mutex is ours."""
    h = _quit_event()
    if h:
        W.kernel32.SetEvent(ctypes.c_void_p(h))
    if single_instance(wait_seconds=wait_seconds):
        return True
    import subprocess
    me = os.getpid()
    subprocess.run(["taskkill", "/F", "/FI", f"PID ne {me}", "/IM", f"{APP_NAME}.exe"],
                   creationflags=0x08000000, capture_output=True)
    return single_instance(wait_seconds=5)


def install_self():
    """A downloaded exe copies itself to the per-user Programs folder, registers for uninstall, points the
    Startup shortcut there and starts the installed copy. Returns True when the caller must exit."""
    if not is_frozen() or "--no-install" in sys.argv:
        return False
    exe = sys.executable
    if same_file(exe, INSTALLED_EXE) or os.path.normcase(os.path.dirname(exe)) == os.path.normcase(INSTALL_DIR):
        return False
    import shutil
    import updater
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        tmp = INSTALLED_EXE + ".download"
        shutil.copyfile(exe, tmp)
        if not stop_other_instances():
            log.warning("install: another instance keeps running; staying in place")
            os.remove(tmp)
            return False
        if os.path.exists(INSTALLED_EXE):
            old = INSTALLED_EXE + updater.OLD_SUFFIX
            if os.path.exists(old):
                os.remove(old)
            os.replace(INSTALLED_EXE, old)
        os.replace(tmp, INSTALLED_EXE)
        register_uninstall(INSTALLED_EXE)
        if startup_enabled():
            set_startup(True)
    except Exception:
        log.exception("install to %s failed; running from %s", INSTALL_DIR, exe)
        release_single_instance()
        return False
    log.info("installed to %s", INSTALLED_EXE)
    release_single_instance()
    updater.launch(INSTALLED_EXE, ["--after-install"] + [a for a in sys.argv[1:] if a.startswith("--")])
    return True


def uninstall(app=None, quiet=None):
    """Remove the Startup shortcut, the Apps entry, optionally the settings, and the program file itself.
    Runs either inside the running app (tray menu) or as a separate process (--uninstall)."""
    if quiet is None:
        quiet = "--quiet" in sys.argv
    wipe_settings = True
    if not quiet:
        try:
            from welcome import show_task_dialog
            result = show_task_dialog(f"{NAME_HE} · {NAME_EN}", f"להסיר את {NAME_HE} מהמחשב?",
                                      "התוכנה תיסגר, ההפעלה האוטומטית תבוטל והקובץ יימחק.",
                                      [(1, "הסר"), (2, "ביטול")], verify_text="מחק גם את ההגדרות והיומן",
                                      verify_checked=True)
        except Exception:
            log.exception("uninstall dialog")
            result = None
        if result is None:
            r = W.user32.MessageBoxW(None, f"להסיר את {NAME_HE} מהמחשב?", f"{NAME_HE} · {NAME_EN}", 0x34 | 0x100000)
            result = (1 if r == 6 else 2, True)
        if result[0] != 1:
            return False
        wipe_settings = result[1]
    log.info("uninstalling (settings %s)", "removed" if wipe_settings else "kept")
    exes = {os.path.abspath(sys.executable)} if is_frozen() else set()
    if os.path.exists(INSTALLED_EXE):
        exes.add(os.path.abspath(INSTALLED_EXE))
    if app is not None:
        app.shutdown()
    else:
        stop_other_instances()
    release_single_instance()
    set_startup(False)
    unregister_uninstall()
    if wipe_settings:
        import shutil
        logging.shutdown()
        shutil.rmtree(APP_DIR, ignore_errors=True)
    if exes:
        # the exe stays locked until the PyInstaller bootloader has cleaned up, so a small batch file
        # keeps trying for up to ~40 s, then removes itself
        import subprocess
        import tempfile
        lines = ["@echo off", "ping 127.0.0.1 -n 3 >nul"]
        for e in exes:
            lines.append(f'for /L %%i in (1,1,20) do (if exist "{e}" (del /f /q "{e}" >nul 2>&1 & ping 127.0.0.1 -n 3 >nul))')
            lines.append(f'del /f /q "{e}.old" >nul 2>&1')
        lines += [f'rmdir "{INSTALL_DIR}" >nul 2>&1', 'del "%~f0"']
        bat = os.path.join(tempfile.gettempdir(), f"{APP_NAME}-remove.bat")
        with open(bat, "w", encoding="ascii", errors="replace") as f:
            f.write("\r\n".join(lines) + "\r\n")
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000 | 0x00000008, close_fds=True)
    if not quiet:
        W.user32.MessageBoxW(None, f"{NAME_HE} הוסרה מהמחשב.", f"{NAME_HE} · {NAME_EN}", 0x40 | 0x100000)
    os._exit(0)


_mutex = None


def single_instance(wait_seconds=0):
    """Own the app-wide mutex. After an update the old instance may still be exiting, so retry."""
    global _mutex
    W.kernel32.CreateMutexW.restype = ctypes.c_void_p
    deadline = time.time() + wait_seconds
    while True:
        h = W.kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}.SingleInstance")
        if W.kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            _mutex = h
            return True
        W.kernel32.CloseHandle(ctypes.c_void_p(h))
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def release_single_instance():
    global _mutex
    if _mutex:
        W.kernel32.CloseHandle(ctypes.c_void_p(_mutex))
        _mutex = None


def setup_logging():
    migrate_old_settings()
    os.makedirs(APP_DIR, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    if sys.stdout and sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        log.addHandler(logging.StreamHandler(sys.stdout))


def main():
    global VERSION
    setup_logging()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp welcome window on high-DPI screens
    except Exception:
        pass
    if "--welcome-test" in sys.argv:
        show_welcome(AutoLanguage(), auto_close_ms=2500)
        return
    if "--uninstall" in sys.argv:
        uninstall()
        return
    if install_self():
        return
    for arg in sys.argv:
        if arg.startswith("--pretend-version="):  # testing aid for the updater
            VERSION = arg.split("=", 1)[1]
    if not single_instance(wait_seconds=15 if ("--after-update" in sys.argv or "--after-install" in sys.argv) else 0):
        log.info("already running")
        return
    log.info("Safa Brura %s starting from %s", VERSION, sys.executable)
    if is_frozen():
        try:
            register_uninstall(sys.executable)
        except Exception:
            log.exception("register uninstall")
    if "--after-update" in sys.argv or "--after-install" in sys.argv:
        import updater
        threading.Thread(target=updater.cleanup_old, daemon=True).start()
    app = AutoLanguage()
    if "--install-startup" in sys.argv:
        set_startup(True)
    if not app.cfg.get("welcomed") and "--no-tray" not in sys.argv:
        threading.Thread(target=show_welcome, args=(app,), daemon=True).start()
    if "--no-hooks" not in sys.argv:  # testing aid: update tests must not touch the keyboard
        threading.Thread(target=app.run_hooks, name="hooks", daemon=True).start()
    else:
        app.init_engine()
    threading.Thread(target=app.run_worker, name="worker", daemon=True).start()
    if "--no-tray" in sys.argv:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    app.run_tray()


if __name__ == "__main__":
    main()
