# Mazen's Plugin Marketplace

Personal plugin marketplace for GitHub Copilot CLI and Claude Code.

The marketplace currently publishes VideoPilot, a dual-host plugin that
provides setup and video-authoring skills plus an MCP connection to the public
[VideoPilot engine](https://github.com/mbahgatTech/videopilot).

## Install VideoPilot

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

The plugin contains instructions and MCP configuration. It does not vendor the
Python engine. Its MCP server launches:

```console
uvx --from videopilot==0.1.7 videopilot-mcp
```

`uvx` downloads that exact public PyPI release into its managed cache.

## Prerequisites

- Python 3.10 or newer
- `uv` and `uvx`
- `ffmpeg` and `ffprobe`

Run the plugin's `init` skill before creating a project. Its scripts are
check-only by default. Installation mode prints the exact package-manager and
elevation effects and requires explicit confirmation.

Edge TTS is the default voice backend and uses the network. Azure Speech is
optional and reads `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` from the
user's environment. Never place credential values in this repository or a
VideoPilot project.

## Repository layout

- `.claude-plugin/marketplace.json` is the canonical catalog for both hosts.
- `plugins/videopilot/` contains byte-identical host manifests, three skills,
  and the pinned MCP launcher.
- `scripts/validate_marketplace.py` validates catalog sources and security
  boundaries without third-party Python dependencies.
- `.github/workflows/validate-marketplace.yml` runs the same checks on
  Windows and Ubuntu.

## Version policy

The marketplace plugin is `0.1.0`, and its engine execution is pinned to
`videopilot==0.1.7`. An engine upgrade must use a separate pull request with a
compatibility review, update every execution pin, and bump the plugin version.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a catalog change.
