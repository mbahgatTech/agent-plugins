# Contributing

Thank you for improving this personal marketplace.

## Propose a change

1. Create a focused branch from the latest `main`.
2. Keep marketplace names and plugin names in kebab-case.
3. Keep plugin source paths relative to the repository.
4. Run the repository checks from the repository root:

   ```console
   python -m json.tool .claude-plugin/marketplace.json
   python scripts/validate_marketplace.py
   python scripts/validate_marketplace.py --self-test
   python scripts/run_plugin_validation.py --self-test
   python scripts/run_plugin_validation.py --phase static
   ```

5. Open a pull request and wait for all four platform checks to pass.

Plugin additions normally change only `.claude-plugin/marketplace.json` and
`plugins/<name>/**`. Core validation and workflow changes are reserved for a
marketplace-wide protocol change, not for one plugin's dependencies or
runtime behavior.

Each plugin must include synchronized `plugin.json` and
`.claude-plugin/plugin.json` manifests, public source links, and this
validation directory:

```text
plugins/<name>/validation/
├── validate.py
├── ci-ubuntu.sh       optional
└── ci-windows.ps1     optional
```

`validate.py` is required and must support both normal validation and
`--self-test`. Keep its dependencies in the plugin payload or use the Python
standard library. It runs twice in each static job:

```console
python plugins/<name>/validation/validate.py
python plugins/<name>/validation/validate.py --self-test
```

The platform hooks are optional. They run only on their matching disposable
GitHub-hosted runner and may install plugin-specific CI prerequisites there.
Exercise them through generic discovery rather than invoking a payload path
from shared workflow code:

```console
python scripts/run_plugin_validation.py --phase runtime --platform ubuntu
python scripts/run_plugin_validation.py --phase runtime --platform windows
```

Hook discovery resolves every catalog source and hook beneath its owning
plugin directory. Escaping paths and symlinks are rejected. Hooks run with
the repository root as the working directory and receive absolute
`MARKETPLACE_REPOSITORY_ROOT` and `MARKETPLACE_PLUGIN_ROOT` environment
variables.

Every hook returns zero on success and nonzero on failure. The discovery
runner reports the plugin name, hook name, and child exit code for a failed
hook. A missing `validate.py` fails static validation; a missing platform
hook is logged and skipped.

Keep plugin-specific validation rules, dependencies, platform setup, and
runtime checks under `plugins/<name>/validation/`. Do not commit generated
files, credentials, host caches, or material that cannot be published.

## Version changes

Update the marketplace version whenever its published catalog changes. A
plugin release updates its own version, execution pins, compatibility
evidence, and marketplace entry together. Keep runtime dependency upgrades
in a focused pull request so compatibility and security can be reviewed
independently.
