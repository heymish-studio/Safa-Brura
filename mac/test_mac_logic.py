"""Run the Mac logic simulation on any OS (no keystrokes are sent). python mac/test_mac_logic.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import bootstrap  # noqa: E402

autolang, engine, macapi, keymap = bootstrap.install()
import logic_sim  # noqa: E402

total_ok = total = 0
for spell_he in (True, False):
    ok, n = logic_sim.run(autolang, macapi, keymap, spell_he=spell_he)
    total_ok += ok
    total += n
print(f"\nALL: {total_ok}/{total}")
sys.exit(0 if total_ok == total else 1)
