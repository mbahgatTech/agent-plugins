#!/usr/bin/env python3
"""Validate marketplace structure, plugin provenance, and safe public content."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable
from urllib.parse import unquote, urlparse


MARKETPLACE_PATH = Path(".claude-plugin/marketplace.json")
VALID_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VALID_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
CATALOG_PLUGIN_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "category",
)
PROHIBITED_BASENAMES = {
    "agency.json",
    "marketplace-config.json",
}
PROHIBITED_EMAIL_DOMAINS = {
    "microsoft.com",
}
PROHIBITED_BRANDING = re.compile(r"\bagency\b", re.IGNORECASE)
PROHIBITED_HOST_SUFFIXES = (
    ".example",
    ".home.arpa",
    ".internal",
    ".invalid",
    ".local",
    ".localhost",
    ".onion",
    ".test",
)
PROHIBITED_HOSTS = {
    "example",
    "home.arpa",
    "internal",
    "localhost",
    "onion",
    "test",
}
CREDENTIAL_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "assigned secret": re.compile(
        r"(?im)^\s*(?:AZURE_SPEECH_KEY|AWS_SECRET_ACCESS_KEY|API_KEY|TOKEN)"
        r"\s*=\s*[^\s#][^\r\n]*$"
    ),
}
IGNORED_DIRECTORY_NAMES = {
    ".claude",
    ".copilot",
    ".git",
    ".plans",
    ".venv",
    "__pycache__",
    "out",
    "projects",
}
NUMERIC_HOST_PATTERN = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+))*$"
)


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

        schema = data.get("$schema")
        self._require_text(path, schema, "$schema")
        if isinstance(schema, str):
            self._validate_url(path, schema)
        self._require_name(path, data.get("name"), "marketplace name")
        self._validate_metadata(path, data.get("metadata"))
        self._validate_author(path, data.get("owner"), "owner")

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
            self._validate_catalog_entry(path, plugin, label)
            source_root = self._resolve_relative(
                path,
                plugin.get("source"),
                self.root,
                self.root,
                f"{label}.source",
                expected="directory",
            )
            if source_root is not None:
                self._validate_plugin(path, plugin, source_root, label)

    def _validate_metadata(self, path: Path, value: Any) -> None:
        if not isinstance(value, dict):
            self._error(path, "metadata must be an object")
            return
        self._require_text(path, value.get("description"), "metadata.description")
        self._require_version(path, value.get("version"), "metadata.version")

    def _validate_catalog_entry(
        self, path: Path, entry: dict[str, Any], label: str
    ) -> None:
        self._require_version(path, entry.get("version"), f"{label}.version")
        self._require_text(path, entry.get("description"), f"{label}.description")
        self._validate_author(path, entry.get("author"), f"{label}.author")
        for field in ("homepage", "repository"):
            value = entry.get(field)
            self._require_text(path, value, f"{label}.{field}")
            if isinstance(value, str):
                self._validate_url(path, value)
        self._require_text(path, entry.get("license"), f"{label}.license")
        self._validate_keywords(path, entry.get("keywords"), f"{label}.keywords")
        self._require_name(path, entry.get("category"), f"{label}.category")

    def _validate_author(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, dict):
            self._error(path, f"{field} must be an object")
            return
        self._require_text(path, value.get("name"), f"{field}.name")
        self._require_email(path, value.get("email"), f"{field}.email")

    def _validate_keywords(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, list):
            self._error(path, f"{field} must be an array")
            return
        seen: set[str] = set()
        for index, keyword in enumerate(value):
            if not isinstance(keyword, str) or not keyword.strip():
                self._error(path, f"{field}[{index}] must be a nonempty string")
                continue
            folded = keyword.casefold()
            if folded in seen:
                self._error(path, f"{field}[{index}] duplicates {keyword!r}")
            seen.add(folded)

    def _resolve_relative(
        self,
        declaration_path: Path,
        value: Any,
        base: Path,
        boundary: Path,
        field: str,
        *,
        expected: str,
    ) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            self._error(declaration_path, f"{field} must be a nonempty relative path")
            return None

        normalized = value.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            self._error(declaration_path, f"{field} must be relative")
            return None
        if ".." in posix.parts:
            self._error(declaration_path, f"{field} must not contain '..' traversal")
            return None

        candidate = (base / Path(*posix.parts)).resolve()
        try:
            candidate.relative_to(boundary.resolve())
        except ValueError:
            self._error(declaration_path, f"{field} resolves outside its allowed root")
            return None

        if expected == "directory" and not candidate.is_dir():
            self._error(declaration_path, f"{field} directory does not exist")
            return None
        if expected == "file" and not candidate.is_file():
            self._error(declaration_path, f"{field} file does not exist")
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

        try:
            root_bytes = root_manifest.read_bytes()
            claude_bytes = claude_manifest.read_bytes()
        except OSError as exc:
            self._error(plugin_root, f"plugin manifests could not be read: {exc}")
            return
        if root_bytes != claude_bytes:
            self._error(plugin_root, "plugin manifests must be byte-for-byte identical")

        manifest = self._read_json(root_manifest)
        if not isinstance(manifest, dict):
            if manifest is not None:
                self._error(root_manifest, "manifest root must be a JSON object")
            return

        for field in CATALOG_PLUGIN_FIELDS:
            if entry.get(field) != manifest.get(field):
                self._error(
                    catalog_path,
                    f"{label}.{field} must match the plugin manifest",
                )

        self._require_name(root_manifest, manifest.get("name"), "name")
        self._require_version(root_manifest, manifest.get("version"), "version")
        self._require_text(root_manifest, manifest.get("description"), "description")
        self._validate_author(root_manifest, manifest.get("author"), "author")
        for field in ("homepage", "repository"):
            value = manifest.get(field)
            self._require_text(root_manifest, value, field)
            if isinstance(value, str):
                self._validate_url(root_manifest, value)
        self._require_text(root_manifest, manifest.get("license"), "license")
        self._validate_keywords(root_manifest, manifest.get("keywords"), "keywords")
        self._require_name(root_manifest, manifest.get("category"), "category")
        self._validate_skills(root_manifest, manifest.get("skills"), plugin_root)
        self._validate_mcp_declaration(
            root_manifest, manifest.get("mcpServers"), plugin_root
        )

    def _validate_skills(
        self, manifest_path: Path, value: Any, plugin_root: Path
    ) -> None:
        if not isinstance(value, list):
            self._error(manifest_path, "skills must be an array")
            return
        seen: set[Path] = set()
        for index, declaration in enumerate(value):
            field = f"skills[{index}]"
            skill_root = self._resolve_relative(
                manifest_path,
                declaration,
                plugin_root,
                plugin_root,
                field,
                expected="directory",
            )
            if skill_root is None:
                continue
            if skill_root in seen:
                self._error(manifest_path, f"{field} duplicates another skill path")
                continue
            seen.add(skill_root)
            self._validate_skill(skill_root / "SKILL.md", skill_root.name)

    def _validate_skill(self, path: Path, expected_name: str) -> None:
        if not path.is_file():
            self._error(path, "declared skill must contain SKILL.md")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self._error(path, "skill must be UTF-8 text")
            return
        except OSError as exc:
            self._error(path, f"skill could not be read: {exc}")
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

    def _validate_mcp_declaration(
        self, manifest_path: Path, value: Any, plugin_root: Path
    ) -> None:
        mcp_path = self._resolve_relative(
            manifest_path,
            value,
            plugin_root,
            plugin_root,
            "mcpServers",
            expected="file",
        )
        if mcp_path is not None:
            self._validate_mcp(mcp_path)

    def _validate_mcp(self, path: Path) -> None:
        data = self._read_json(path)
        if not isinstance(data, dict):
            if data is not None:
                self._error(path, "MCP root must be a JSON object")
            return
        if set(data) != {"mcpServers"} or not isinstance(data["mcpServers"], dict):
            self._error(path, "root must contain only an mcpServers object")
            return
        servers = data["mcpServers"]
        for name in sorted(servers):
            server = servers[name]
            if not isinstance(name, str) or VALID_NAME.fullmatch(name) is None:
                self._error(path, f"MCP server name {name!r} must be kebab-case")
                continue
            if not isinstance(server, dict):
                self._error(path, f"MCP server {name!r} must be an object")
                continue
            if set(server) != {"type", "command", "args"}:
                self._error(
                    path,
                    f"MCP server {name!r} must contain only type, command, and args",
                )
            self._require_text(path, server.get("type"), f"MCP server {name!r} type")
            if server.get("type") != "stdio":
                self._error(path, f"MCP server {name!r} type must be stdio")
            self._require_text(
                path, server.get("command"), f"MCP server {name!r} command"
            )
            args = server.get("args")
            if not isinstance(args, list):
                self._error(path, f"MCP server {name!r} args must be an array")
            else:
                for index, argument in enumerate(args):
                    if not isinstance(argument, str):
                        self._error(
                            path,
                            f"MCP server {name!r} args[{index}] must be a string",
                        )

    def _scan_repository(self) -> None:
        validator_path = Path(__file__).resolve()
        tracked_files = self._tracked_files()
        for path in self.root.rglob("*"):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            ignored_directory = next(
                (
                    part
                    for part in relative.parts[:-1]
                    if part in IGNORED_DIRECTORY_NAMES
                ),
                None,
            )
            if ignored_directory == ".git":
                continue
            if ignored_directory is not None:
                if tracked_files is None or relative not in tracked_files:
                    continue
                self._error(
                    path,
                    f"tracked files under {ignored_directory!r} are prohibited",
                )
            if not (path.is_file() or path.is_symlink()):
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
            if path.resolve() == validator_path:
                continue
            self._validate_public_text(path, text)

    def _tracked_files(self) -> set[Path] | None:
        result = subprocess.run(
            ["git", "-C", str(self.root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return {
            Path(value.decode("utf-8", errors="surrogateescape"))
            for value in result.stdout.split(b"\0")
            if value
        }

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

    def _validate_url(self, path: Path, value: str) -> None:
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or not host:
            self._error(path, f"URL must use public HTTPS: {value}")
            return
        if parsed.username or parsed.password:
            self._error(path, f"credentials in URLs are prohibited: {value}")
        try:
            parsed.port
        except ValueError:
            self._error(path, f"URL port is invalid: {value}")
        if host in PROHIBITED_HOSTS or host.endswith(PROHIBITED_HOST_SUFFIXES):
            self._error(path, f"URL host is not public: {value}")
        else:
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if NUMERIC_HOST_PATTERN.fullmatch(host):
                    self._error(path, f"numeric URL host is prohibited: {value}")
                elif "." not in host:
                    self._error(path, f"URL host is not a public domain: {value}")
            else:
                if not address.is_global:
                    self._error(path, f"URL IP address is not public: {value}")
        decoded_path = parsed.path
        for _ in range(3):
            next_path = unquote(decoded_path)
            if next_path == decoded_path:
                break
            decoded_path = next_path
        if any(
            part.casefold() == "private"
            for part in PurePosixPath(decoded_path).parts
        ):
            self._error(path, f"private URL path is prohibited: {value}")

    def _require_name(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, str) or VALID_NAME.fullmatch(value) is None:
            self._error(path, f"{field} must be kebab-case")

    def _require_version(self, path: Path, value: Any, field: str) -> None:
        if not isinstance(value, str) or VALID_VERSION.fullmatch(value) is None:
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
        "name": "sample-marketplace",
        "metadata": {
            "description": "Public sample plugins.",
            "version": "0.1.0",
        },
        "owner": {
            "name": "Example Maintainer",
            "email": "maintainer@example.org",
        },
        "plugins": [],
    }


def plugin_entry(name: str, ordinal: int) -> dict[str, Any]:
    """Create neutral catalog metadata for a disposable plugin."""
    return {
        "name": name,
        "source": f"./plugins/{name}",
        "description": f"Sample plugin number {ordinal}.",
        "version": f"0.{ordinal}.0",
        "author": {
            "name": "Example Maintainer",
            "email": "maintainer@example.org",
        },
        "homepage": f"https://github.com/example/{name}",
        "repository": f"https://github.com/example/{name}",
        "license": "MIT",
        "keywords": ["sample", f"plugin-{ordinal}"],
        "category": "developer-tools",
    }


def write_plugin(root: Path, entry: dict[str, Any]) -> Path:
    """Create one synchronized neutral plugin payload for self-tests."""
    plugin_root = root / entry["source"].removeprefix("./")
    manifest = {
        key: entry[key]
        for key in CATALOG_PLUGIN_FIELDS
    }
    manifest["skills"] = [
        "./skills/prepare",
        "./skills/publish",
    ]
    manifest["mcpServers"] = "./.mcp.json"
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
                "alpha-server": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "sample_alpha"],
                },
                "beta-server": {
                    "type": "stdio",
                    "command": "node",
                    "args": ["sample-beta.js", "--quiet"],
                },
            }
        },
    )
    for skill in ("prepare", "publish"):
        path = plugin_root / "skills" / skill / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {skill}\ndescription: Fixture skill.\n---\n\n# {skill}\n",
            encoding="utf-8",
        )
    return plugin_root


def sync_manifests(plugin_root: Path, manifest: Any) -> None:
    """Write matching root and host manifests after a fixture mutation."""
    write_json(plugin_root / "plugin.json", manifest)
    write_json(plugin_root / ".claude-plugin/plugin.json", manifest)


def make_directory_link(link: Path, target: Path) -> None:
    """Create a directory link for portable confinement tests."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr or result.stdout or "could not create directory link")


FixtureMutation = Callable[[Path, dict[str, Any]], None]


def fixture_errors(
    mutate: FixtureMutation | None = None,
    *,
    plugin_count: int = 0,
    rewrite_catalog: bool = True,
) -> list[str]:
    """Build a disposable fixture, optionally mutate it, and return errors."""
    with tempfile.TemporaryDirectory(prefix="marketplace-validator-") as directory:
        workspace = Path(directory)
        root = workspace / "repository"
        marketplace = valid_marketplace()
        for ordinal in range(1, plugin_count + 1):
            name = "example-plugin" if ordinal == 1 else f"sample-plugin-{ordinal}"
            entry = plugin_entry(name, ordinal)
            marketplace["plugins"].append(entry)
            write_plugin(root, entry)
        write_json(root / MARKETPLACE_PATH, marketplace)
        if mutate is not None:
            mutate(root, marketplace)
            if rewrite_catalog:
                write_json(root / MARKETPLACE_PATH, marketplace)
        return validate_root(root)


def run_self_tests() -> int:
    """Exercise positive and negative invariants without external test packages."""
    failures: list[str] = []
    cases = 0

    def expect_valid(name: str, *, plugin_count: int = 0) -> None:
        nonlocal cases
        cases += 1
        errors = fixture_errors(plugin_count=plugin_count)
        if errors:
            failures.append(f"{name}: expected valid, got {errors}")

    def expect_invalid(
        name: str,
        needle: str,
        mutate: FixtureMutation,
        *,
        plugin_count: int = 0,
        rewrite_catalog: bool = True,
    ) -> None:
        nonlocal cases
        cases += 1
        errors = fixture_errors(
            mutate,
            plugin_count=plugin_count,
            rewrite_catalog=rewrite_catalog,
        )
        if not any(needle in error for error in errors):
            failures.append(f"{name}: expected {needle!r}, got {errors}")

    expect_valid("empty catalog")
    expect_valid("one-plugin catalog", plugin_count=1)
    expect_valid("multi-plugin catalog", plugin_count=2)
    expect_invalid(
        "catalog root type",
        "catalog root must be a JSON object",
        lambda root, _data: write_json(root / MARKETPLACE_PATH, []),
        rewrite_catalog=False,
    )
    expect_invalid(
        "metadata type",
        "metadata must be an object",
        lambda _root, data: data.update(metadata=[]),
    )
    expect_invalid(
        "owner type",
        "owner must be an object",
        lambda _root, data: data.update(owner="maintainer"),
    )
    expect_invalid(
        "plugins type",
        "plugins must be an array",
        lambda _root, data: data.update(plugins={}),
    )
    expect_invalid(
        "plugin entry type",
        "plugins[0] must be an object",
        lambda _root, data: data["plugins"].__setitem__(0, []),
        plugin_count=1,
    )
    expect_invalid(
        "invalid marketplace name",
        "kebab-case",
        lambda _root, data: data.update(name="Bad_Name"),
    )
    expect_invalid(
        "invalid semantic version",
        "semantic version",
        lambda _root, data: data["metadata"].update(version="1"),
    )
    expect_invalid(
        "duplicate plugin names",
        "duplicates",
        lambda _root, data: data["plugins"].append(
            {**data["plugins"][0], "name": "Example-Plugin"}
        ),
        plugin_count=1,
    )
    expect_invalid(
        "missing source",
        "directory does not exist",
        lambda _root, data: data["plugins"][0].update(source="./plugins/missing"),
        plugin_count=1,
    )
    expect_invalid(
        "absolute source",
        "must be relative",
        lambda _root, data: data["plugins"][0].update(source="C:\\outside"),
        plugin_count=1,
    )
    expect_invalid(
        "traversal source",
        "must not contain '..'",
        lambda _root, data: data["plugins"][0].update(source="../outside"),
        plugin_count=1,
    )

    def escaping_source(root: Path, data: dict[str, Any]) -> None:
        outside = root.parent / "outside-plugin"
        outside.mkdir()
        make_directory_link(root / "plugins/linked-plugin", outside)
        data["plugins"][0]["source"] = "./plugins/linked-plugin"

    expect_invalid(
        "escaping source symlink",
        "resolves outside its allowed root",
        escaping_source,
        plugin_count=1,
    )

    def drift_manifest(root: Path, _data: dict[str, Any]) -> None:
        path = root / "plugins/example-plugin/.claude-plugin/plugin.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    expect_invalid(
        "manifest drift",
        "byte-for-byte identical",
        drift_manifest,
        plugin_count=1,
    )

    def remove_manifest(root: Path, _data: dict[str, Any]) -> None:
        (root / "plugins/example-plugin/.claude-plugin/plugin.json").unlink()

    expect_invalid(
        "missing dual manifest",
        "both plugin manifests are required",
        remove_manifest,
        plugin_count=1,
    )
    expect_invalid(
        "catalog metadata mismatch",
        "must match the plugin manifest",
        lambda _root, data: data["plugins"][0].update(version="9.9.9"),
        plugin_count=1,
    )

    def manifest_root_type(root: Path, _data: dict[str, Any]) -> None:
        sync_manifests(root / "plugins/example-plugin", [])

    expect_invalid(
        "manifest root type",
        "manifest root must be a JSON object",
        manifest_root_type,
        plugin_count=1,
    )

    def mutate_manifest(
        root: Path, field: str, value: Any
    ) -> None:
        plugin_root = root / "plugins/example-plugin"
        manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
        manifest[field] = value
        sync_manifests(plugin_root, manifest)

    expect_invalid(
        "manifest author type",
        "author must be an object",
        lambda root, _data: mutate_manifest(root, "author", []),
        plugin_count=1,
    )
    expect_invalid(
        "manifest keywords type",
        "keywords must be an array",
        lambda root, _data: mutate_manifest(root, "keywords", "sample"),
        plugin_count=1,
    )
    expect_invalid(
        "skills type",
        "skills must be an array",
        lambda root, _data: mutate_manifest(root, "skills", "./skills/prepare"),
        plugin_count=1,
    )
    expect_invalid(
        "missing skill",
        "directory does not exist",
        lambda root, _data: mutate_manifest(root, "skills", ["./skills/missing"]),
        plugin_count=1,
    )
    expect_invalid(
        "skill traversal",
        "must not contain '..'",
        lambda root, _data: mutate_manifest(root, "skills", ["../outside"]),
        plugin_count=1,
    )

    def escaping_skill(root: Path, _data: dict[str, Any]) -> None:
        outside = root.parent / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text(
            "---\nname: escaped\ndescription: Outside.\n---\n",
            encoding="utf-8",
        )
        make_directory_link(root / "plugins/example-plugin/skills/escaped", outside)
        mutate_manifest(root, "skills", ["./skills/escaped"])

    expect_invalid(
        "escaping skill symlink",
        "resolves outside its allowed root",
        escaping_skill,
        plugin_count=1,
    )

    def invalid_skill_frontmatter(root: Path, _data: dict[str, Any]) -> None:
        (root / "plugins/example-plugin/skills/prepare/SKILL.md").write_text(
            "# Missing frontmatter\n",
            encoding="utf-8",
        )

    expect_invalid(
        "skill frontmatter",
        "must start with YAML frontmatter",
        invalid_skill_frontmatter,
        plugin_count=1,
    )
    expect_invalid(
        "MCP declaration type",
        "must be a nonempty relative path",
        lambda root, _data: mutate_manifest(root, "mcpServers", []),
        plugin_count=1,
    )
    expect_invalid(
        "MCP traversal",
        "must not contain '..'",
        lambda root, _data: mutate_manifest(root, "mcpServers", "../outside.json"),
        plugin_count=1,
    )

    def escaping_mcp(root: Path, _data: dict[str, Any]) -> None:
        outside = root.parent / "outside-config"
        outside.mkdir()
        write_json(outside / "servers.json", {"mcpServers": {}})
        make_directory_link(root / "plugins/example-plugin/config", outside)
        mutate_manifest(root, "mcpServers", "./config/servers.json")

    expect_invalid(
        "escaping MCP symlink",
        "resolves outside its allowed root",
        escaping_mcp,
        plugin_count=1,
    )

    def replace_mcp(root: Path, value: Any) -> None:
        write_json(root / "plugins/example-plugin/.mcp.json", value)

    def malformed_mcp(root: Path, _data: dict[str, Any]) -> None:
        (root / "plugins/example-plugin/.mcp.json").write_text("{", encoding="utf-8")

    expect_invalid(
        "malformed MCP",
        "malformed JSON",
        malformed_mcp,
        plugin_count=1,
    )
    expect_invalid(
        "MCP root type",
        "MCP root must be a JSON object",
        lambda root, _data: replace_mcp(root, []),
        plugin_count=1,
    )
    expect_invalid(
        "MCP root shape",
        "root must contain only an mcpServers object",
        lambda root, _data: replace_mcp(root, {"servers": {}}),
        plugin_count=1,
    )
    expect_invalid(
        "MCP server type",
        "must be an object",
        lambda root, _data: replace_mcp(
            root, {"mcpServers": {"sample-server": []}}
        ),
        plugin_count=1,
    )
    expect_invalid(
        "MCP command type",
        "command must be a nonempty string",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "sample-server": {
                        "type": "stdio",
                        "command": 7,
                        "args": [],
                    }
                }
            },
        ),
        plugin_count=1,
    )
    expect_invalid(
        "MCP transport",
        "type must be stdio",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "sample-server": {
                        "type": "unsupported",
                        "command": "sample-command",
                        "args": [],
                    }
                }
            },
        ),
        plugin_count=1,
    )
    expect_invalid(
        "MCP args type",
        "args must be an array",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "sample-server": {
                        "type": "stdio",
                        "command": "sample-command",
                        "args": "--quiet",
                    }
                }
            },
        ),
        plugin_count=1,
    )
    expect_invalid(
        "MCP argument item type",
        "args[1] must be a string",
        lambda root, _data: replace_mcp(
            root,
            {
                "mcpServers": {
                    "sample-server": {
                        "type": "stdio",
                        "command": "sample-command",
                        "args": ["--count", 2],
                    }
                }
            },
        ),
        plugin_count=1,
    )

    def add_text(root: Path, text: str, name: str = "README.md") -> None:
        (root / name).write_text(text, encoding="utf-8")

    expect_invalid(
        "prohibited file",
        "prohibited governance file",
        lambda root, _data: add_text(root, "{}", "agency.json"),
    )
    expect_invalid(
        "prohibited branding",
        "prohibited branding",
        lambda root, _data: add_text(root, "Legacy agency material."),
    )
    expect_invalid(
        "corporate email",
        "prohibited corporate domain",
        lambda root, _data: add_text(root, "owner@microsoft.com"),
    )
    expect_invalid(
        "credential",
        "possible GitHub token",
        lambda root, _data: add_text(root, "gh" + "p_" + ("x" * 24)),
    )
    expect_invalid(
        "HTTP URL",
        "must use public HTTPS",
        lambda root, _data: add_text(root, "http://github.com/example/project"),
    )
    expect_invalid(
        "credential URL",
        "credentials in URLs",
        lambda root, _data: add_text(
            root, "https://user:password@github.com/example/project"
        ),
    )
    expect_invalid(
        "localhost URL",
        "URL host is not public",
        lambda root, _data: add_text(root, "https://localhost/resource"),
    )
    expect_invalid(
        "private IP URL",
        "URL IP address is not public",
        lambda root, _data: add_text(root, "https://127.0.0.1/resource"),
    )
    expect_invalid(
        "shorthand IP URL",
        "numeric URL host is prohibited",
        lambda root, _data: add_text(root, "https://127.1/resource"),
    )
    expect_invalid(
        "reserved suffix URL",
        "URL host is not public",
        lambda root, _data: add_text(root, "https://service.example.test/resource"),
    )
    expect_invalid(
        "special-use URL",
        "URL host is not public",
        lambda root, _data: add_text(root, "https://service.example/resource"),
    )
    expect_invalid(
        "private path URL",
        "private URL path is prohibited",
        lambda root, _data: add_text(
            root, "https://github.com/example/private/resource"
        ),
    )
    expect_invalid(
        "encoded private path URL",
        "private URL path is prohibited",
        lambda root, _data: add_text(
            root, "https://github.com/example/%70rivate/resource"
        ),
    )

    cases += 1
    aggregate_errors = fixture_errors(
        lambda _root, data: (
            data.update(name="Bad_Name"),
            data["metadata"].update(version="invalid"),
        )
    )
    if not (
        any("marketplace name must be kebab-case" in error for error in aggregate_errors)
        and any("metadata.version must be a semantic version" in error for error in aggregate_errors)
    ):
        failures.append(
            f"aggregate errors: expected both actionable errors, got {aggregate_errors}"
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
