"""Language-decision engine for Safa Brura.

Given the physical keys a user pressed and the layout they were typed under, decide whether the
word is gibberish in that layout but a real, common word in another installed layout.

Rules (deliberately asymmetric, to keep false positives rare):
  * the word as typed must NOT be acceptable in the current language (lenient check: frequency
    list OR the Windows spell checker for words of 3+ letters);
  * the converted word MUST be a common word in the other language (strict check: frequency list
    with a minimum count, plus language-specific shape rules such as Hebrew final letters).
"""
import os
import sys
from dataclasses import dataclass, field

import winapi as W

HEBREW_FINALS = "ךםןףץ"
HEBREW_APOSTROPHE_AFTER = "גזצתד"


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def load_frequency(path):
    freq = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    freq[parts[0]] = int(parts[1])
    except OSError:
        pass
    return freq


@dataclass
class KeyRec:
    vk: int
    scan: int
    shift: bool
    caps: bool
    layout: int = 0  # layout id (low 16 bits of HKL) the key is currently rendered in
    fixed: bool = False  # True once an automatic fix rewrote this key into another layout


class Language:
    """One installed keyboard language: layout handle, frequency list, Windows spell checker."""

    def __init__(self, tag, hkl, min_target_count=200, min_short_count=5000):
        self.tag = tag
        self.hkl = hkl
        self.id = W.layout_id(hkl)
        self.short = tag.split("-")[0].lower()
        self.freq = load_frequency(resource_path(os.path.join("data", f"{self.short}_50k.txt")))
        self.min_target_count = min_target_count
        self.min_short_count = min_short_count
        # below this length only the frequency list counts (the Hebrew checker accepts almost any
        # 3-letter string as prefix+word, so it is only trusted for longer words)
        self.spell_min_len = 4 if self.short == "he" else 3
        self.spell = None
        try:
            from spellcheck import SpellChecker
            self.spell = SpellChecker(tag)
        except Exception:
            self.spell = None

    # -- shape rules ---------------------------------------------------------
    def well_formed(self, word):
        if not word:
            return False
        if not all(c.isalpha() or c == "'" for c in word):
            return False
        if self.short == "he":
            body = word[:-1] if word.endswith("'") else word
            if any(c in HEBREW_FINALS for c in body[:-1]):
                return False
            for i, c in enumerate(word):
                if c == "'" and (i == 0 or word[i - 1] not in HEBREW_APOSTROPHE_AFTER):
                    return False
            if any("a" <= c.lower() <= "z" for c in word):
                return False
        else:
            if word.startswith("'") or word.endswith("'"):
                return False
        return True

    def plausible(self, word):
        """Lenient: could a user have meant this word in this language?"""
        if not self.well_formed(word):
            return False
        low = word.lower()
        if low in self.freq:
            return True
        if len(word) >= self.spell_min_len and self.spell is not None:
            try:
                return self.spell.is_word(word)
            except Exception:
                return False
        return False

    def confident(self, word):
        """Strict: a common, real word in this language."""
        if not self.well_formed(word):
            return False
        count = self.freq.get(word.lower(), 0)
        need = self.min_short_count if len(word) <= 2 else self.min_target_count
        return count >= need

    def render(self, keys):
        """Text these keys produce under this language's layout."""
        out = []
        for k in keys:
            ch = W.key_to_char(k.vk, k.scan, self.hkl, k.shift, k.caps)
            if k.shift and not (ch and ch.isalpha()):
                alt = W.key_to_char(k.vk, k.scan, self.hkl, False, k.caps)
                if alt and alt.isalpha():
                    ch = alt
            out.append(ch)
        return "".join(out)


@dataclass
class Decision:
    target: Language
    text: str
    source_text: str


class Engine:
    def __init__(self, languages, min_word_len=2, ignore_words=()):
        self.languages = languages  # list[Language], deduplicated by language id
        self.by_id = {l.id: l for l in languages}
        self.min_word_len = min_word_len
        self.ignore_words = set(w.lower() for w in ignore_words)

    def language_for(self, hkl):
        return self.by_id.get(W.layout_id(hkl))

    def is_word_key(self, rec, cur):
        """A key belongs to a word if it produces a letter (or apostrophe) in ANY installed layout."""
        for lang in self.languages:
            ch = lang.render([rec])
            if ch and (ch.isalpha() or ch == "'" or ch.isdigit()):
                return True
        return False

    def decide(self, keys, cur):
        """keys: list[KeyRec] of the current word; cur: Language they were typed in."""
        if cur is None or len(keys) < self.min_word_len:
            return None
        source = cur.render(keys)
        if not source or any(c.isdigit() for c in source):
            return None
        if source.lower() in self.ignore_words:
            return None
        if cur.plausible(source):
            return None
        for lang in self.languages:
            if lang is cur:
                continue
            text = lang.render(keys)
            if text.lower() in self.ignore_words:
                continue
            if lang.confident(text):
                return Decision(lang, text, source)
        return None

    def other_language(self, cur, keys=None):
        """Target for a manual conversion: the layout whose rendering looks most like real words."""
        others = [l for l in self.languages if l is not cur]
        if not others:
            return None
        if keys and len(others) > 1:
            for l in others:
                if l.confident(l.render(keys)) or l.plausible(l.render(keys)):
                    return l
        return others[0]


def build_languages(min_target_count=200, min_short_count=5000):
    """One Language per distinct language tag among the installed keyboard layouts."""
    langs = []
    seen = set()
    for hkl in W.installed_layouts():
        tag = W.layout_language_tag(hkl)
        lid = W.layout_id(hkl)
        if not tag or lid in seen:
            continue
        seen.add(lid)
        langs.append(Language(tag, hkl, min_target_count, min_short_count))
    return langs
