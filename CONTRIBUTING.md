# Contributing

Thank you for improving this personal marketplace.

## Propose a change

1. Create a focused branch from the latest `main`.
2. Keep marketplace names and plugin names in kebab-case.
3. Keep plugin source paths relative to the repository.
4. Run the required checks:

   ```console
   python -m json.tool .claude-plugin/marketplace.json
   python -m json.tool plugins/videopilot/plugin.json
   python -m json.tool plugins/videopilot/.claude-plugin/plugin.json
   python -m json.tool plugins/videopilot/.mcp.json
   python scripts/validate_marketplace.py
   python scripts/validate_marketplace.py --self-test
   ```

5. Open a pull request and wait for both platform checks to pass.

Plugin additions must include synchronized host manifests and public source
links. Test check-only setup behavior on Windows and Unix before changing an
installer path. Do not commit generated files, credentials, host caches, or
material that cannot be published.

## Version changes

Update the marketplace version whenever its published catalog changes. Keep
runtime dependency upgrades in a separate pull request so compatibility and
security can be reviewed independently.

For a VideoPilot engine upgrade:

1. Review the new public release's tool metadata and state schemas.
2. Update every `videopilot==0.1.7` execution pin together.
3. Revalidate all three skills, both hosts, doctor, and a disposable render.
4. Bump the plugin version and marketplace version.

Do not combine unrelated marketplace changes with an engine upgrade.
