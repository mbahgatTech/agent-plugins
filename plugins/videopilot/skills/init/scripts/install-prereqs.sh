#!/usr/bin/env bash

set -u

install_mode=0
assume_yes=0
dry_run=0
required=(uv uvx ffmpeg ffprobe)
missing=()

usage() {
  printf '%s\n' \
    "Usage: install-prereqs.sh [--install] [--yes] [--dry-run]" \
    "  default    Check prerequisites without changing the machine." \
    "  --install  Install only missing prerequisites." \
    "  --yes      Confirm a previously reviewed noninteractive install." \
    "  --dry-run  Print exact install commands without executing them."
}

while (($#)); do
  case "$1" in
    --install) install_mode=1 ;;
    --yes) assume_yes=1 ;;
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unsupported option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if ((dry_run && !install_mode)); then
  printf '%s\n' "--dry-run requires --install." >&2
  exit 2
fi

test_missing=",${VIDEOPILOT_TEST_MISSING:-},"

has_tool() {
  local name="$1"
  if [[ "$test_missing" == *",$name,"* ]]; then
    return 1
  fi
  command -v "$name" >/dev/null 2>&1
}

check_prerequisites() {
  missing=()
  local name version
  for name in "${required[@]}"; do
    if ! has_tool "$name"; then
      printf '[missing] %s\n' "$name"
      missing+=("$name")
      continue
    fi
    version="$("$name" --version 2>&1 | head -n 1)"
    printf '[found] %s - %s\n' "$name" "$version"
  done
}

contains_missing() {
  local wanted="$1" item
  for item in "${missing[@]}"; do
    [[ "$item" == "$wanted" ]] && return 0
  done
  return 1
}

detect_os() {
  if [[ -n "${VIDEOPILOT_TEST_OS:-}" ]]; then
    printf '%s\n' "$VIDEOPILOT_TEST_OS"
    return
  fi
  case "$(uname -s)" in
    Linux) printf '%s\n' linux ;;
    Darwin) printf '%s\n' macos ;;
    *) printf '%s\n' unsupported ;;
  esac
}

detect_package_manager() {
  if [[ -n "${VIDEOPILOT_TEST_PACKAGE_MANAGER:-}" ]]; then
    printf '%s\n' "$VIDEOPILOT_TEST_PACKAGE_MANAGER"
    return
  fi
  local candidate
  for candidate in brew apt-get dnf pacman; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  printf '%s\n' none
}

effective_uid() {
  if [[ -n "${VIDEOPILOT_TEST_EUID:-}" ]]; then
    printf '%s\n' "$VIDEOPILOT_TEST_EUID"
  else
    id -u
  fi
}

sudo_available() {
  [[ "${VIDEOPILOT_TEST_NO_SUDO:-0}" != 1 ]] && command -v sudo >/dev/null 2>&1
}

print_plan() {
  local command
  printf '%s\n' "The following commands may download software and modify user or system tool locations:"
  for command in "${commands[@]}"; do
    printf '  %s\n' "$command"
  done
  printf '%s\n' \
    "Package-manager commands may request elevation." \
    "After installation, uvx contacts PyPI to prewarm videopilot==0.1.7."
}

run_command() {
  local command="$1"
  printf 'Running: %s\n' "$command"
  bash -c "$command"
}

printf '%s\n' "Checking VideoPilot prerequisites (no changes are made by this check)."
check_prerequisites

if ((${#missing[@]} == 0)); then
  printf '%s\n' "All prerequisites are available."
  if ((!install_mode)); then
    exit 0
  fi
fi

os="$(detect_os)"
if [[ "$os" == unsupported ]]; then
  printf '%s\n' "Unsupported operating system." >&2
  exit 3
fi

package_manager="$(detect_package_manager)"
commands=()

if contains_missing uv || contains_missing uvx; then
  commands+=("curl -LsSf https://astral.sh/uv/install.sh | sh")
fi

if contains_missing ffmpeg || contains_missing ffprobe; then
  if [[ "$package_manager" == none ]]; then
    printf '%s\n' "No supported package manager found (brew, apt-get, dnf, or pacman)." >&2
    exit 3
  fi

  prefix=""
  if [[ "$package_manager" != brew && "$(effective_uid)" != 0 ]]; then
    if ! sudo_available; then
      printf '%s\n' "A non-root install requires sudo before any package-manager command can run." >&2
      exit 4
    fi
    prefix="sudo "
  fi

  case "$package_manager" in
    brew) commands+=("brew install ffmpeg") ;;
    apt-get)
      commands+=("${prefix}apt-get update")
      commands+=("${prefix}apt-get install -y ffmpeg curl")
      ;;
    dnf) commands+=("${prefix}dnf install -y ffmpeg curl") ;;
    pacman) commands+=("${prefix}pacman -Sy --noconfirm ffmpeg curl") ;;
    *) printf 'Unsupported package manager: %s\n' "$package_manager" >&2; exit 3 ;;
  esac
fi

if ((!install_mode)); then
  print_plan
  printf '%s\n' "Prerequisites are missing. Re-run with --install after reviewing the commands." >&2
  exit 1
fi

if ((${#commands[@]})); then
  print_plan
else
  printf '%s\n' "No package-manager commands are needed."
fi

if ((dry_run)); then
  printf '%s\n' "Dry run complete; no commands were executed."
  exit 0
fi

if ((!assume_yes)); then
  if [[ ! -t 0 || "${CI:-}" == true || "${VIDEOPILOT_NONINTERACTIVE:-0}" == 1 ]]; then
    printf '%s\n' "Noninteractive install requires --yes." >&2
    exit 2
  fi
  read -r -p "Proceed with these installation commands? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES|Yes) ;;
    *) printf '%s\n' "Installation declined." >&2; exit 2 ;;
  esac
fi

for command in "${commands[@]}"; do
  if ! run_command "$command"; then
    printf 'Installation command failed: %s\n' "$command" >&2
    exit 1
  fi
done

printf '%s\n' "Rechecking prerequisites."
test_missing=""
check_prerequisites
if ((${#missing[@]})); then
  printf 'Prerequisites remain missing: %s\n' "${missing[*]}" >&2
  exit 1
fi

printf '%s\n' "Prewarming: uvx --from videopilot==0.1.7 videopilot-mcp --version"
uvx --from videopilot==0.1.7 videopilot-mcp --version || exit $?

printf '%s\n' "Running: uvx --from videopilot==0.1.7 videopilot doctor"
uvx --from videopilot==0.1.7 videopilot doctor || exit $?

printf '%s\n' "Setup succeeded. Reload the host and call the VideoPilot MCP doctor tool."
