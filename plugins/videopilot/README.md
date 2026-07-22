# VideoPilot plugin

VideoPilot helps GitHub Copilot CLI and Claude Code plan and render video
projects through the public [VideoPilot](https://github.com/mbahgatTech/videopilot)
MCP server.

## Install

GitHub Copilot CLI:

```console
copilot plugin marketplace add mbahgatTech/agent-plugins
copilot plugin install videopilot@mazen-plugins
```

Claude Code:

```text
/plugin marketplace add mbahgatTech/agent-plugins
/plugin install videopilot@mazen-plugins
/reload-plugins
```

The installed plugin supplies three skills:

- `init` checks prerequisites and explains any installation side effects.
- `create-video` guides project state, approvals, previews, rendering, and
  recovery across the public 20-tool MCP surface.
- `design-slide` creates project-local RGB slide artwork in an isolated
  `uv run --script` environment.

## Plugin and engine responsibilities

This repository contains instructions, manifests, and MCP configuration. It
does not contain the Python video engine or generated media.

The MCP configuration launches:

```console
uvx --from videopilot==0.1.7 videopilot-mcp
```

`uvx` downloads the exact `videopilot==0.1.7` package from public PyPI and
runs its stdio server. The engine owns project JSON, TTS, transcription,
ffmpeg cutting, composition, previews, and exports.

For direct diagnostics outside a host:

```console
uvx --from videopilot==0.1.7 videopilot-mcp --version
uvx --from videopilot==0.1.7 videopilot doctor
```

## Prerequisites and setup consent

VideoPilot requires Python 3.10 or newer, `uv`/`uvx`, and
`ffmpeg`/`ffprobe`. Invoke the `init` skill first. Its bundled PowerShell and
Bash scripts only inspect the machine by default.

Install mode is opt-in. Before running it, review the printed commands and
approve the disclosed package-manager, network, and elevation effects. An
unattended install must include the script's explicit affirmative option.

## Speech backends

Edge TTS is the default and makes network requests when synthesizing speech.
Azure Speech is optional and reads `AZURE_SPEECH_KEY` and
`AZURE_SPEECH_REGION` from the environment.

Do not store credential values in plugin files, project state, scripts, shell
history, or issue reports.

## Generated projects

VideoPilot writes projects under `projects/<slug>/` by default. Preview and
final renders appear under:

```text
projects/<slug>/out/preview-NNN.mp4
projects/<slug>/out/final.mp4
```

Generated project state, images, audio, and video do not belong in this
marketplace repository.

## Upgrade policy

The plugin version is `0.1.0`; the engine is pinned to
`videopilot==0.1.7`. A later engine release requires its own pull request,
public compatibility review, synchronized updates to every execution pin,
full host and render validation, and a plugin version bump.
