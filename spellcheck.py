"""Thin wrapper over the Windows built-in spell checker (ISpellChecker COM API).
No dictionaries to download: uses the dictionaries Windows ships with each installed language.
"""
import ctypes
from ctypes import POINTER, c_wchar_p, c_int, c_ulong, HRESULT
import comtypes
from comtypes import GUID, IUnknown, COMMETHOD, CoCreateInstance

CLSID_SpellCheckerFactory = GUID("{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}")


class IEnumString(IUnknown):
    _iid_ = GUID("{00000101-0000-0000-C000-000000000046}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["in"], c_ulong, "celt"),
                  (["out"], POINTER(c_wchar_p), "rgelt"), (["out"], POINTER(c_ulong), "pceltFetched")),
        COMMETHOD([], HRESULT, "Skip", (["in"], c_ulong, "celt")),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "Clone", (["out"], POINTER(POINTER(IUnknown)), "ppenum")),
    ]


class ISpellingError(IUnknown):
    _iid_ = GUID("{B7C82D61-FBE8-4B47-9B27-6C0D2E0DE0A3}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_StartIndex", (["out"], POINTER(c_ulong), "value")),
        COMMETHOD([], HRESULT, "get_Length", (["out"], POINTER(c_ulong), "value")),
        COMMETHOD([], HRESULT, "get_CorrectiveAction", (["out"], POINTER(c_int), "value")),
        COMMETHOD([], HRESULT, "get_Replacement", (["out"], POINTER(c_wchar_p), "value")),
    ]


class IEnumSpellingError(IUnknown):
    _iid_ = GUID("{803E3BD4-2828-4410-8290-418D1D73C762}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["out"], POINTER(POINTER(ISpellingError)), "value")),
    ]


class ISpellChecker(IUnknown):
    _iid_ = GUID("{B6FD0B71-E2BC-4653-8D05-F197E412770B}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_LanguageTag", (["out"], POINTER(c_wchar_p), "value")),
        COMMETHOD([], HRESULT, "Check", (["in"], c_wchar_p, "text"),
                  (["out"], POINTER(POINTER(IEnumSpellingError)), "value")),
        COMMETHOD([], HRESULT, "Suggest", (["in"], c_wchar_p, "word"),
                  (["out"], POINTER(POINTER(IEnumString)), "value")),
        COMMETHOD([], HRESULT, "Add", (["in"], c_wchar_p, "word")),
        COMMETHOD([], HRESULT, "Ignore", (["in"], c_wchar_p, "word")),
        COMMETHOD([], HRESULT, "AutoCorrect", (["in"], c_wchar_p, "from_"), (["in"], c_wchar_p, "to")),
    ]


class ISpellCheckerFactory(IUnknown):
    _iid_ = GUID("{8E018A9D-2415-4677-BF08-794EA61F94BB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_SupportedLanguages", (["out"], POINTER(POINTER(IEnumString)), "value")),
        COMMETHOD([], HRESULT, "IsSupported", (["in"], c_wchar_p, "languageTag"), (["out"], POINTER(c_int), "value")),
        COMMETHOD([], HRESULT, "CreateSpellChecker", (["in"], c_wchar_p, "languageTag"),
                  (["out"], POINTER(POINTER(ISpellChecker)), "value")),
    ]


class SpellChecker:
    """One Windows spell checker per language tag, e.g. 'he-IL' or 'en-US'."""

    def __init__(self, language_tag: str):
        comtypes.CoInitialize()
        self.factory = CoCreateInstance(CLSID_SpellCheckerFactory, interface=ISpellCheckerFactory)
        if not self.factory.IsSupported(language_tag):
            raise RuntimeError(f"Windows has no spell checker for {language_tag}")
        self.checker = self.factory.CreateSpellChecker(language_tag)
        self.tag = language_tag

    def is_word(self, word: str) -> bool:
        """True when Windows finds no spelling error in the word."""
        if not word:
            return False
        errors = self.checker.Check(word)
        err = errors.Next()  # NULL pointer (S_FALSE) means no spelling error
        return not bool(err)

    @staticmethod
    def supported_languages():
        comtypes.CoInitialize()
        factory = CoCreateInstance(CLSID_SpellCheckerFactory, interface=ISpellCheckerFactory)
        enum = factory.get_SupportedLanguages()
        out = []
        while True:
            try:
                s, n = enum.Next(1)
            except comtypes.COMError:
                break
            if not n:
                break
            out.append(s)
        return out


if __name__ == "__main__":
    print("supported:", SpellChecker.supported_languages())
    he = SpellChecker("he-IL")
    en = SpellChecker("en-US")
    for w in ["שלום", "akuo", "hello", "susu", "בדיקה", "כדגכשד", "Hello", "WiFi", "לך"]:
        print(f"{w!r:12} he={he.is_word(w)!s:5} en={en.is_word(w)}")
