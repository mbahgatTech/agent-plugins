#!/usr/bin/env python3
"""Validate the VideoPilot payload's pinned contract and safety guidance."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


PLUGIN_NAME = "videopilot"
PLUGIN_VERSION = "0.1.0"
ENGINE_NAME = "videopilot"
ENGINE_VERSION = "0.1.7"
ENGINE_PIN = f"{ENGINE_NAME}=={ENGINE_VERSION}"
MCP_SERVER_NAME = "videopilot"
MCP_COMMAND = "uvx"
MCP_ARGS = ["--from", ENGINE_PIN, "videopilot-mcp"]
EXPECTED_SKILLS = [
    "./skills/init",
    "./skills/create-video",
    "./skills/design-slide",
]
EXPECTED_TOOLS = (
    "doctor",
    "voices",
    "list_projects",
    "project_status",
    "init",
    "import_source",
    "read_state",
    "write_state",
    "tts",
    "transcribe",
    "silence",
    "cut",
    "compose",
    "export",
    "schema",
    "add_vo_segment",
    "add_slide",
    "set_compose_output",
    "preview_slide",
    "is_up_to_date",
)
REQUIRED_PATHS = (
    ".claude-plugin/plugin.json",
    ".mcp.json",
    "plugin.json",
    "README.md",
    "skills/init/SKILL.md",
    "skills/init/scripts/install-prereqs.ps1",
    "skills/init/scripts/install-prereqs.sh",
    "skills/create-video/SKILL.md",
    "skills/design-slide/SKILL.md",
    "validation/validate.py",
    "validation/ci-ubuntu.sh",
    "validation/ci-windows.ps1",
)
PIN_PATTERN = re.compile(r"\bvideopilot==([0-9]+\.[0-9]+\.[0-9]+)\b", re.IGNORECASE)
UNPINNED_EXECUTION_PATTERNS = (
    re.compile(r"\buvx\s+--from\s+videopilot(?:\s|$)", re.IGNORECASE),
    re.compile(
        r"\b(?:python\s+-m\s+pip|pip|uv\s+pip)\s+install\s+videopilot(?:\s|$)",
        re.IGNORECASE,
    ),
)
TOOL_ROW_PATTERN = re.compile(r"(?m)^\| `([a-z_]+)` \|")


class VideoPilotValidator:
    """Collect all payload-specific validation errors in a deterministic order."""

    def __init__(self, plugin_root: Path) -> None:
        self.plugin_root = plugin_root.resolve()
        self.errors: list[str] = []

    def validate(self) -> list[str]:
        """Verify exact metadata, runtime pins, tool guidance, and safety markers."""
        self._validate_required_paths()
        manifest = self._validate_manifests()
        if manifest is not None:
            self._validate_manifest_contract(manifest)
        self._validate_mcp()
        self._validate_engine_pins()
        self._validate_runtime_hooks()
        self._validate_skill_contracts()
        return self.errors

    def _error(self, path: Path | str, message: str) -> None:
        try:
            display = Path(path).resolve().relative_to(self.plugin_root)
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

    def _read_text(self, relative: str) -> str | None:
        path = self.plugin_root / relative
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            self._error(path, "must be UTF-8 text")
        except OSError as exc:
            self._error(path, f"could not be read: {exc}")
        return None

    def _validate_required_paths(self) -> None:
        for relative in REQUIRED_PATHS:
            if not (self.plugin_root / relative).is_file():
                self._error(
                    self.plugin_root / relative,
                    "required VideoPilot payload file is missing",
                )

    def _validate_manifests(self) -> dict[str, Any] | None:
        root_manifest = self.plugin_root / "plugin.json"
        host_manifest = self.plugin_root / ".claude-plugin/plugin.json"
        if not root_manifest.is_file() or not host_manifest.is_file():
            return None
        try:
            if root_manifest.read_bytes() != host_manifest.read_bytes():
                self._error(
                    self.plugin_root,
                    "VideoPilot manifests must be byte-for-byte identical",
                )
        except OSError as exc:
            self._error(self.plugin_root, f"manifests could not be read: {exc}")
            return None
        manifest = self._read_json(root_manifest)
        if not isinstance(manifest, dict):
            if manifest is not None:
                self._error(root_manifest, "manifest root must be an object")
            return None
        return manifest

    def _validate_manifest_contract(self, manifest: dict[str, Any]) -> None:
        expected_fields = {
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "license": "MIT",
            "category": "media-tools",
            "skills": EXPECTED_SKILLS,
            "mcpServers": "./.mcp.json",
        }
        for field, expected in expected_fields.items():
            if manifest.get(field) != expected:
                self._error(
                    self.plugin_root / "plugin.json",
                    f"{field} must be exactly {expected!r}",
                )

    def _validate_mcp(self) -> None:
        path = self.plugin_root / ".mcp.json"
        data = self._read_json(path)
        if not isinstance(data, dict):
            return
        if set(data) != {"mcpServers"} or not isinstance(data["mcpServers"], dict):
            self._error(path, "root must contain only an mcpServers object")
            return
        servers = data["mcpServers"]
        if set(servers) != {MCP_SERVER_NAME}:
            self._error(
                path,
                f"mcpServers must define exactly {MCP_SERVER_NAME!r}",
            )
            return
        server = servers[MCP_SERVER_NAME]
        if not isinstance(server, dict):
            self._error(path, f"{MCP_SERVER_NAME} server must be an object")
            return
        expected = {
            "type": "stdio",
            "command": MCP_COMMAND,
            "args": MCP_ARGS,
        }
        if server != expected:
            self._error(
                path,
                f"{MCP_SERVER_NAME} server must be exactly {expected!r}",
            )

    def _validate_engine_pins(self) -> None:
        pin_occurrences = 0
        for path in sorted(self.plugin_root.rglob("*")):
            relative = path.relative_to(self.plugin_root)
            if relative == Path("validation/validate.py") or "__pycache__" in relative.parts:
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            matches = PIN_PATTERN.findall(text)
            pin_occurrences += len(matches)
            for version in matches:
                if version != ENGINE_VERSION:
                    self._error(
                        path,
                        f"engine pin must be exactly {ENGINE_PIN}",
                    )
            for pattern in UNPINNED_EXECUTION_PATTERNS:
                if pattern.search(text):
                    self._error(path, f"engine execution must use {ENGINE_PIN}")
        if pin_occurrences == 0:
            self._error(self.plugin_root, f"payload must reference {ENGINE_PIN}")

    def _validate_runtime_hooks(self) -> None:
        required_commands = (
            f"uvx --from {ENGINE_PIN} videopilot-mcp --version",
            f"uvx --from {ENGINE_PIN} videopilot doctor",
        )
        for relative in (
            "validation/ci-ubuntu.sh",
            "validation/ci-windows.ps1",
        ):
            text = self._read_text(relative)
            if text is None:
                continue
            for command in required_commands:
                if command not in text:
                    self._error(
                        self.plugin_root / relative,
                        f"required runtime command is missing: {command!r}",
                    )

    def _validate_skill_contracts(self) -> None:
        create_text = self._read_text("skills/create-video/SKILL.md")
        init_text = self._read_text("skills/init/SKILL.md")
        design_text = self._read_text("skills/design-slide/SKILL.md")
        if create_text is not None:
            self._validate_create_video(create_text)
        if init_text is not None:
            self._validate_init(init_text)
        if design_text is not None:
            self._validate_design_slide(design_text)

    def _validate_create_video(self, text: str) -> None:
        path = self.plugin_root / "skills/create-video/SKILL.md"
        tools = tuple(TOOL_ROW_PATTERN.findall(text))
        if tools != EXPECTED_TOOLS:
            self._error(
                path,
                f"public tool table must list exactly {list(EXPECTED_TOOLS)!r}",
            )
        normalized = normalize_whitespace(text)
        required_markers = (
            "Immediately before calling `tts`, obtain explicit user approval.",
            "Immediately before calling `cut`, obtain explicit user approval.",
            "Immediately before calling `compose`, obtain explicit user approval.",
            "out/preview-NNN.mp4",
            "leave the associated segment, clip, state, and intermediate files intact",
        )
        for marker in required_markers:
            if marker not in normalized:
                self._error(path, f"required safety marker is missing: {marker!r}")

    def _validate_init(self, text: str) -> None:
        path = self.plugin_root / "skills/init/SKILL.md"
        normalized = normalize_whitespace(text)
        required_markers = (
            "Their default mode never installs software, invokes a package manager, or elevates",
            "obtain explicit approval before continuing",
            "Noninteractive install mode fails unless the affirmative option is present",
            f"uvx --from {ENGINE_PIN} videopilot-mcp --version",
            f"uvx --from {ENGINE_PIN} videopilot doctor",
        )
        for marker in required_markers:
            if marker not in normalized:
                self._error(path, f"required setup safety marker is missing: {marker!r}")

    def _validate_design_slide(self, text: str) -> None:
        path = self.plugin_root / "skills/design-slide/SKILL.md"
        normalized = normalize_whitespace(text)
        required_markers = (
            "uv run --script",
            "Do not run a global `pip install Pillow`",
            'Image.new("RGB", (1920, 1080)',
            "out/preview-NNN.mp4",
            "do not copy generated images or project state into the marketplace repository",
        )
        for marker in required_markers:
            if marker not in normalized:
                self._error(path, f"required slide safety marker is missing: {marker!r}")


def normalize_whitespace(value: str) -> str:
    """Collapse formatting-only whitespace while preserving maintainer wording."""
    return " ".join(value.split())


def validate_plugin(plugin_root: Path) -> list[str]:
    """Run VideoPilot payload validation for command-line and fixture callers."""
    return VideoPilotValidator(plugin_root).validate()


def sync_manifests(plugin_root: Path, manifest: Any) -> None:
    """Write matching manifests after a disposable mutation."""
    encoded = json.dumps(manifest, indent=2) + "\n"
    (plugin_root / "plugin.json").write_text(encoded, encoding="utf-8")
    (plugin_root / ".claude-plugin/plugin.json").write_text(encoded, encoding="utf-8")


Mutation = Callable[[Path], None]


def fixture_errors(source_root: Path, mutate: Mutation) -> list[str]:
    """Copy the payload, apply one mutation, and return validation errors."""
    with tempfile.TemporaryDirectory(prefix="videopilot-validator-") as directory:
        fixture_root = Path(directory) / PLUGIN_NAME
        shutil.copytree(
            source_root,
            fixture_root,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        mutate(fixture_root)
        return validate_plugin(fixture_root)


def run_self_tests(plugin_root: Path) -> int:
    """Reject representative drift in every payload-specific invariant."""
    failures: list[str] = []
    cases = 0

    real_errors = validate_plugin(plugin_root)
    cases += 1
    if real_errors:
        failures.append(f"unchanged payload: expected valid, got {real_errors}")

    def expect_invalid(name: str, needle: str, mutate: Mutation) -> None:
        nonlocal cases
        cases += 1
        errors = fixture_errors(plugin_root, mutate)
        if not any(needle in error for error in errors):
            failures.append(f"{name}: expected {needle!r}, got {errors}")

    def mutate_manifest(root: Path, field: str, value: Any) -> None:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        manifest[field] = value
        sync_manifests(root, manifest)

    expect_invalid(
        "wrong plugin name",
        "name must be exactly",
        lambda root: mutate_manifest(root, "name", "other-plugin"),
    )
    expect_invalid(
        "wrong plugin version",
        "version must be exactly",
        lambda root: mutate_manifest(root, "version", "9.9.9"),
    )

    def drift_manifest(root: Path) -> None:
        path = root / ".claude-plugin/plugin.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    expect_invalid("manifest drift", "byte-for-byte identical", drift_manifest)
    expect_invalid(
        "wrong skill set",
        "skills must be exactly",
        lambda root: mutate_manifest(root, "skills", ["./skills/init"]),
    )

    def replace_mcp(root: Path, value: Any) -> None:
        (root / ".mcp.json").write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )

    expect_invalid(
        "wrong engine pin",
        "server must be exactly",
        lambda root: replace_mcp(
            root,
            {
                "mcpServers": {
                    MCP_SERVER_NAME: {
                        "type": "stdio",
                        "command": MCP_COMMAND,
                        "args": ["--from", "videopilot==9.9.9", "videopilot-mcp"],
                    }
                }
            },
        ),
    )
    expect_invalid(
        "wrong MCP command",
        "server must be exactly",
        lambda root: replace_mcp(
            root,
            {
                "mcpServers": {
                    MCP_SERVER_NAME: {
                        "type": "stdio",
                        "command": "python",
                        "args": MCP_ARGS,
                    }
                }
            },
        ),
    )
    expect_invalid(
        "wrong MCP server name",
        "must define exactly",
        lambda root: replace_mcp(
            root,
            {
                "mcpServers": {
                    "other-server": {
                        "type": "stdio",
                        "command": MCP_COMMAND,
                        "args": MCP_ARGS,
                    }
                }
            },
        ),
    )
    expect_invalid(
        "missing required file",
        "required VideoPilot payload file is missing",
        lambda root: (root / "README.md").unlink(),
    )

    def replace_text(root: Path, relative: str, old: str, new: str) -> None:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError(f"fixture marker not found: {old}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    expect_invalid(
        "tool set drift",
        "public tool table must list exactly",
        lambda root: replace_text(
            root,
            "skills/create-video/SKILL.md",
            "| `doctor` | none |\n",
            "",
        ),
    )
    expect_invalid(
        "approval marker drift",
        "required safety marker is missing",
        lambda root: replace_text(
            root,
            "skills/create-video/SKILL.md",
            "Immediately before calling `tts`, obtain explicit user approval.",
            "Call `tts` when ready.",
        ),
    )
    expect_invalid(
        "preview path drift",
        "required safety marker is missing",
        lambda root: replace_text(
            root,
            "skills/create-video/SKILL.md",
            "out/preview-NNN.mp4",
            "out/preview.png",
        ),
    )
    expect_invalid(
        "safe default drift",
        "required setup safety marker is missing",
        lambda root: replace_text(
            root,
            "skills/init/SKILL.md",
            "Their default\nmode never installs software, invokes a package manager, or elevates",
            "Their default mode may install missing software",
        ),
    )
    expect_invalid(
        "unpinned engine execution",
        "engine execution must use",
        lambda root: replace_text(
            root,
            "README.md",
            f"uvx --from {ENGINE_PIN} videopilot-mcp",
            "uvx --from videopilot videopilot-mcp",
        ),
    )
    expect_invalid(
        "Ubuntu hook pin drift",
        "engine pin must be exactly",
        lambda root: replace_text(
            root,
            "validation/ci-ubuntu.sh",
            ENGINE_PIN,
            "videopilot==9.9.9",
        ),
    )
    expect_invalid(
        "Windows hook pin drift",
        "engine pin must be exactly",
        lambda root: replace_text(
            root,
            "validation/ci-windows.ps1",
            ENGINE_PIN,
            "videopilot==9.9.9",
        ),
    )

    if failures:
        for failure in failures:
            print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"VideoPilot validator self-tests passed: {cases} cases")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the payload validator command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="VideoPilot plugin root (default: parent of validation directory)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run disposable payload mutation fixtures",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run payload validation or its mutation self-tests."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not args.root.exists() or not args.root.is_dir():
        print(f"ERROR: {args.root}: plugin root must be a readable directory", file=sys.stderr)
        return 2
    if args.self_test:
        return run_self_tests(args.root)
    errors = validate_plugin(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"VideoPilot validation passed: {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
