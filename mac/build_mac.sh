#!/usr/bin/env bash
# Builds dist/SafaBrura.app and dist/SafaBrura-mac.zip on a Mac (Apple silicon or Intel).
# Runs the logic simulation before the build and the app's own selftest after it.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(python3 -c "import re;print(re.search(r'MAC_VERSION = \"([^\"]+)\"', open('mac/safabrura_mac.py',encoding='utf-8').read()).group(1))")"
echo "== Safa Brura for Mac $VERSION"

python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r mac/requirements-mac.txt

echo "== logic simulation"
python3 mac/test_mac_logic.py

echo "== build"
rm -rf build dist
python3 -m PyInstaller --noconfirm --clean --windowed --name SafaBrura \
  --osx-bundle-identifier studio.heymish.safabrura \
  --add-data "data:data" --paths mac --paths . \
  --hidden-import engine --hidden-import autolang --hidden-import macapi --hidden-import keymap \
  --hidden-import bootstrap --hidden-import logic_sim --hidden-import updater \
  --collect-submodules rumps \
  mac/safabrura_mac.py

PLIST=dist/SafaBrura.app/Contents/Info.plist
plutil -replace LSUIElement -bool true "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace CFBundleDisplayName -string "Safa Brura" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "Heymish Studio" "$PLIST"
codesign --force --deep --sign - dist/SafaBrura.app

echo "== selftest inside the built app"
dist/SafaBrura.app/Contents/MacOS/SafaBrura --selftest

echo "== package"
ditto -c -k --keepParent dist/SafaBrura.app dist/SafaBrura-mac.zip
ls -la dist/SafaBrura-mac.zip
