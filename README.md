# Mazen's Plugin Marketplace

Personal plugin marketplace for GitHub Copilot CLI and Claude Code.

The catalog is intentionally empty while the repository foundation is
established. Every catalog change is validated on Windows and Ubuntu before
it reaches `main`.

## Add the marketplace

GitHub Copilot CLI:

```console
copilot plugin marketplace add mbahgatTech/agent-plugins
copilot plugin marketplace list
```

Claude Code:

```text
/plugin marketplace add mbahgatTech/agent-plugins
```

## Repository layout

- `.claude-plugin/marketplace.json` is the canonical catalog for both hosts.
- `scripts/validate_marketplace.py` validates catalog sources and security
  boundaries without third-party Python dependencies.
- `.github/workflows/validate-marketplace.yml` runs the same checks on
  Windows and Ubuntu.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a catalog change.
