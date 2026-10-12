"""Wire the shared Windows-era modules to the Mac adapter. Import this before engine/autolang."""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def install():
    for p in (HERE, ROOT):
        if p not in sys.path:
            sys.path.insert(0, p)
    if sys.platform == "darwin":
        support = os.path.expanduser("~/Library/Application Support")
        os.environ.setdefault("APPDATA", support)  # autolang keeps settings in $APPDATA/SafaBrura
        if "comtypes" not in sys.modules:          # the shared worker calls comtypes.CoInitialize()
            stub = types.ModuleType("comtypes")
            stub.CoInitialize = lambda: None
            sys.modules["comtypes"] = stub
    import macapi
    import keymap
    sys.modules["winapi"] = macapi
    import autolang
    import engine
    # Mac defaults: no Windows-only exclusions, no Alt+Shift fallback, Mac apps that must not be touched
    autolang.DEFAULT_CONFIG.update({
        "fallback_switch_hotkey": "",
        "hotkeys": ["shift+backspace"],
        "hotkeys_all": ["ctrl+shift+backspace"],
        "excluded_window_classes": [],
        "excluded_processes": [
            "com.apple.terminal", "com.googlecode.iterm2", "dev.warp.warp-stable", "com.mitchellh.ghostty",
            "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
            "com.apple.keychainaccess", "com.apple.passwords", "com.microsoft.rdc.macos",
            "com.teamviewer.teamviewer", "com.anydesk.anydeskmac",
        ],
    })
    autolang.modifiers_down = macapi.modifiers_down
    return autolang, engine, macapi, keymap
