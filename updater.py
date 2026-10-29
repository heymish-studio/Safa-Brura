"""Update check and self-update for Safa Brura.

Check: GitHub's public "latest release" API, no account needed. Self-update (frozen exe only):
download the new exe next to the running one, rename the running exe out of the way (Windows allows
renaming a running exe, not overwriting it), move the new one into place, start it, and exit. The new
instance deletes the leftover old file on start.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "heymish-studio/Safa-Brura"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "SafaBrura.exe"
OLD_SUFFIX = ".old"


def parse_version(tag):
    """'v1.2.0' -> (1, 2, 0); anything unparsable -> (0,)."""
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "SafaBrura-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check_latest(current_version):
    """Return {'version', 'url', 'size', 'page', 'notes'} if GitHub has a newer release, else None."""
    with _get(API_LATEST) as r:
        data = json.load(r)
    tag = data.get("tag_name", "")
    if parse_version(tag) <= parse_version(current_version):
        return None
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if not asset:
        return None
    return {
        "version": tag.lstrip("vV"),
        "url": asset["browser_download_url"],
        "size": asset.get("size", 0),
        "page": data.get("html_url", f"https://github.com/{REPO}/releases/latest"),
        "notes": summarize_notes(data.get("body", "")),
    }


def summarize_notes(body, limit=420):
    """First section of the release notes as plain text (markdown stripped)."""
    text = body.split("## הורדה")[0]
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*|`", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text.startswith("מה חדש"):
        text = text[len("מה חדש"):].strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def can_self_update():
    return getattr(sys, "frozen", False)


def install(info, progress=None):
    """Download and swap the exe, then start the new one. Caller must exit right after it returns."""
    exe = sys.executable
    folder = os.path.dirname(exe)
    tmp = os.path.join(folder, ASSET_NAME + ".download")
    with _get(info["url"], timeout=60) as r, open(tmp, "wb") as f:
        got = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, info.get("size") or 0)
    if info.get("size") and os.path.getsize(tmp) != info["size"]:
        os.remove(tmp)
        raise IOError("download incomplete")
    with open(tmp, "rb") as f:
        if f.read(2) != b"MZ":
            os.remove(tmp)
            raise IOError("download is not a Windows program")

    old = exe + OLD_SUFFIX
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)          # rename the running exe out of the way
    try:
        os.replace(tmp, exe)
    except OSError:
        os.replace(old, exe)      # put the original back
        raise
    launch(exe, ["--after-update"] + [a for a in sys.argv[1:] if a == "--no-hooks"])


def launch(exe, args):
    """Start another copy of the program, detached, with a clean PyInstaller environment (a child that
    inherits the parent's temp folder loses its data files when the parent exits)."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in list(env):
        if k.startswith("_PYI") or k.startswith("_MEI"):
            del env[k]
    subprocess.Popen([exe] + list(args), cwd=os.path.dirname(exe), env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def cleanup_old():
    """Delete the previous exe left behind by an update (retries: it may still be exiting)."""
    if not can_self_update():
        return
    old = sys.executable + OLD_SUFFIX
    import time
    for _ in range(20):
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            return
        except OSError:
            time.sleep(0.5)
