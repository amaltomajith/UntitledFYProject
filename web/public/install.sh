#!/bin/sh
# CompactCode installer for macOS and Linux.
#
#   curl -fsSL https://compactcode.vercel.app/install.sh | sh
#
# What it does, in order:
#   1. installs uv from Astral (https://docs.astral.sh/uv/) into your home folder, if you do not have it;
#   2. reads the latest CompactCode release from GitHub;
#   3. downloads the wheel and SHA256SUMS, and stops unless the checksum matches;
#   4. installs CompactCode as a uv tool, with its own Python 3.12, and puts `compactcode` on your PATH.
# No sudo. Remove it with: uv tool uninstall compactcode
#
# Options (environment variables):
#   COMPACTCODE_VERSION=0.2.0       install this version instead of the latest
#   COMPACTCODE_RELEASE_URL=<url>   read release JSON from here (for testing)
#   COMPACTCODE_NO_MODIFY_PATH=1    do not add uv's tool folder to your shell profile

set -eu

REPO="amaltomajith/UntitledFYProject"
if [ -n "${COMPACTCODE_RELEASE_URL:-}" ]; then
  API="$COMPACTCODE_RELEASE_URL"
elif [ -n "${COMPACTCODE_VERSION:-}" ]; then
  API="https://api.github.com/repos/$REPO/releases/tags/v${COMPACTCODE_VERSION#v}"
else
  API="https://api.github.com/repos/$REPO/releases/latest"
fi

say() { printf '%s\n' "$*"; }
fail() {
  printf 'CompactCode was not installed: %s\n' "$*" >&2
  exit 1
}

if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL -H "User-Agent: compactcode-installer" -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q --header="User-Agent: compactcode-installer" -O "$2" "$1"; }
else
  fail "it needs curl or wget, and this system has neither."
fi

if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
  sha256() { shasum -a 256 "$1" | awk '{print $1}'; }
else
  fail "it needs sha256sum or shasum to check the download."
fi

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t compactcode)"
trap 'rm -rf "$TMP"' EXIT INT TERM

# 1. uv
find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
  for candidate in "${XDG_BIN_HOME:-}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then printf '%s\n' "$candidate"; return 0; fi
  done
  return 1
}

if UV="$(find_uv)"; then
  say "Using uv at $UV"
else
  say "Installing uv from Astral (it manages Python for CompactCode)..."
  fetch "https://astral.sh/uv/install.sh" "$TMP/uv-install.sh" || fail "could not download the uv installer."
  sh "$TMP/uv-install.sh" >/dev/null || fail "the uv installer failed. See https://docs.astral.sh/uv/getting-started/installation/"
  UV="$(find_uv)" || fail "uv was installed but cannot be found. Open a new terminal and run this again."
fi

# 2. the release
say "Finding the latest CompactCode release..."
fetch "$API" "$TMP/release.json" || fail "could not read the release from GitHub ($API)."
urls="$(tr ',' '\n' < "$TMP/release.json" | sed -n 's/.*"browser_download_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
wheel_url="$(printf '%s\n' "$urls" | grep '/compactcode-[^/]*-py3-none-any\.whl$' | head -n 1 || true)"
sums_url="$(printf '%s\n' "$urls" | grep '/SHA256SUMS$' | head -n 1 || true)"
[ -n "$wheel_url" ] || fail "the release has no CompactCode wheel."
[ -n "$sums_url" ] || fail "the release has no SHA256SUMS, so the download cannot be checked."
wheel="${wheel_url##*/}"
version="$(printf '%s\n' "$wheel" | sed 's/^compactcode-\(.*\)-py3-none-any\.whl$/\1/')"

# 3. download and check
say "Downloading CompactCode $version..."
fetch "$wheel_url" "$TMP/$wheel" || fail "could not download $wheel."
fetch "$sums_url" "$TMP/SHA256SUMS" || fail "could not download SHA256SUMS."
want="$(awk -v n="$wheel" '$2 == n || $2 == "*" n {print tolower($1)}' "$TMP/SHA256SUMS" | head -n 1)"
[ -n "$want" ] || fail "SHA256SUMS does not list $wheel."
got="$(sha256 "$TMP/$wheel" | tr 'A-F' 'a-f')"
[ "$got" = "$want" ] || fail "the download does not match its published SHA-256. Nothing was installed."
say "Checksum matches."

# 4. install
say "Installing (uv fetches Python 3.12 if it needs to)..."
install() { "$UV" tool install --force --python 3.12 "compactcode[web] @ file://$TMP/$wheel" >>"$TMP/uv.log" 2>&1; }
# An earlier install that was cut off leaves an environment uv will not reuse: remove it and try once more.
install || { "$UV" tool uninstall compactcode >>"$TMP/uv.log" 2>&1 || true; install; } || {
  cat "$TMP/uv.log" >&2
  fail "uv could not install it (output above)."
}
if [ -z "${COMPACTCODE_NO_MODIFY_PATH:-}" ]; then "$UV" tool update-shell >/dev/null 2>&1 || true; fi

say ""
say "CompactCode $version is installed."
say "Open a new terminal in your project and type: compactcode"
say "It opens in your browser. To remove it: uv tool uninstall compactcode"
