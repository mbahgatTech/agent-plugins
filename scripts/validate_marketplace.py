#!/usr/bin/env python3
"""Validate marketplace structure, plugin provenance, and safe public content."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse


MARKETPLACE_PATH = Path(".claude-plugin/marketplace.json")
VALID_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VALID_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
ALLOWED_URL_HOSTS = {
    "astral.sh",
    "code.claude.com",
    "docs.astral.sh",
    "docs.github.com",
    "github.com",
    "json.schemastore.org",
    "pypi.org",
    "raw.githubusercontent.com",
}
PROHIBITED_BASENAMES = {
    "agency.json",
    "marketplace-config.json",
}
PROHIBITED_EMAIL_DOMAINS = {
    "microsoft.com",
}
PROHIBITED_BRANDING = re.compile(r"\bagency\b", re.IGNORECASE)
CREDENTIAL_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "assigned secret": re.compile(
        r"(?im)^\s*(?:AZURE_SPEECH_KEY|AWS_SECRET_ACCESS_KEY|API_KEY|TOKEN)"
        r"\s*=\s*[^\s#][^\r\n]*$"
    ),
}
UNPINNED_ENGINE_PATTERNS = {
    "unpinned uvx engine execution": re.compile(
        r"\buvx\s+--from\s+videopilot(?:\s|$)", re.IGNORECASE
    ),
    "unpinned pip engine installation": re.compile(
        r"\b(?:python\s+-m\s+pip|pip|uv\s+pip)\s+install\s+videopilot(?:\s|$)",
        re.IGNORECASE,
    ),
}
EXPECTED_SKILLS = [
    "./skills/init",
    "./skills/create-video",
    "./skills/design-slide",
]
EXPECTED_MCP_ARGS = [
    "--from",
    "videopilot==0.1.7",
    "videopilot-mcp",
]
REQUIRED_PLUGIN_PATHS = [
    "README.md",
    "skills/init/SKILL.md",
    "skills/init/scripts/install-prereqs.ps1",
    "skills/init/scripts/install-prereqs.sh",
    "skills/create-video/SKILL.md",
    "skills/design-slide/SKILL.md",
]


class MarketplaceValidator:
    """Collect deterministic validation errors without stopping at the first one."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.errors: list[str] = []

    def validate(self) -> list[str]:
        """Validate the complete repository and return every actionable error."""
        marketplace = self._read_json(self.root / MARKETPLACE_PATH)
        if marketplace is not None:
            self._validate_marketplace(marketplace)
        self._scan_repository()
        return self.errors

    def _error(self, path: Path | str, message: str) -> None:
        try:
            display = Path(path).resolve().relative_to(self.root)
        except (OSError, ValueError):
            display = path
        self.errors.append(f"{display}: {message}")

    def _read_json(self, path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._error(path, "required JSON file is missing")
        except UnicodeDecodeError:
            self._error(path, "must be UTF-8 text")
        except json.JSONDecodeError as exc:
            self._error(path, f"malformed JSON at line {exc.lineno}, column {exc.colno}")
        return None

    def _validate_marketplace(self, data: Any) -> None:
        path = self.root / MARKETPLACE_PATH
        if not isinstance(data, dict):
            self._error(path, "catalog root must be a JSON object")
            return

        self._require_name(path, data.get("name"), "marketplace name")
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            self._error(path, "metadata must be an object")
        else:
            self._require_text(path, metadata.get("description"), "metadata.description")
            self._require_version(path, metadata.get("version"), "metadata.version")

        owner = data.get("owner")
        if not isinstance(owner, dict):
            self._error(path, "owner must be an object")
        else:
            self._require_text(path, owner.get("name"), "owner.name")
            self._require_email(path, owner.get("email"), "owner.email")

        plugins = data.get("plugins")
        if not isinstance(plugins, list):
            self._error(path, "plugins must be an array")
            return

        seen: set[str] = set()
        for index, plugin in enumerate(plugins):
            label = f"plugins[{index}]"
            if not isinstance(plugin, dict):
                self._error(path, f"{label} must be an object")
                continue
            name = plugin.get("name")
            self._require_name(path, name, f"{label}.name")
            if isinstance(name, str):
                folded = name.casefold()
                if folded in seen:
                    self._error(path, f"{label}.name duplicates {name!r} case-insensitively")
                seen.add(folded)
            source_root = self._resolve_source(path, plugin.get("source"), label)
            if source_root is not None:
                self._validate_plugin(path, plugin, source_root, label)

    def _resolve_source(
        self, catalog_path: Path, source: Any, label: str
    ) -> Path | None:
        if not isinstance(source, str) or not source.strip():
            self._error(catalog_path, f"{label}.source must be a nonempty relative path")
            return None

        posix = PurePosixPath(source.replace("\\", "/"))
        windows = PureWindowsPath(source)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            self._error(catalog_path, f"{label}.source must be relative")
            return None
        if ".." in posix.parts:
            self._error(catalog_path, f"{label}.source must not contain '..' traversal")
            return None

        candidate = (self.root / Path(*posix.parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            self._error(catalog_path, f"{label}.source resolves outside the repository")
            return None
        if not candidate.is_dir():
            self._error(catalog_path, f"{label}.source directory does not exist")
            return None
        return candidate

    def _validate_plugin(
        self, catalog_path: Path, entry: dict[str, Any], plugin_root: Path, label: str
    ) -> None:
        root_manifest = plugin_root / "plugin.json"
        claude_manifest = plugin_root / ".claude-plugin/plugin.json"
        if not root_manifest.is_file() or not claude_manifest.is_file():
            self._error(plugin_root, "both plugin manifests are required")
            return

        root_bytes = root_manifest.read_bytes()
        claude_bytes = claude_manifest.read_bytes()
        if root_bytes != claude_bytes:
            self._error(plugin_root, "plugin manifests must be byte-for-byte identical")

        manifest = self._read_json(root_manifest)
        if not isinstance(manifest, dict):
            return

        for field in (
            "name",
            "version",
            "description",
            "author",
            "homepage",
            "repository",
            "license",
            "keywords",
            "category",
        ):
            if entry.get(field) != manifest.get(field):
                self._error(
                    catalog_path,
                    f"{label}.{field} must match the plugin manifest",
                )

        self._require_name(root_manifest, manifest.get("name"), "name")
        self._require_version(root_manifest, manifest.get("version"), "version")
        self._require_text(root_manifest, manifest.get("description"), "description")
        if manifest.get("license") != "MIT":
            self._error(root_manifest, "license must be MIT")
        if manifest.get("category") != "media-tools":
            self._error(root_manifest, "category must be media-tools")

        author = manifest.get("author")
        if not isinstance(author, dict):
            self._error(root_manifest, "author must be an object")
        else:
            self._require_text(root_manifest, author.get("name"), "author.name")
            self._require_email(root_manifest, author.get("email"), "author.email")

        for field in ("homepage", "repository"):
            value = manifest.get(field)
            self._require_text(root_manifest, value, field)
            if isinstance(value, str):
                self._validate_url(root_manifest, value)

        if manifest.get("skills") != EXPECTED_SKILLS:
            self._error(root_manifest, f"skills must be exactly {EXPECTED_SKILLS!r}")
        if manifest.get("mcpServers") != "./.mcp.json":
            self._error(root_manifest, "mcpServers must be './.mcp.json'")

        for relative in REQUIRED_PLUGIN_PATHS:
            required_path = plugin_root / relative
            if not required_path.is_file():
                self._error(required_path, "required plugin file is missing")

        for skill_path in EXPECTED_SKILLS:
            skill_root = plugin_root / skill_path.removeprefix("./")
            skill_file = skill_root / "SKILL.md"
            if skill_file.is_file():
                self._validate_skill(skill_file, skill_root.name)

        self._validate_mcp(plugin_root / ".mcp.json")

    def _validate_skill(self, path: Path, expected_name: str) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self._error(path, "skill must be UTF-8 text")
            return
        frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
        if frontmatter is None:
            self._error(path, "skill must start with YAML frontmatter")
            return
        name = re.search(r"(?m)^name:\s*([a-z0-9-]+)\s*$", frontmatter.group(1))
        description = re.search(r"(?m)^description:\s*(\S.*)$", frontmatter.group(1))
        if name is None or name.group(1) != expected_name:
            self._error(path, f"skill name must be {expected_name!r}")
        if description is None:
            self._error(path, "skill description must be nonempty")

    def _validate_mcp(self, path: Path) -> None:
        data = self._read_json(path)
        if not isinstance(data, dict):
            return
        if set(data) != {"mcpServers"} or not isinstance(data["mcpServers"], dict):
            self._error(path, "root must contain only an mcpServers object")
            return
        servers = data["mcpServers"]
        if set(servers) != {"videopilot"} or not isinstance(servers["videopilot"], dict):
            self._error(path, "mcpServers must define exactly the videopilot server")
            return
        server = servers["videopilot"]
        if set(server) != {"type", "command", "args"}:
            self._error(path, "videopilot server must contain only type, command, and args")
        if server.get("type") != "stdio":
            self._error(path, "videopilot server type must be stdio")
        if server.get("command") != "uvx":
            self._error(path, "videopilot server command must be uvx")
        if server.get("args") != EXPECTED_MCP_ARGS:
            self._error(path, f"videopilot server args must be exactly {EXPECTED_MCP_ARGS!r}")

    def _scan_repository(self) -> None:
        for path in self.root.rglob("*"):
            if ".git" in path.parts or not (path.is_file() or path.is_symlink()):
                continue
            if path.name.casefold() in PROHIBITED_BASENAMES:
                self._error(path, "prohibited governance file")
            if path.is_symlink():
                try:
                    path.resolve(strict=True).relative_to(self.root)
                except (FileNotFoundError, ValueError):
                    self._error(path, "symlink escapes the repository or is broken")
                    continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            self._validate_public_text(path, text)

    def _validate_public_text(self, path: Path, text: str) -> None:
        if PROHIBITED_BRANDING.search(text):
            self._error(path, "prohibited branding is present")
        for email in EMAIL_PATTERN.findall(text):
            self._require_email(path, email, "email address")
        for match in URL_PATTERN.findall(text):
            self._validate_url(path, match.rstrip(".,;:"))
        for label, pattern in CREDENTIAL_PATTERNS.items():
            if pattern.search(text):
                self._error(path, f"possible {label} material is present")
        for label, pattern in UNPINNED_ENGINE_PATTERNS.items():
            if pattern.search(text):
                self._error(path, f"{label} is present")

    def _validate_url(self, path: Path, value: str) -> None:
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or host not in ALLOWED_URL_HOSTS:
            self._error(path, f"URL is not on the public allowlist: {value}")
        if any(part.casefold() == "private" for part in PurePosixPath(parsed.path).parts):
            self._error(path, f"private URL path is prohibited: {value}")
        if parsed.username or parsed.password:
            self._error(path, f"credentials in URLs are prohibited: {value}")

    def _require_name(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, str) or not VALID_NAME.fullmatch(value):
            self._error(path, f"{field} must be kebab-case")

    def _require_version(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, str) or not VALID_VERSION.fullmatch(value):
            self._error(path, f"{field} must be a semantic version")

    def _require_text(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            self._error(path, f"{field} must be a nonempty string")

    def _require_email(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, str) or EMAIL_PATTERN.fullmatch(value) is None:
            self._error(path, f"{field} must be a valid email address")
            return
        domain = value.rsplit("@", 1)[1].casefold()
        if domain in PROHIBITED_EMAIL_DOMAINS:
            self._error(path, f"{field} uses a prohibited corporate domain")


def validate_root(root: Path) -> list[str]:
    """Run repository validation for command-line and fixture callers."""
    return MarketplaceValidator(root).validate()


def write_json(path: Path, value: Any) -> None:
    """Write deterministic UTF-8 JSON used by disposable validator fixtures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def valid_marketplace() -> dict[str, Any]:
    """Return the canonical empty catalog fixture."""
    return {
        "$schema": "https://json.schemastore.org/claude-code-marketplace.json",
        "name": "mazen-plugins",
        "metadata": {
            "description": "Personal plugins maintained by Mazen Bahgat.",
            "version": "0.1.0",
        },
        "owner": {
            "name": "Mazen Bahgat",
            "email": "mazenbahgat@outlook.com",
        },
        "plugins": [],
    }


def add_valid_plugin(root: Path, marketplace: dict[str, Any]) -> None:
    """Populate a fixture with one valid synchronized plugin."""
    description = "Create and edit videos with the public VideoPilot engine."
    entry = {
        "name": "videopilot",
        "source": "./plugins/videopilot",
        "description": description,
        "version": "0.1.0",
        "author": {
            "name": "Mazen Bahgat",
            "email": "mazenbahgat@outlook.com",
        },
        "homepage": "https://github.com/mbahgatTech/videopilot",
        "repository": "https://github.com/mbahgatTech/videopilot",
        "license": "MIT",
        "keywords": ["video", "voiceover", "ffmpeg", "mcp"],
        "category": "media-tools",
    }
    manifest = {
        "name": "videopilot",
        "version": "0.1.0",
        "description": description,
        "author": {
            "name": "Mazen Bahgat",
            "email": "mazenbahgat@outlook.com",
        },
        "homepage": "https://github.com/mbahgatTech/videopilot",
        "repository": "https://github.com/mbahgatTech/videopilot",
        "license": "MIT",
        "keywords": ["video", "voiceover", "ffmpeg", "mcp"],
        "category": "media-tools",
        "skills": EXPECTED_SKILLS,
        "mcpServers": "./.mcp.json",
    }
    marketplace["metadata"]["version"] = "0.2.0"
    marketplace["plugins"] = [entry]
    plugin_root = root / "plugins/videopilot"
    write_json(plugin_root / "plugin.json", manifest)
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        plugin_root / "plugin.json",
        plugin_root / ".claude-plugin/plugin.json",
    )
    write_json(
        plugin_root / ".mcp.json",
        {
            "mcpServers": {
                "videopilot": {
                    "type": "stdio",
                    "command": "uvx",
                    "args": EXPECTED_MCP_ARGS,
                }
            }
        },
    )
    write_json(plugin_root / "README.md", {})
    for skill in ("init", "create-video", "design-slide"):
        path = plugin_root / "skills" / skill / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {skill}\ndescription: Fixture skill.\n---\n\n# {skill}\n",
            encoding="utf-8",
        )
    scripts = plugin_root / "skills/init/scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install-prereqs.ps1").write_text(
        "Write-Output 'fixture'\n", encoding="utf-8"
    )
    (scripts / "install-prereqs.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'fixture\\n'\n", encoding="utf-8"
    )


def fixture_errors(
    mutate: Any | None = None, *, plugin: bool = False
) -> list[str]:
    """Build a disposable fixture, optionally mutate it, and return errors."""
    with tempfile.TemporaryDirectory(prefix="marketplace-validator-") as directory:
        root = Path(directory)
        marketplace = valid_marketplace()
        if plugin:
            add_valid_plugin(root, marketplace)
        write_json(root / MARKETPLACE_PATH, marketplace)
        if mutate is not None:
            mutate(root, marketplace)
            write_json(root / MARKETPLACE_PATH, marketplace)
        return validate_root(root)


def run_self_tests() -> int:
    """Exercise positive and negative invariants without external test packages."""
    failures: list[str] = []
    cases = 0

    def expect_valid(name: str, *, plugin: bool = False) -> None:
        nonlocal cases
        cases += 1
        errors = fixture_errors(plugin=plugin)
        if errors:
            failures.append(f"{name}: expected valid, got {errors}")

    def expect_invalid(
        name: str,
        needle: str,
        mutate: Any,
        *,
        plugin: bool = False,
    ) -> None:
        nonlocal cases
        cases += 1
        errors = fixture_errors(mutate, plugin=plugin)
        if not any(needle in error for error in errors):
            failures.append(f"{name}: expected {needle!r}, got {errors}")

    expect_valid("empty catalog")
    expect_valid("one-plugin catalog", plugin=True)
    expect_invalid(
        "invalid name",
        "kebab-case",
        lambda _root, data: data.update(name="Bad_Name"),
    )
    expect_invalid(
        "duplicate names",
        "duplicates",
        lambda _root, data: data["plugins"].append(
            {**data["plugins"][0], "name": "VideoPilot"}
        ),
        plugin=True,
    )
    expect_invalid(
        "missing source",
        "does not exist",
        lambda _root, data: data["plugins"][0].update(source="./plugins/missing"),
        plugin=True,
    )
    expect_invalid(
        "absolute source",
        "must be relative",
        lambda _root, data: data["plugins"][0].update(source="C:\\outside"),
        plugin=True,
    )
    expect_invalid(
        "traversal source",
        "must not contain '..'",
        lambda _root, data: data["plugins"][0].update(source="../outside"),
        plugin=True,
    )

    def drift_manifest(root: Path, _data: dict[str, Any]) -> None:
        path = root / "plugins/videopilot/.claude-plugin/plugin.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    expect_invalid(
        "manifest drift",
        "byte-for-byte identical",
        drift_manifest,
        plugin=True,
    )

    def remove_manifest(root: Path, _data: dict[str, Any]) -> None:
        (root / "plugins/videopilot/.claude-plugin/plugin.json").unlink()

    expect_invalid(
        "missing dual manifest",
        "both plugin manifests are required",
        remove_manifest,
        plugin=True,
    )
    expect_invalid(
        "metadata mismatch",
        "must match the plugin manifest",
        lambda _root, data: data["plugins"][0].update(version="9.9.9"),
        plugin=True,
    )

    def replace_mcp(root: Path, value: Any) -> None:
        write_json(root / "plugins/videopilot/.mcp.json", value)

    expect_invalid(
        "unpinned MCP",
        "server args must be exactly",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "videopilot": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--from", "videopilot", "videopilot-mcp"],
                    }
                }
            },
        ),
        plugin=True,
    )
    expect_invalid(
        "wrong MCP command",
        "server command must be uvx",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "videopilot": {
                        "type": "stdio",
                        "command": "python",
                        "args": EXPECTED_MCP_ARGS,
                    }
                }
            },
        ),
        plugin=True,
    )

    def malformed_mcp(root: Path, _data: dict[str, Any]) -> None:
        (root / "plugins/videopilot/.mcp.json").write_text("{", encoding="utf-8")

    expect_invalid("malformed MCP", "malformed JSON", malformed_mcp, plugin=True)
    expect_invalid(
        "unexpected MCP server",
        "exactly the videopilot server",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "videopilot": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": EXPECTED_MCP_ARGS,
                    },
                    "extra": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": [],
                    },
                }
            },
        ),
        plugin=True,
    )

    def add_text(root: Path, text: str, name: str = "README.md") -> None:
        (root / name).write_text(text, encoding="utf-8")

    expect_invalid(
        "prohibited file",
        "prohibited governance file",
        lambda root, _data: add_text(root, "{}", "agency.json"),
    )
    expect_invalid(
        "corporate email",
        "prohibited corporate domain",
        lambda root, _data: add_text(root, "owner@microsoft.com"),
    )
    expect_invalid(
        "credential",
        "possible GitHub token",
        lambda root, _data: add_text(root, "ghp_123456789012345678901234567890123456"),
    )
    expect_invalid(
        "private URL",
        "not on the public allowlist",
        lambda root, _data: add_text(root, "https://internal.example.test/resource"),
    )
    expect_invalid(
        "unpinned execution",
        "unpinned uvx engine execution",
        lambda root, _data: add_text(
            root,
            "uvx --from videopilot videopilot-mcp",
        ),
    )

    if failures:
        for failure in failures:
            print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"Validator self-tests passed: {cases} cases")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the validator's small, stable command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root to validate (default: current directory)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run disposable positive and negative fixtures",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run fixture tests or validate one repository root."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return run_self_tests()
    if not args.root.exists() or not args.root.is_dir():
        print(f"{args.root}: root must be a readable directory", file=sys.stderr)
        return 2
    errors = validate_root(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Marketplace validation passed: {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
