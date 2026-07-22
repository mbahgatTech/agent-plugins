---
name: init
description: Check and prepare VideoPilot prerequisites before creating a project.
---

# Initialize VideoPilot

Use this skill when VideoPilot is new on a machine, when `doctor` reports a
missing dependency, or before the first project in a fresh shell.

## Start with a read-only check

Call the VideoPilot MCP `doctor` tool. A successful result has
`exit_code: 0` and `ok: true`. Surface its complete diagnostic message when it
fails instead of guessing at a repair.

The bundled scripts inspect `uv`, `uvx`, `ffmpeg`, and `ffprobe`. Their default
mode never installs software, invokes a package manager, or elevates:

```powershell
pwsh -File skills/init/scripts/install-prereqs.ps1
```

```bash
bash skills/init/scripts/install-prereqs.sh
```

Run these from the plugin directory, or use the full installed-plugin path.
A missing prerequisite produces a nonzero result and prints the commands that
an explicit install would use.

## Review installation side effects

Preview the exact commands without changing the machine:

```powershell
pwsh -File skills/init/scripts/install-prereqs.ps1 -Install -DryRun
```

```bash
bash skills/init/scripts/install-prereqs.sh --install --dry-run
```

The Windows path may invoke `winget` for missing tools. The Unix path may use
Homebrew, `apt-get`, `dnf`, or `pacman`; package-manager commands use `sudo`
only for a non-root user. Both paths may contact PyPI when prewarming
`videopilot==0.1.7`. Installation changes system or user tool locations and
may request elevation.

Show the printed commands to the user and obtain explicit approval before
continuing. Do not treat a general request to create a video as installation
approval.

## Install only after approval

For an attended prompt:

```powershell
pwsh -File skills/init/scripts/install-prereqs.ps1 -Install
```

```bash
bash skills/init/scripts/install-prereqs.sh --install
```

For automation where approval was already obtained:

```powershell
pwsh -File skills/init/scripts/install-prereqs.ps1 -Install -Yes
```

```bash
bash skills/init/scripts/install-prereqs.sh --install --yes
```

Noninteractive install mode fails unless the affirmative option is present.
Unsupported systems, an unavailable package manager, and a non-root Unix user
without `sudo` fail before package-manager mutation.

After installation, the scripts recheck all four commands, prewarm the exact
public engine with:

```console
uvx --from videopilot==0.1.7 videopilot-mcp --version
uvx --from videopilot==0.1.7 videopilot doctor
```

Reload or restart the host, then call the MCP `doctor` tool again. Continue to
project creation only when that MCP result succeeds.
