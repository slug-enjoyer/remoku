#!/bin/sh
# remoku installer
#
#   curl -fsSL https://raw.githubusercontent.com/slug-enjoyer/remoku/main/install.sh | sh
#
# Installs the app into ~/.local (override with --prefix), plus a `remoku`
# launcher and a desktop entry. Nothing is installed system-wide and no root
# is needed, apart from installing the GTK/Python dependencies yourself when
# they are missing.
#
# Options:
#   --prefix DIR   install under DIR (default: ~/.local)
#   --ref REF      git ref to install (default: main)
#   --uninstall    remove a previous install
#   --help

set -eu

OWNER="slug-enjoyer"
REPO="remoku"
REF="main"
PREFIX="${HOME}/.local"
ACTION="install"

usage() {
    cat <<'EOF'
remoku installer

  curl -fsSL https://raw.githubusercontent.com/slug-enjoyer/remoku/main/install.sh | sh

Installs the app into ~/.local (override with --prefix), plus a `remoku`
launcher and a desktop entry. Nothing is installed system-wide and no root
is needed, apart from installing the GTK/Python dependencies yourself when
they are missing.

Options:
  --prefix DIR   install under DIR (default: ~/.local)
  --ref REF      git ref to install (default: main)
  --uninstall    remove a previous install
  --help
EOF
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            [ $# -ge 2 ] || { echo "error: --prefix needs a value" >&2; exit 2; }
            PREFIX="$2"; shift 2 ;;
        --prefix=*) PREFIX="${1#*=}"; shift ;;
        --ref)
            [ $# -ge 2 ] || { echo "error: --ref needs a value" >&2; exit 2; }
            REF="$2"; shift 2 ;;
        --ref=*) REF="${1#*=}"; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        -h|--help) usage ;;
        *) echo "error: unknown option: $1" >&2; exit 2 ;;
    esac
done

case "$PREFIX" in
    /*) ;;
    *) echo "error: --prefix must be an absolute path" >&2; exit 2 ;;
esac

SHAREDIR="$PREFIX/share/$REPO"
BINDIR="$PREFIX/bin"
APPDIR="$PREFIX/share/applications"
ICONDIR="$PREFIX/share/icons/hicolor/scalable/apps"
PNGICONDIR="$PREFIX/share/icons/hicolor/256x256/apps"
LAUNCHER="$BINDIR/$REPO"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 &&
        update-desktop-database "$APPDIR" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 &&
        gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true
}

uninstall() {
    say "Removing remoku from $PREFIX..."
    rm -rf "$SHAREDIR"
    rm -f "$LAUNCHER" "$APPDIR/$REPO.desktop"
    rm -f "$ICONDIR/$REPO.svg" "$PNGICONDIR/$REPO.png"
    refresh_caches
    say "Done. (Your settings in ~/.config/remoku and ~/.cache/remoku are kept.)"
    exit 0
}

[ "$ACTION" = "uninstall" ] && uninstall

# -- requirements ---------------------------------------------------------

need() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required but not installed"
}

need python3
need tar
if command -v curl >/dev/null 2>&1; then
    download() { curl -fsSL "$1"; }
elif command -v wget >/dev/null 2>&1; then
    download() { wget -qO- "$1"; }
else
    fail "curl or wget is required to download remoku"
fi

deps_hint() {
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "${ID:-} ${ID_LIKE:-}" in
            *arch*)   echo "  sudo pacman -S python-gobject gtk3 python-requests" ;;
            *debian*|*ubuntu*)
                      echo "  sudo apt install python3-gi gir1.2-gtk-3.0 python3-requests" ;;
            *fedora*) echo "  sudo dnf install python3-gobject gtk3 python3-requests" ;;
            *suse*)   echo "  sudo zypper install python3-gobject gtk3 python3-requests" ;;
            *)        echo "  install PyGObject (GTK3), GTK 3 and the Python requests module" ;;
        esac
    else
        echo "  install PyGObject (GTK3), GTK 3 and the Python requests module"
    fi
}

if ! python3 -c '
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import requests
' >/dev/null 2>&1; then
    say "remoku needs GTK 3, PyGObject and Python requests."
    say "Install them first, for example:"
    say ""
    deps_hint
    say ""
    exit 1
fi

# -- install --------------------------------------------------------------

say "Installing remoku $REF into $SHAREDIR"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT INT TERM

download "https://codeload.github.com/$OWNER/$REPO/tar.gz/$REF" | tar -xz -C "$tmpdir"
srcdir=$(find "$tmpdir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
[ -n "$srcdir" ] || fail "download failed (is the ref '$REF' correct?)"
[ -f "$srcdir/bin/$REPO" ] || fail "downloaded archive does not look like remoku"

rm -rf "$SHAREDIR"
mkdir -p "$SHAREDIR"
cp -R "$srcdir"/. "$SHAREDIR"/
chmod +x "$SHAREDIR/bin/$REPO"

mkdir -p "$BINDIR" "$APPDIR" "$ICONDIR" "$PNGICONDIR"
ln -sfn "$SHAREDIR/bin/$REPO" "$LAUNCHER"
sed "s|@EXEC@|$LAUNCHER|" "$SHAREDIR/data/$REPO.desktop.in" \
    > "$APPDIR/$REPO.desktop"
cp "$SHAREDIR/remoku/assets/$REPO.svg" "$ICONDIR/$REPO.svg"
if [ -f "$SHAREDIR/remoku/assets/$REPO-256.png" ]; then
    cp "$SHAREDIR/remoku/assets/$REPO-256.png" "$PNGICONDIR/$REPO.png"
fi
refresh_caches

say ""
say "Installed. Run it with: $REPO"
case ":${PATH}:" in
    *":$BINDIR:"*) ;;
    *)
        say ""
        say "Note: $BINDIR is not in your PATH. Add this to your shell profile:"
        say "  export PATH=\"$BINDIR:\$PATH\""
        ;;
esac
say ""
say "Uninstall any time with:"
say "  curl -fsSL https://raw.githubusercontent.com/$OWNER/$REPO/main/install.sh | sh -s -- --uninstall"
