#!/usr/bin/env bash

set -euo pipefail

if [[ "${CI:-}" != "true" ]]; then
  printf '%s\n' "This hook installs prerequisites only on a disposable CI runner." >&2
  exit 2
fi

plugin_root="${MARKETPLACE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
installer="$plugin_root/skills/init/scripts/install-prereqs.sh"

expect_exit() {
  local expected="$1"
  local label="$2"
  shift 2
  set +e
  "$@"
  local actual=$?
  set -e
  if [[ "$actual" -ne "$expected" ]]; then
    printf '%s: expected exit code %s, got %s\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
  printf '%s: observed expected exit code %s\n' "$label" "$expected"
}

printf '%s\n' "Installing VideoPilot CI prerequisites."
sudo apt-get update
sudo apt-get install -y curl ffmpeg
if ! command -v uvx >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

printf '%s\n' "Checking prerequisite script safe default."
default_output="$(bash "$installer")"
printf '%s\n' "$default_output"
if grep -q '^Running:' <<<"$default_output"; then
  printf '%s\n' "The default prerequisite check attempted a mutation." >&2
  exit 1
fi

printf '%s\n' "Checking root dry-run setup."
set +e
root_output="$(
  VIDEOPILOT_TEST_MISSING=uv,uvx,ffmpeg,ffprobe \
  VIDEOPILOT_TEST_OS=linux \
  VIDEOPILOT_TEST_PACKAGE_MANAGER=apt-get \
  VIDEOPILOT_TEST_EUID=0 \
  bash "$installer" --install --dry-run
)"
root_code=$?
set -e
printf '%s\n' "$root_output"
if [[ "$root_code" -ne 0 ]]; then
  printf 'root dry-run: expected exit code 0, got %s\n' "$root_code" >&2
  exit 1
fi
grep -q 'apt-get install -y ffmpeg curl' <<<"$root_output"
if grep -q 'sudo apt-get' <<<"$root_output"; then
  printf '%s\n' "Root dry-run must not use sudo." >&2
  exit 1
fi

printf '%s\n' "Checking non-root setup without sudo."
expect_exit 4 "non-root without sudo" env \
  VIDEOPILOT_TEST_MISSING=uv,uvx,ffmpeg,ffprobe \
  VIDEOPILOT_TEST_OS=linux \
  VIDEOPILOT_TEST_PACKAGE_MANAGER=apt-get \
  VIDEOPILOT_TEST_EUID=1000 \
  VIDEOPILOT_TEST_NO_SUDO=1 \
  bash "$installer" --install --dry-run

printf '%s\n' "Checking unsupported Unix setup."
expect_exit 3 "unsupported operating system" env \
  VIDEOPILOT_TEST_MISSING=uv,uvx,ffmpeg,ffprobe \
  VIDEOPILOT_TEST_OS=unsupported \
  bash "$installer"

printf '%s\n' "Checking noninteractive setup without approval."
expect_exit 2 "noninteractive without approval" env \
  VIDEOPILOT_TEST_MISSING=uv \
  VIDEOPILOT_TEST_OS=linux \
  VIDEOPILOT_TEST_PACKAGE_MANAGER=none \
  VIDEOPILOT_NONINTERACTIVE=1 \
  bash "$installer" --install

printf '%s\n' "Prewarming: uvx --from videopilot==0.1.7 videopilot-mcp --version"
uvx --from videopilot==0.1.7 videopilot-mcp --version

printf '%s\n' "Running: uvx --from videopilot==0.1.7 videopilot doctor"
uvx --from videopilot==0.1.7 videopilot doctor
