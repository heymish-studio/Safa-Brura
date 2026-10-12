"""Virtual-screen simulation of the shared key handler running on the Mac adapter.

Runs the real AutoLanguage handler with mac/macapi.py standing in for winapi, but replaces every call
that would touch the OS (posting events, input sources, frontmost app) with edits of a Python string.
Used by mac/test_mac_logic.py on any OS and by `SafaBrura --selftest` inside the built Mac app.
No keystrokes are ever sent.
"""


def run(autolang, macapi, keymap, spell_he=True, verbose=True, after_init=None):
    """Returns (passed, total)."""
    K = keymap
    W = macapi

    class Sim:
        screen = ""
        layout = K.EN_ID
        shift = False
        ctrl = False

    sim = Sim()

    def replace_text(n, t):
        if n:
            sim.screen = sim.screen[:-n]
        sim.screen += t

    def send_vk(vk, scan=0):
        if vk == W.VK_SPACE:
            sim.screen += " "
        elif vk == W.VK_RETURN:
            sim.screen += "\n"

    saved = {name: getattr(W, name) for name in (
        "replace_text", "send_text", "send_backspaces", "send_vk", "send_inputs", "beep", "focus_window",
        "current_layout", "request_layout", "focused_is_password", "foreground_process_name",
        "installed_layouts")}
    saved_mods = autolang.modifiers_down
    saved_save = autolang.save_config
    try:
        W.replace_text = replace_text
        W.send_text = lambda t: replace_text(0, t)
        W.send_backspaces = lambda n: replace_text(n, "")
        W.send_vk = send_vk
        W.send_inputs = lambda inputs: None
        W.beep = lambda: None
        W.focus_window = lambda: (1, 1)
        W.current_layout = lambda: sim.layout
        W.request_layout = lambda hkl: setattr(sim, "layout", W.layout_id(hkl))
        W.focused_is_password = lambda: False
        W.foreground_process_name = lambda: "com.apple.textedit"
        W.installed_layouts = lambda: [K.EN_ID, K.HE_ID]
        autolang.modifiers_down = lambda: ({"shift"} if sim.shift else set()) | ({"ctrl"} if sim.ctrl else set())
        autolang.save_config = lambda cfg: None

        app = autolang.AutoLanguage()
        app.cfg["ignore_words"] = []
        app.cfg["fallback_switch_hotkey"] = ""
        app.init_engine()
        if after_init:
            after_init(app)
        if not spell_he:
            for lang in app.languages:
                if lang.short == "he":
                    lang.spell = None
        langs = {l.id: l for l in app.languages}

        def switch(target):
            sim.layout = target.id
            app.layout_id = target.id
        app._switch_layout = switch

        def press(vk, shift=False):
            sim.shift = shift
            mac = K.VK_TO_MAC[vk]
            kb = W.KeyEvent(vk, mac, 0, 0)
            swallowed = app._handle_key(kb, W.WM_KEYDOWN)
            if not swallowed:
                if vk == W.VK_BACK:
                    sim.screen = sim.screen[:-1]
                else:
                    sim.screen += K.char_for(vk, sim.layout, shift)
            while not app.jobs.empty():
                kind, job = app.jobs.get()
                if kind == "fix":
                    app._do_fix(job)
                elif kind == "manual":
                    app._do_manual(job)
                app._end_fix()
            sim.shift = False

        def type_as(text, meant):
            for vk, shift in K.keys_for_text(text, meant):
                press(vk, shift)

        def reset(layout):
            sim.screen = ""
            sim.layout = layout
            app.reset()
            app.layout_id = layout

        results = []

        def check(name, want, want_layout):
            ok = sim.screen == want and sim.layout == want_layout
            results.append(ok)
            if verbose:
                print(f"{'PASS' if ok else 'FAIL'} {name}: {sim.screen!r}" + ("" if ok else f"  want {want!r} layout {want_layout:#x}, got {sim.layout:#x}"))

        EN, HE = K.EN_ID, K.HE_ID
        reset(EN); type_as("שלום ", HE)
        check("Hebrew typed on the English layout", "שלום ", HE)
        reset(HE); type_as("hello ", EN)
        check("English typed on the Hebrew layout", "hello ", EN)
        reset(EN); type_as("אם אתה רוצה ", HE)
        check("whole sentence: to -> אם", "אם אתה רוצה ", HE)
        reset(EN); type_as("this is fine ", EN)
        check("correct English untouched", "this is fine ", EN)
        reset(HE); type_as("אני כבר מצאתי ", HE)
        check("correct Hebrew untouched", "אני כבר מצאתי ", HE)
        reset(EN); type_as("ak ", EN)
        press(W.VK_BACK, shift=True)
        check("Shift+Delete converts the last word", "של ", HE)
        reset(EN); type_as("שלום.", HE)
        check("punctuation key ends the word", "שלום.", HE)
        if verbose:
            print(f"{sum(results)}/{len(results)} passed (Hebrew spell checker {'on' if spell_he else 'off'})")
        return sum(results), len(results)
    finally:
        for name, fn in saved.items():
            setattr(W, name, fn)
        autolang.modifiers_down = saved_mods
        autolang.save_config = saved_save
