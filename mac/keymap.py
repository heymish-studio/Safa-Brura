"""Static key tables for the Mac build (pure Python, testable on any OS).

The shared engine thinks in Windows virtual-key codes, so the Mac layer translates each Mac hardware
keycode to the matching Windows VK and renders characters from fixed tables of the two supported
layouts: English (US / ABC / British) and Hebrew (Apple "Hebrew" and "Hebrew-PC"; their letter
positions are identical). Hebrew-QWERTY (phonetic) is a different layout and is not supported.
"""

EN_ID = 0x0409
HE_ID = 0x040D
TAGS = {EN_ID: "en-US", HE_ID: "he-IL"}

# Mac hardware keycode (kVK_*) -> Windows virtual-key code
MAC_TO_VK = {
    0x00: ord("A"), 0x01: ord("S"), 0x02: ord("D"), 0x03: ord("F"), 0x04: ord("H"), 0x05: ord("G"),
    0x06: ord("Z"), 0x07: ord("X"), 0x08: ord("C"), 0x09: ord("V"), 0x0B: ord("B"), 0x0C: ord("Q"),
    0x0D: ord("W"), 0x0E: ord("E"), 0x0F: ord("R"), 0x10: ord("Y"), 0x11: ord("T"), 0x1F: ord("O"),
    0x20: ord("U"), 0x22: ord("I"), 0x23: ord("P"), 0x25: ord("L"), 0x26: ord("J"), 0x28: ord("K"),
    0x2D: ord("N"), 0x2E: ord("M"),
    0x12: ord("1"), 0x13: ord("2"), 0x14: ord("3"), 0x15: ord("4"), 0x17: ord("5"), 0x16: ord("6"),
    0x1A: ord("7"), 0x1C: ord("8"), 0x19: ord("9"), 0x1D: ord("0"),
    0x29: 0xBA,  # ;
    0x18: 0xBB,  # =
    0x2B: 0xBC,  # ,
    0x1B: 0xBD,  # -
    0x2F: 0xBE,  # .
    0x2C: 0xBF,  # /
    0x32: 0xC0,  # `
    0x21: 0xDB,  # [
    0x2A: 0xDC,  # backslash
    0x1E: 0xDD,  # ]
    0x27: 0xDE,  # '
    0x24: 0x0D, 0x4C: 0x0D,  # Return, keypad Enter
    0x30: 0x09,  # Tab
    0x31: 0x20,  # Space
    0x33: 0x08,  # Delete (backspace)
    0x35: 0x1B,  # Escape
    0x75: 0x2E,  # forward delete
    0x73: 0x24, 0x77: 0x23, 0x74: 0x21, 0x79: 0x22,  # Home End PageUp PageDown
    0x7B: 0x25, 0x7C: 0x27, 0x7D: 0x28, 0x7E: 0x26,  # Left Right Down Up
}
VK_TO_MAC = {}
for _mac, _vk in MAC_TO_VK.items():
    VK_TO_MAC.setdefault(_vk, _mac)

_LETTERS_HE = {
    "A": "ש", "B": "נ", "C": "ב", "D": "ג", "E": "ק", "F": "כ", "G": "ע", "H": "י", "I": "ן", "J": "ח",
    "K": "ל", "L": "ך", "M": "צ", "N": "מ", "O": "ם", "P": "פ", "Q": "/", "R": "ר", "S": "ד", "T": "א",
    "U": "ו", "V": "ה", "W": "'", "X": "ס", "Y": "ט", "Z": "ז",
}
_DIGITS_SHIFT = dict(zip("1234567890", "!@#$%^&*()"))
_PUNCT_EN = {0xBA: (";", ":"), 0xBB: ("=", "+"), 0xBC: (",", "<"), 0xBD: ("-", "_"), 0xBE: (".", ">"),
             0xBF: ("/", "?"), 0xC0: ("`", "~"), 0xDB: ("[", "{"), 0xDC: ("\\", "|"), 0xDD: ("]", "}"),
             0xDE: ("'", '"')}
_PUNCT_HE = {0xBA: ("ף", ":"), 0xBB: ("=", "+"), 0xBC: ("ת", ">"), 0xBD: ("-", "_"), 0xBE: ("ץ", "<"),
             0xBF: (".", "?"), 0xC0: (";", "~"), 0xDB: ("]", "}"), 0xDC: ("\\", "|"), 0xDD: ("[", "{"),
             0xDE: (",", '"')}


def char_for(vk, layout_id, shift=False, caps=False):
    """Character a key produces in the given layout ('' for non-character keys)."""
    if vk == 0x20:
        return " "   # same as Windows: space renders as a character (a word boundary, not a letter)
    if vk in (0x0D, 0x09):
        return chr(vk)  # Return / Tab, as Windows renders them
    if ord("A") <= vk <= ord("Z"):
        letter = chr(vk)
        if layout_id == HE_ID:
            return letter if shift else _LETTERS_HE[letter]
        upper = shift != caps
        return letter if upper else letter.lower()
    if ord("0") <= vk <= ord("9"):
        d = chr(vk)
        return _DIGITS_SHIFT[d] if shift else d
    table = _PUNCT_HE if layout_id == HE_ID else _PUNCT_EN
    if vk in table:
        return table[vk][1 if shift else 0]
    return ""


def keys_for_text(text, layout_id):
    """(vk, shift) pairs that type `text` in the layout; None if a character is not on the layout."""
    out = []
    for ch in text:
        if ch == " ":
            out.append((0x20, False))
            continue
        found = None
        for vk in list(range(ord("A"), ord("Z") + 1)) + list(range(ord("0"), ord("9") + 1)) + list(_PUNCT_EN):
            for shift in (False, True):
                if char_for(vk, layout_id, shift) == ch:
                    found = (vk, shift)
                    break
            if found:
                break
        if not found:
            return None
        out.append(found)
    return out


# Apple input-source IDs we understand
HEBREW_SOURCES = {"com.apple.keylayout.Hebrew", "com.apple.keylayout.Hebrew-PC"}
ENGLISH_SOURCES = {"com.apple.keylayout.US", "com.apple.keylayout.ABC", "com.apple.keylayout.British",
                   "com.apple.keylayout.British-PC", "com.apple.keylayout.USExtended",
                   "com.apple.keylayout.Australian", "com.apple.keylayout.Canadian",
                   "com.apple.keylayout.Irish", "com.apple.keylayout.USInternational-PC"}


def classify_source(source_id):
    """Layout id for an Apple input-source id, or 0 when the layout is not supported."""
    if source_id in HEBREW_SOURCES:
        return HE_ID
    if source_id in ENGLISH_SOURCES:
        return EN_ID
    return 0
