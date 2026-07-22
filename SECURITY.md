# Security Policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting for issues that could expose
credentials, execute an unintended command, or bypass marketplace source
validation. For non-sensitive defects, open a public issue.

Do not include credentials, access tokens, customer data, or unpublished
source material in a report.

## Publication boundaries

Marketplace entries must resolve to paths inside this public repository.
Only public HTTPS documentation and source links are accepted. The validator
rejects credential-shaped content, prohibited governance files, corporate
contact domains, unapproved URL hosts, path traversal, and manifest drift.

Plugin launchers pin reviewed package versions. The VideoPilot plugin downloads
only the public `videopilot==0.1.7` engine through `uvx`.

Credentials required by optional runtime integrations belong in the user's
environment, never in this repository, plugin files, logs, or generated
projects. Azure Speech reads `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION`;
publish only those variable names, never their values.

Setup scripts are read-only unless install mode is explicitly selected. They
must disclose every package-manager, network, and elevation effect and obtain
confirmation before making changes.

## Supported versions

Security fixes are applied to the current `main` branch. Older catalog
versions are not maintained separately.
