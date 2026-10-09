"""Offline evaluation of the decision engine: recall on common words typed in the wrong layout,
false positives on correct words and on common typos. Run: python test_engine.py"""
import ctypes
import string
import sys

sys.stdout.reconfigure(encoding="utf-8")
import winapi as W
from engine import Engine, KeyRec, build_languages

user32 = ctypes.windll.user32
user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
user32.VkKeyScanExW.restype = ctypes.c_short
langs = build_languages()
eng = Engine(langs)
by_short = {l.short: l for l in langs}
EN, HE = by_short["en"], by_short["he"]


def keys_for(text, lang):
    """Physical keys that type `text` under `lang`'s layout."""
    recs = []
    for ch in text:
        r = user32.VkKeyScanExW(ch, lang.hkl)
        if r == -1 or r == 0xFFFF:
            return None
        vk, mods = r & 0xFF, (r >> 8) & 0xFF
        scan = user32.MapVirtualKeyExW(vk, 0, lang.hkl)
        recs.append(KeyRec(vk, scan, bool(mods & 1), False, lang.id))
    return recs


def rendered_in(text, meant, typed):
    k = keys_for(text, meant)
    return k, (typed.render(k) if k else None)


# --- recall: Hebrew words typed while the layout is English ---------------
top_he = [w for w, _ in sorted(HE.freq.items(), key=lambda x: -x[1])[:3000] if len(w) >= 2]
hit = miss = 0
missed = []
for w in top_he:
    k = keys_for(w, HE)
    if not k:
        continue
    d = eng.decide(k, EN)
    if d and d.text == w:
        hit += 1
    else:
        miss += 1
        missed.append((w, EN.render(k)))
print(f"Hebrew typed in EN layout: fixed {hit}/{hit + miss} of top-3000; sample missed: {missed[:25]}")

# --- recall: English words typed while the layout is Hebrew ---------------
top_en = [w for w, _ in sorted(EN.freq.items(), key=lambda x: -x[1])[:3000] if len(w) >= 2 and w.isalpha()]
hit = miss = 0
missed = []
for w in top_en:
    k = keys_for(w, EN)
    if not k:
        continue
    d = eng.decide(k, HE)
    if d and d.text == w:
        hit += 1
    else:
        miss += 1
        missed.append((w, HE.render(k)))
print(f"English typed in HE layout: fixed {hit}/{hit + miss} of top-3000; sample missed: {missed[:25]}")

# --- false positives: correct English words must never be touched ---------
fp = [w for w in top_en if (k := keys_for(w, EN)) and eng.decide(k, EN)]
print(f"FP on correct English: {len(fp)} {fp[:20]}")
fp = [w for w in top_he if (k := keys_for(w, HE)) and eng.decide(k, HE)]
print(f"FP on correct Hebrew: {len(fp)} {fp[:20]}")

# --- false positives: English typos (adjacent swap / dropped letter) ------
typos = set()
for w in top_en[:600]:
    for i in range(len(w) - 1):
        typos.add(w[:i] + w[i + 1] + w[i] + w[i + 2:])
    for i in range(len(w)):
        typos.add(w[:i] + w[i + 1:])
typos = [t for t in typos if len(t) >= 2]
fp = [(t, d.text) for t in typos if (k := keys_for(t, EN)) and (d := eng.decide(k, EN))]
print(f"FP on English typos: {len(fp)}/{len(typos)} = {100 * len(fp) / len(typos):.1f}%  {fp[:30]}")

# --- false positives: Hebrew typos ----------------------------------------
typos = set()
for w in top_he[:600]:
    for i in range(len(w) - 1):
        typos.add(w[:i] + w[i + 1] + w[i] + w[i + 2:])
    for i in range(len(w)):
        typos.add(w[:i] + w[i + 1:])
typos = [t for t in typos if len(t) >= 2]
fp = [(t, d.text) for t in typos if (k := keys_for(t, HE)) and (d := eng.decide(k, HE))]
print(f"FP on Hebrew typos: {len(fp)}/{len(typos)} = {100 * len(fp) / len(typos):.1f}%  {fp[:30]}")

# --- 2-letter junk typed in EN ------------------------------------------
two = [a + b for a in string.ascii_lowercase for b in string.ascii_lowercase]
fp = [(t, d.text) for t in two if (k := keys_for(t, EN)) and (d := eng.decide(k, EN))]
print(f"2-letter EN strings auto-converted: {len(fp)}/676 {fp[:40]}")
