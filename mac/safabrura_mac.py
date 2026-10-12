"""שפה ברורה · Safa Brura for macOS (beta).

Menu-bar app. A Quartz event tap feeds every key to the same handler the Windows version uses
(autolang.AutoLanguage), through the Mac adapter in macapi.py. Needs the Accessibility permission
(and on recent macOS, Input Monitoring). Password fields are protected by macOS Secure Input.
"""
import json
import os
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bootstrap  # noqa: E402

autolang, engine, macapi, keymap = bootstrap.install()
W, K = macapi, keymap
log = autolang.log

MAC_VERSION = "0.1.0"
NAME = "שפה ברורה · Safa Brura"
REPO = "heymish-studio/Safa-Brura"
MAC_ASSET = "SafaBrura-mac.zip"
SETTINGS_ACCESSIBILITY = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
SETTINGS_INPUT_MONITORING = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/studio.heymish.safabrura.plist")

WELCOME = """התוכנה יושבת בשורת התפריטים למעלה (Aא) ומתקנת מילים שהקלדתם בשפה הלא נכונה.

הקלדתם "akuo" כשהמקלדת באנגלית? זה יהפוך ל"שלום", והמקלדת תעבור לעברית. וגם הפוך.

Shift+Delete ממיר ידנית את המילה האחרונה, או מבטל תיקון שקרה הרגע.
Control+Shift+Delete ממיר את כל מה שהקלדתם מאז הלחיצה האחרונה.

כדי לעבוד, macOS יבקש לאשר לתוכנה גישה למקלדת (נגישות). זה הכרחי: בלי זה היא לא רואה מה מקלידים. בשדות סיסמה macOS חוסם אותה בכל מקרה.

זו גרסת ניסיון. אם משהו מתנהג מוזר, ספרו לנו."""


# ------------------------------------------------------------------ spell checking -----
class MacSpell:
    """NSSpellChecker wrapper with the interface the engine expects (is_word)."""

    def __init__(self, language):
        from AppKit import NSSpellChecker
        self.checker = NSSpellChecker.sharedSpellChecker()
        self.language = language

    def is_word(self, word):
        def run():
            rng, _ = self.checker.checkSpellingOfString_startingAt_language_wrap_inSpellDocumentWithTag_wordCount_(
                word, 0, self.language, False, 0, None)
            return rng.length == 0
        return bool(W.on_main(run))


def available_spell_languages():
    from AppKit import NSSpellChecker
    return [str(x) for x in (NSSpellChecker.sharedSpellChecker().availableLanguages() or [])]


def attach_spellers(app):
    """Use the macOS spell checker for each language it actually supports; otherwise word lists only."""
    try:
        avail = available_spell_languages()
    except Exception:
        log.exception("spell checker")
        return
    for lang in app.languages:
        code = next((a for a in avail if a == lang.short), None) or \
            next((a for a in avail if a.split("_")[0] == lang.short), None)
        lang.spell = MacSpell(code) if code else None
    log.info("spell checkers: %s", {l.short: (l.spell.language if l.spell else None) for l in app.languages})


# ------------------------------------------------------------------ event tap ----------
class Tap:
    def __init__(self, app):
        self.app = app
        self.tap = None

    def start(self):
        import Quartz
        mask = 0
        for t in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp, Quartz.kCGEventLeftMouseDown,
                  Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown):
            mask |= Quartz.CGEventMaskBit(t)
        self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                           Quartz.kCGEventTapOptionDefault, mask, self.callback, None)
        if not self.tap:
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        log.info("event tap installed")
        return True

    def callback(self, proxy, etype, event, refcon):
        import Quartz
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self.tap, True)
                return event
            if etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown):
                self.app.reset("mouse")
                return event
            tag = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
            if tag == W.INJECT_TAG:
                return event  # our own typing
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            vk = K.MAC_TO_VK.get(keycode)
            if vk is None:
                return event  # F-keys, media keys, fn...
            W.set_event_flags(Quartz.CGEventGetFlags(event))
            down = etype == Quartz.kCGEventKeyDown
            kb = W.KeyEvent(vk, keycode, W.LLKHF_INJECTED if tag == W.REPLAY_TAG else 0, tag)
            if self.app._handle_key(kb, W.WM_KEYDOWN if down else W.WM_KEYUP):
                return None  # swallowed: the worker types it after the fix
        except Exception:
            log.exception("tap callback")
        return event


# ------------------------------------------------------------------ helpers -------------
def bundle_path():
    exe = os.path.realpath(sys.executable)
    marker = ".app/Contents/MacOS/"
    return exe[: exe.index(marker) + 4] if marker in exe else None


def login_item_enabled():
    return os.path.exists(LAUNCH_AGENT)


def set_login_item(enable):
    if not enable:
        try:
            os.remove(LAUNCH_AGENT)
        except OSError:
            pass
        return
    app = bundle_path()
    if not app:
        return
    os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>studio.heymish.safabrura</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-g</string><string>{app}</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
"""
    with open(LAUNCH_AGENT, "w", encoding="utf-8") as f:
        f.write(plist)


def single_instance():
    import fcntl
    os.makedirs(autolang.APP_DIR, exist_ok=True)
    fh = open(os.path.join(autolang.APP_DIR, "mac.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh


def permissions_granted(prompt=False):
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        log.exception("AX check")
        return False


def request_input_monitoring():
    try:
        import Quartz
        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
    except Exception:
        pass


def latest_mac_release():
    """(version, page_url) of the newest Mac release on GitHub if newer than this build, else None."""
    import ssl
    import urllib.request
    import updater
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=30",
                                 headers={"User-Agent": "SafaBrura-mac", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        releases = json.load(r)
    best = None
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith("mac-v") or rel.get("draft"):
            continue
        if not any(a.get("name") == MAC_ASSET for a in rel.get("assets", [])):
            continue
        v = updater.parse_version(tag)
        if v > updater.parse_version(MAC_VERSION) and (best is None or v > best[0]):
            best = (v, tag[len("mac-v"):], rel.get("html_url"))
    return (best[1], best[2]) if best else None


# ------------------------------------------------------------------ menu-bar app --------
def run_app():
    import rumps

    lock = single_instance()
    if lock is None:
        log.info("already running")
        return
    autolang.setup_logging()
    log.info("Safa Brura for Mac %s starting", MAC_VERSION)

    app = autolang.AutoLanguage()
    app.init_engine()
    attach_spellers(app)
    threading.Thread(target=app.run_worker, name="worker", daemon=True).start()
    tap = Tap(app)

    class Menu(rumps.App):
        def __init__(self):
            super().__init__("SafaBrura", title="Aא", quit_button=None)
            self.update_url = None
            self.toggle = rumps.MenuItem("תיקון אוטומטי פעיל", callback=self.on_toggle)
            self.toggle.state = 1 if app.cfg["enabled"] else 0
            self.login = rumps.MenuItem("הפעלה עם הכניסה למק", callback=self.on_login)
            self.login.state = 1 if login_item_enabled() else 0
            self.perm = rumps.MenuItem("צריך לאשר גישה למקלדת, לחצו כאן", callback=self.on_permissions)
            self.update_item = rumps.MenuItem("", callback=self.on_update)
            self.header = f"{NAME}  {MAC_VERSION} (ניסיון)"
            self.menu = [
                rumps.MenuItem(self.header),
                self.perm,
                self.toggle,
                None,
                rumps.MenuItem("המרת המילה האחרונה / ביטול: Shift+Delete"),
                rumps.MenuItem("המרת הכול: Control+Shift+Delete"),
                None,
                self.login,
                rumps.MenuItem("בדיקת עדכונים", callback=self.on_check),
                rumps.MenuItem("הסבר קצר", callback=self.on_help),
                rumps.MenuItem("פתח קובץ הגדרות", callback=self.on_config),
                rumps.MenuItem("פתח יומן", callback=self.on_log),
                None,
                rumps.MenuItem("יציאה", callback=self.on_quit),
            ]
            self.tap_timer = rumps.Timer(self.try_tap, 2)
            self.tap_timer.start()
            rumps.Timer(self.first_run, 1).start()
            rumps.Timer(self.periodic_update_check, 24 * 3600).start()
            threading.Timer(30, self.check_updates).start()

        # -- startup / permissions
        def first_run(self, timer):
            timer.stop()
            if not app.cfg.get("welcomed"):
                rumps.alert(title=NAME, message=WELCOME, ok="הבנתי, התחל")
                with app.lock:
                    app.cfg["welcomed"] = True
                    autolang.save_config(app.cfg)
                if not login_item_enabled():
                    set_login_item(True)
                    self.login.state = 1
            if not permissions_granted(prompt=True):
                request_input_monitoring()

        def try_tap(self, timer):
            if tap.tap is None and tap.start():
                timer.stop()
                try:
                    del self.menu[self.perm.title]
                except KeyError:
                    pass
                rumps.notification(NAME, "", "מוכן. אפשר להקליד.")

        def on_permissions(self, _):
            permissions_granted(prompt=True)
            webbrowser.open(SETTINGS_ACCESSIBILITY)

        # -- menu actions
        def on_toggle(self, item):
            with app.lock:
                app.cfg["enabled"] = not app.cfg["enabled"]
                autolang.save_config(app.cfg)
            item.state = 1 if app.cfg["enabled"] else 0
            self.title = "Aא" if app.cfg["enabled"] else "Aא·"

        def on_login(self, item):
            set_login_item(not login_item_enabled())
            item.state = 1 if login_item_enabled() else 0

        def on_help(self, _):
            rumps.alert(title=NAME, message=WELCOME, ok="סגור")

        def on_config(self, _):
            autolang.save_config(app.cfg)
            os.system(f'open -t "{autolang.CONFIG_PATH}"')

        def on_log(self, _):
            os.system(f'open -t "{autolang.LOG_PATH}"')

        def on_quit(self, _):
            rumps.quit_application()

        # -- updates
        def periodic_update_check(self, _):
            threading.Thread(target=self.check_updates, daemon=True).start()

        def check_updates(self, manual=False):
            """Network call on a background thread; every UI change goes back to the main thread."""
            try:
                found = latest_mac_release()
            except Exception as e:
                log.info("update check failed: %s", e)
                if manual:
                    W.on_main(lambda: rumps.notification(NAME, "", "לא הצלחתי לבדוק עדכונים."))
                return
            if not found:
                if manual:
                    W.on_main(lambda: rumps.notification(NAME, "", f"יש לכם את הגרסה האחרונה ({MAC_VERSION})."))
                return
            W.on_main(lambda: self.show_update(found[0], found[1], manual))

        def show_update(self, version, url, manual):
            self.update_url = url
            self.update_item.title = f"גרסה {version} זמינה, לחצו להורדה"
            if self.update_item.title not in self.menu:
                self.menu.insert_after(self.header, self.update_item)
            if manual or app.cfg.get("notified_version_mac") != version:
                rumps.notification(NAME, "", f"גרסה {version} זמינה. לחצו על Aא למעלה כדי להוריד.")
                with app.lock:
                    app.cfg["notified_version_mac"] = version
                    autolang.save_config(app.cfg)

        def on_check(self, _):
            threading.Thread(target=self.check_updates, kwargs={"manual": True}, daemon=True).start()

        def on_update(self, _):
            if self.update_url:
                webbrowser.open(self.update_url)

    Menu().run()


# ------------------------------------------------------------------ self test -----------
def selftest():
    """Runs inside the built app on a Mac (CI). Checks imports, input sources, spell checker,
    bundled word lists and the full typing logic on a virtual screen. Never touches the keyboard."""
    import logic_sim
    ok = True
    print(f"Safa Brura Mac selftest, version {MAC_VERSION}, python {sys.version.split()[0]}")
    for mod in ("Quartz", "AppKit", "rumps", "ApplicationServices", "objc", "certifi"):
        try:
            __import__(mod)
            print("import", mod, "ok")
        except Exception as e:
            ok = False
            print("import", mod, "FAILED", e)
    try:
        print("input sources:", [(sid, hex(lid)) for sid, lid, _ in W.enabled_sources()])
        print("current source:", W.current_source_id())
    except Exception as e:
        ok = False
        print("input sources FAILED", e)
    try:
        avail = available_spell_languages()
        print("spell languages:", "he" in avail and "he" or "no he", "/", "en" in avail and "en" or "no en")
    except Exception as e:
        print("spell check FAILED", e)
    print("word lists:", {k: len(engine.load_frequency(engine.resource_path(os.path.join("data", f"{k}_50k.txt"))))
                          for k in ("he", "en")})
    passed, total = logic_sim.run(autolang, macapi, keymap, after_init=attach_spellers)
    ok = ok and passed == total
    passed2, total2 = logic_sim.run(autolang, macapi, keymap, spell_he=False, after_init=attach_spellers)
    ok = ok and passed2 == total2
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run_app()
