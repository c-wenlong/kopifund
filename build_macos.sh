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
APPS="/Applications"
# the one installer, shared with `make desktop`: it builds the shell in one Rust
# cache for every version (so a rebuild takes minutes, not a fresh compile) and
# swaps the new app in
INSTALL="$REPO/apps/desktop/install.sh"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nbuild_macos.sh: %s\n' "$*" >&2; exit 1; }

[ "$(uname)" = Darwin ] || fail "this builds the Mac app; run it on a Mac"

say "Checking what the build needs"
for tool in git python3 node npm cargo pdftotext; do
  command -v "$tool" >/dev/null || fail "$tool is missing (AGENTS.md lists what Kopifund needs; Rust comes from rustup.rs)"
done

# one build at a time, this script or `make desktop`: they share the Rust output
# and its bundle folder, and would delete or install each other's app; this
# script also deletes older build folders. The lock is the one the installer
# takes (apps/desktop/install.sh), held here for the whole run and passed down.
mkdir -p "$SUPPORT"
exec 9>"$SUPPORT/build.lock.file"
python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null \
  || fail "another build (build_macos.sh or make desktop) is running; wait for it to finish"
# the app runs python3 and pdftotext from the owner's login shell (SPEC.md, "Desktop
# app (G23)"), whose PATH can differ from this one (pyenv or conda in .zshrc), so
# they are checked the way the app finds them
APP_SHELL="${SHELL:-/bin/zsh}"
in_app_shell() { "$APP_SHELL" -l -i -c "$1" </dev/null 2>/dev/null; }
in_app_shell 'python3 -c "import sys; sys.exit(sys.version_info < (3, 14))"' \
  || fail "Kopifund needs Python 3.14 or later where the app finds it; $APP_SHELL -l -i has $(in_app_shell 'python3 -V' | tail -1 | grep . || echo 'no python3')"
in_app_shell 'command -v pdftotext >/dev/null' \
  || fail "the app's login shell ($APP_SHELL -l -i) finds no pdftotext (brew install poppler)"
[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 22 ] \
  || fail "Kopifund needs Node 22 or later; node is $(node -v)"
[ -w "$APPS" ] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; echo "/Applications is not writable here, so the app goes in ~/Applications"; }

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

# the installer builds the shell, quits the open app (its server still reads the
# old build folder) and swaps the new app in, keeping the old one until then
KOPIFUND_BUILD_LOCK_HELD=1 "$INSTALL" --quit-open "$DT" "$APPS"
trap - ERR

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
