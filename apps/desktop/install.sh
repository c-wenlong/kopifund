#!/usr/bin/env bash
# G23: the one installer for Kopifund.app. It builds the Tauri shell in one
# apps/desktop folder and swaps the new bundle into one Applications folder.
# `make desktop` runs it on this checkout into ~/Applications;
# ./build_macos.sh runs it on its copy of one commit into /Applications.
#
#   apps/desktop/install.sh [--quit-open] <apps/desktop folder> <Applications folder>
#
# --quit-open quits the Kopifund that is running first, and stops if it will
# not quit (build_macos.sh: the open app's server reads a build folder that
# is about to be deleted).
#
# Both routes build in one Rust cache (CARGO_TARGET_DIR, else
# ~/Library/Caches/Kopifund/cargo-target) and so one bundle folder, so a
# build takes the build lock in ~/Library/Application Support/Kopifund. A
# caller that already holds it (build_macos.sh, for its whole run) says so
# with KOPIFUND_BUILD_LOCK_HELD=1.
set -euo pipefail

QUIT=0
if [ "${1:-}" = --quit-open ]; then QUIT=1; shift; fi
[ $# -eq 2 ] || { sed -n '7p' "$0" | sed 's/^# //' >&2; exit 2; }
DT="$1"
APPS="$2"
APP_ID="com.kopifund.desktop"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\ninstall.sh: %s\n' "$*" >&2; exit 1; }

[ -f "$DT/package.json" ] || fail "$DT is not the desktop app's folder (no package.json)"

CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/Library/Caches/Kopifund/cargo-target}"
case "$CARGO_TARGET_DIR" in /*) ;; *) CARGO_TARGET_DIR="$PWD/$CARGO_TARGET_DIR" ;; esac
export CARGO_TARGET_DIR

# The lock is a kernel flock on file descriptor 9, which this script and
# everything it starts hold open: the kernel drops it when the run ends however
# it ends, so it is never stale and two runs can never both hold it. (python3
# takes it: macOS ships no flock(1).)
if [ "${KOPIFUND_BUILD_LOCK_HELD:-}" != 1 ]; then
  SUPPORT="$HOME/Library/Application Support/Kopifund"
  mkdir -p "$SUPPORT"
  exec 9>"$SUPPORT/build.lock.file"
  python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null \
    || fail "another build (build_macos.sh or make desktop) is running; wait for it to finish"
fi

say "Building Kopifund.app (the first build compiles Rust for a few minutes)"
BUILT="$CARGO_TARGET_DIR/release/bundle/macos/Kopifund.app"
# the Rust cache is shared, so the bundle from the last build sits where this one
# lands: remove it, and accept only a bundle newer than this build's start
STARTED="$(mktemp -t kopifund-build)"
trap 'rm -f "$STARTED"' EXIT
rm -rf "$BUILT"
(cd "$DT" && npm ci --no-audit --no-fund --loglevel=error && npm run tauri -- build --bundles app -- --locked)
[ -d "$BUILT" ] || fail "the build finished but $BUILT is not there"
[ "$BUILT/Contents/Info.plist" -nt "$STARTED" ] || fail "$BUILT is older than this build; it is not the one just built"

running() { [ "$(osascript -e "application id \"$APP_ID\" is running" 2>/dev/null)" = true ]; }
if [ "$QUIT" = 1 ] && running; then
  say "Quitting the Kopifund that is open"
  osascript -e "tell application id \"$APP_ID\" to quit" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    running || break
    sleep 0.5
  done
  # its server still reads the old build folder: replace nothing while it runs
  ! running || fail "Kopifund did not quit; quit it yourself and run this again"
fi

say "Installing in $APPS"
mkdir -p "$APPS"
# copied beside the old app first, then swapped in: an interrupted or failed copy
# leaves the old app where it was
NEW="$APPS/.Kopifund.app.new"
OLD="$APPS/.Kopifund.app.old"
rm -rf "$NEW" "$OLD"
ditto "$BUILT" "$NEW" || { rm -rf "$NEW"; fail "could not copy the app into $APPS; the old one is still there"; }
# two renames on one volume: the old app is only deleted once the new one is in place
if [ -e "$APPS/Kopifund.app" ]; then mv "$APPS/Kopifund.app" "$OLD"; fi
if ! mv "$NEW" "$APPS/Kopifund.app"; then
  [ ! -e "$OLD" ] || mv "$OLD" "$APPS/Kopifund.app"
  fail "could not put the new app in place; the old one is back"
fi
rm -rf "$OLD"
