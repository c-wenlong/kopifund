#!/usr/bin/env bash
# G23: one click builds Kopifund.app from one version of the code and puts it
# in /Applications.
#
#   ./build_macos.sh              # the latest merged code (origin/main)
#   ./build_macos.sh v0.3         # any branch, tag or commit
#   ./build_macos.sh --no-open    # install without opening the app
#
# The app runs serve.py from the folder it was built from (SPEC.md, "Desktop
# app (G23)"). Building in this checkout would tie the app to whatever branch
# the checkout has at the time, so this script copies one commit into its own
# folder under ~/Library/Application Support/Kopifund/builds and builds from
# there: the installed app keeps serving that version until the next build.
# Your data folder (~/.kopifund) is not touched.
set -euo pipefail

REF="origin/main"
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN=0 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    *) REF="$arg" ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT="$HOME/Library/Application Support/Kopifund"
BUILDS="$SUPPORT/builds"
# one Rust build cache for every version, so a rebuild takes minutes, not a fresh compile
CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/Library/Caches/Kopifund/cargo-target}"
case "$CARGO_TARGET_DIR" in /*) ;; *) CARGO_TARGET_DIR="$PWD/$CARGO_TARGET_DIR" ;; esac
export CARGO_TARGET_DIR
APP_ID="com.kopifund.desktop"
APPS="/Applications"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nbuild_macos.sh: %s\n' "$*" >&2; exit 1; }

[ "$(uname)" = Darwin ] || fail "this builds the Mac app; run it on a Mac"

say "Checking what the build needs"
for tool in git python3 node npm cargo pdftotext; do
  command -v "$tool" >/dev/null || fail "$tool is missing (AGENTS.md lists what Kopifund needs; Rust comes from rustup.rs)"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 14))' \
  || fail "Kopifund needs Python 3.14 or later; python3 is $(python3 -V 2>&1)"
[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 22 ] \
  || fail "Kopifund needs Node 22 or later; node is $(node -v)"
[ -w "$APPS" ] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; echo "/Applications is not writable here, so the app goes in ~/Applications"; }

# one build at a time: two would share the Rust output and delete each other's folders
mkdir -p "$SUPPORT"
LOCK="$SUPPORT/build.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  sleep 1   # a run that has just taken the lock may not have written its pid yet
  holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
  # the pid counts only while it is still this script: after a crash or restart
  # the number may belong to some other process, and the lock is stale
  if [ -n "$holder" ] && ps -p "$holder" -o command= 2>/dev/null | grep -q "build_macos.sh"; then
    fail "another build is running (process $holder); wait for it to finish"
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" || fail "could not take the build lock at $LOCK"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

say "Finding the version to build ($REF)"
case "$REF" in
  origin/*) git -C "$REPO" fetch --quiet origin || echo "Could not reach GitHub; building $REF as this Mac last fetched it" ;;
esac
SHA="$(git -C "$REPO" rev-parse --verify --quiet "$REF^{commit}")" || fail "no branch, tag or commit is called $REF"
SHORT="${SHA:0:7}"
echo "$REF is $SHORT: $(git -C "$REPO" log -1 --format='%s (%cd)' --date=format:'%-d %b %Y' "$SHA")"

# a folder of its own per build, so the app open now keeps its files until it quits
DIR="$BUILDS/$SHORT-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DIR"
trap 'echo; echo "The build stopped; its half-built folder is $DIR (safe to delete)." >&2' ERR
git -C "$REPO" archive "$SHA" | tar -x -C "$DIR"
printf 'commit %s\nref %s\nbuilt %s\n' "$SHA" "$REF" "$(date '+%-d %b %Y %H:%M')" > "$DIR/BUILD"

# a version from before the monorepo move has web/ and desktop/ at its root
WEB="$DIR/apps/web";   [ -d "$WEB" ] || WEB="$DIR/web"
DT="$DIR/apps/desktop"; [ -d "$DT" ] || DT="$DIR/desktop"

say "Building the web app"
(cd "$WEB" && npm ci --no-audit --no-fund --loglevel=error && npm run build)

say "Building Kopifund.app (the first build compiles Rust for a few minutes)"
(cd "$DT" && npm ci --no-audit --no-fund --loglevel=error && npm run tauri -- build --bundles app)
BUILT="$CARGO_TARGET_DIR/release/bundle/macos/Kopifund.app"
[ -d "$BUILT" ] || fail "the build finished but $BUILT is not there"
trap - ERR

if [ "$(osascript -e "application id \"$APP_ID\" is running" 2>/dev/null)" = true ]; then
  say "Quitting the Kopifund that is open"
  osascript -e "tell application id \"$APP_ID\" to quit" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    [ "$(osascript -e "application id \"$APP_ID\" is running" 2>/dev/null)" = true ] || break
    sleep 0.5
  done
  # its server still reads the old build folder: replace nothing while it runs
  [ "$(osascript -e "application id \"$APP_ID\" is running" 2>/dev/null)" != true ] \
    || fail "Kopifund did not quit; quit it yourself and run this again (the new build is ready in $DIR)"
fi

say "Installing in $APPS"
rm -rf "$APPS/Kopifund.app"
ditto "$BUILT" "$APPS/Kopifund.app"

# the installed app needs only this build's code and the web app's dist: drop the
# npm packages, and every older build (no app runs from them any more)
rm -rf "$WEB/node_modules" "$DT/node_modules"
for old in "$BUILDS"/*; do
  [ "$old" = "$DIR" ] || rm -rf "$old"
done

[ "$APPS" = "$HOME/Applications" ] || [ ! -d "$HOME/Applications/Kopifund.app" ] || \
  echo "Note: an older Kopifund.app from 'make desktop' is still in ~/Applications; delete it so Spotlight shows one."

echo
echo "Kopifund $SHORT is in $APPS/Kopifund.app, serving from $DIR"
if [ "$OPEN" = 1 ]; then open "$APPS/Kopifund.app"; fi
