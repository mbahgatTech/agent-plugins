#!/usr/bin/env python3
"""Discover and execute validation hooks owned by marketplace plugins."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Mapping, Sequence


MARKETPLACE_PATH = Path(".claude-plugin/marketplace.json")
PLUGIN_ROOT_ENV = "MARKETPLACE_PLUGIN_ROOT"
REPOSITORY_ROOT_ENV = "MARKETPLACE_REPOSITORY_ROOT"


@dataclass(frozen=True)
class Plugin:
    """Identify one catalog plugin and its confined payload root."""

    name: str
    root: Path


@dataclass(frozen=True)
class Hook:
    """Describe one plugin-owned command that the runner will execute."""

    plugin: Plugin
    name: str
    command: tuple[str, ...]


Executor = Callable[[Hook, Mapping[str, str]], int]


def read_catalog_plugins(root: Path) -> tuple[list[Plugin], list[str]]:
    """Discover catalog plugins in deterministic order without importing payload code."""
    repository_root = root.resolve()
    catalog_path = repository_root / MARKETPLACE_PATH
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], [f"{MARKETPLACE_PATH}: required catalog is missing"]
    except UnicodeDecodeError:
        return [], [f"{MARKETPLACE_PATH}: catalog must be UTF-8 text"]
    except json.JSONDecodeError as exc:
        return [], [
            f"{MARKETPLACE_PATH}: malformed JSON at line {exc.lineno}, column {exc.colno}"
        ]

    if not isinstance(catalog, dict):
        return [], [f"{MARKETPLACE_PATH}: catalog root must be an object"]
    entries = catalog.get("plugins")
    if not isinstance(entries, list):
        return [], [f"{MARKETPLACE_PATH}: plugins must be an array"]

    plugins: list[Plugin] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"plugins[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{MARKETPLACE_PATH}: {label} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{MARKETPLACE_PATH}: {label}.name must be a nonempty string")
            continue
        folded = name.casefold()
        if folded in seen:
            errors.append(f"{MARKETPLACE_PATH}: {label}.name duplicates {name!r}")
            continue
        seen.add(folded)
        plugin_root, source_error = resolve_catalog_source(
            repository_root, entry.get("source"), label, name
        )
        if source_error is not None:
            errors.append(source_error)
            continue
        plugins.append(Plugin(name=name, root=plugin_root))

    plugins.sort(key=lambda plugin: (plugin.name.casefold(), plugin.root.as_posix()))
    return plugins, errors


def resolve_catalog_source(
    repository_root: Path, value: object, label: str, plugin_name: str
) -> tuple[Path, str | None]:
    """Resolve one catalog source while rejecting absolute, traversal, and escaping paths."""
    if not isinstance(value, str) or not value.strip():
        return repository_root, (
            f"{MARKETPLACE_PATH}: {label}.source must be a nonempty relative path"
        )
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return repository_root, f"{MARKETPLACE_PATH}: {label}.source must be relative"
    if ".." in posix.parts:
        return repository_root, (
            f"{MARKETPLACE_PATH}: {label}.source must not contain '..' traversal"
        )
    if posix.parts != ("plugins", plugin_name):
        return repository_root, (
            f"{MARKETPLACE_PATH}: {label}.source must be plugins/{plugin_name}"
        )
    candidate = (repository_root / Path(*posix.parts)).resolve()
    try:
        candidate.relative_to(repository_root)
    except ValueError:
        return repository_root, (
            f"{MARKETPLACE_PATH}: {label}.source resolves outside the repository"
        )
    if not candidate.is_dir():
        return repository_root, (
            f"{MARKETPLACE_PATH}: {label}.source directory does not exist"
        )
    return candidate, None


def resolve_hook(
    plugin: Plugin, relative: Path, *, required: bool
) -> tuple[Path | None, str | None]:
    """Resolve a fixed hook name and keep symlinks inside the owning payload."""
    candidate = (plugin.root / relative).resolve()
    try:
        candidate.relative_to(plugin.root)
    except ValueError:
        return None, (
            f"plugin {plugin.name}: hook {relative.as_posix()} resolves outside "
            "the plugin payload"
        )
    if not candidate.is_file():
        if required:
            return None, (
                f"plugin {plugin.name}: required hook {relative.as_posix()} is missing"
            )
        return None, None
    return candidate, None


def discover_hooks(
    root: Path, phase: str, platform: str | None
) -> tuple[list[Hook], list[str], list[str]]:
    """Build the ordered hook list and optional-hook skip messages for one phase."""
    plugins, errors = read_catalog_plugins(root)
    hooks: list[Hook] = []
    skipped: list[str] = []
    for plugin in plugins:
        if phase == "static":
            validator, error = resolve_hook(
                plugin, Path("validation/validate.py"), required=True
            )
            if error is not None:
                errors.append(error)
                continue
            assert validator is not None
            hooks.extend(
                (
                    Hook(
                        plugin=plugin,
                        name="validation/validate.py",
                        command=(sys.executable, str(validator)),
                    ),
                    Hook(
                        plugin=plugin,
                        name="validation/validate.py --self-test",
                        command=(sys.executable, str(validator), "--self-test"),
                    ),
                )
            )
            continue

        if phase != "runtime":
            errors.append(f"unknown validation phase: {phase}")
            break
        if platform == "ubuntu":
            relative = Path("validation/ci-ubuntu.sh")
            prefix = ("bash",)
        elif platform == "windows":
            relative = Path("validation/ci-windows.ps1")
            prefix = ("pwsh", "-NoProfile", "-File")
        else:
            errors.append("runtime validation requires --platform ubuntu or windows")
            break
        platform_hook, error = resolve_hook(plugin, relative, required=False)
        if error is not None:
            errors.append(error)
        elif platform_hook is None:
            skipped.append(
                f"plugin {plugin.name}: optional hook {relative.as_posix()} not present"
            )
        else:
            hooks.append(
                Hook(
                    plugin=plugin,
                    name=relative.as_posix(),
                    command=(*prefix, str(platform_hook)),
                )
            )
    return hooks, errors, skipped


def execute_hook(hook: Hook, environment: Mapping[str, str]) -> int:
    """Run one hook in the repository root while streaming its output."""
    result = subprocess.run(
        hook.command,
        cwd=environment[REPOSITORY_ROOT_ENV],
        env=dict(environment),
        check=False,
    )
    return result.returncode


def run_phase(
    root: Path,
    phase: str,
    platform: str | None = None,
    *,
    executor: Executor = execute_hook,
    base_environment: Mapping[str, str] | None = None,
) -> int:
    """Execute every discovered hook and identify each failure by plugin and hook."""
    repository_root = root.resolve()
    hooks, errors, skipped = discover_hooks(repository_root, phase, platform)
    for message in skipped:
        print(f"SKIP: {message}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    environment = dict(os.environ)
    if base_environment is not None:
        environment.update(base_environment)
    environment[REPOSITORY_ROOT_ENV] = str(repository_root)
    failures = 0
    for hook in hooks:
        print(f"RUN: plugin {hook.plugin.name}: hook {hook.name}", flush=True)
        hook_environment = dict(environment)
        hook_environment[PLUGIN_ROOT_ENV] = str(hook.plugin.root)
        return_code = executor(hook, hook_environment)
        if return_code != 0:
            failures += 1
            print(
                f"ERROR: plugin {hook.plugin.name}: hook {hook.name} "
                f"failed with exit code {return_code}",
                file=sys.stderr,
            )
    if failures:
        return 1
    print(f"Plugin validation passed: phase={phase}, hooks={len(hooks)}")
    return 0


def write_catalog(root: Path, entries: Sequence[dict[str, str]]) -> None:
    """Write the minimal catalog shape consumed by discovery self-tests."""
    path = root / MARKETPLACE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"plugins": list(entries)}, indent=2) + "\n", encoding="utf-8")


def write_static_hook(plugin_root: Path, *, failure_mode: str = "") -> None:
    """Create a portable static hook that records its mode for self-tests."""
    validation = plugin_root / "validation"
    validation.mkdir(parents=True, exist_ok=True)
    script = (
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        f"failure_mode = {failure_mode!r}\n"
        "mode = 'self-test' if '--self-test' in sys.argv else 'validate'\n"
        "plugin = Path(os.environ['MARKETPLACE_PLUGIN_ROOT']).name\n"
        "with Path(os.environ['HOOK_LOG']).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(f'{plugin}:{mode}\\n')\n"
        "if failure_mode == mode:\n"
        "    raise SystemExit(7)\n"
    )
    (validation / "validate.py").write_text(script, encoding="utf-8")


def make_directory_link(link: Path, target: Path) -> None:
    """Create a directory link for portable hook-confinement tests."""
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


def run_self_tests() -> int:
    """Verify discovery, confinement, ordering, optional hooks, and failures."""
    failures: list[str] = []
    cases = 0

    def check(name: str, condition: bool, detail: object) -> None:
        nonlocal cases
        cases += 1
        if not condition:
            failures.append(f"{name}: {detail}")

    with tempfile.TemporaryDirectory(prefix="plugin-validation-") as directory:
        workspace = Path(directory)
        root = workspace / "repository"
        alpha = root / "plugins/alpha-plugin"
        beta = root / "plugins/beta-plugin"
        write_static_hook(alpha)
        write_static_hook(beta)
        write_catalog(
            root,
            (
                {"name": "beta-plugin", "source": "./plugins/beta-plugin"},
                {"name": "alpha-plugin", "source": "./plugins/alpha-plugin"},
            ),
        )
        log = workspace / "hooks.log"
        environment = {"HOOK_LOG": str(log)}
        result = run_phase(root, "static", base_environment=environment)
        order = log.read_text(encoding="utf-8").splitlines()
        check("static execution", result == 0, f"exit code {result}")
        check(
            "deterministic order and modes",
            order
            == [
                "alpha-plugin:validate",
                "alpha-plugin:self-test",
                "beta-plugin:validate",
                "beta-plugin:self-test",
            ],
            order,
        )
        check(
            "disposable second plugin",
            any(line.startswith("beta-plugin:") for line in order),
            order,
        )

        (alpha / "validation/validate.py").unlink()
        _, missing_errors, _ = discover_hooks(root, "static", None)
        check(
            "missing validator",
            any(
                "plugin alpha-plugin" in error and "required hook" in error
                for error in missing_errors
            ),
            missing_errors,
        )
        write_static_hook(alpha)

        traversal_entries = (
            {"name": "escaped-plugin", "source": "../outside"},
        )
        write_catalog(root, traversal_entries)
        _, traversal_errors = read_catalog_plugins(root)
        check(
            "source traversal",
            any("must not contain '..'" in error for error in traversal_errors),
            traversal_errors,
        )

        write_catalog(
            root,
            ({"name": "root-plugin", "source": "."},),
        )
        _, root_source_errors = read_catalog_plugins(root)
        check(
            "repository root source",
            any("must be plugins/root-plugin" in error for error in root_source_errors),
            root_source_errors,
        )

        write_catalog(
            root,
            ({"name": "alpha-plugin", "source": "./plugins/beta-plugin"},),
        )
        _, mismatched_source_errors = read_catalog_plugins(root)
        check(
            "mismatched source name",
            any(
                "must be plugins/alpha-plugin" in error
                for error in mismatched_source_errors
            ),
            mismatched_source_errors,
        )

        outside_source = workspace / "outside-source"
        outside_source.mkdir()
        make_directory_link(root / "plugins/linked-plugin", outside_source)
        write_catalog(
            root,
            ({"name": "linked-plugin", "source": "./plugins/linked-plugin"},),
        )
        _, source_link_errors = read_catalog_plugins(root)
        check(
            "source symlink confinement",
            any("resolves outside the repository" in error for error in source_link_errors),
            source_link_errors,
        )

        outside_validation = workspace / "outside-validation"
        write_static_hook(outside_validation.parent / outside_validation.name)
        target_validation = outside_validation / "validation"
        if (alpha / "validation").exists():
            for child in (alpha / "validation").iterdir():
                child.unlink()
            (alpha / "validation").rmdir()
        make_directory_link(alpha / "validation", target_validation)
        write_catalog(
            root,
            ({"name": "alpha-plugin", "source": "./plugins/alpha-plugin"},),
        )
        _, hook_link_errors, _ = discover_hooks(root, "static", None)
        check(
            "hook symlink confinement",
            any("outside the plugin payload" in error for error in hook_link_errors),
            hook_link_errors,
        )
        if (alpha / "validation").is_symlink():
            (alpha / "validation").unlink()
        elif (alpha / "validation").exists():
            (alpha / "validation").rmdir()
        write_static_hook(alpha)

        write_catalog(
            root,
            (
                {"name": "beta-plugin", "source": "./plugins/beta-plugin"},
                {"name": "alpha-plugin", "source": "./plugins/alpha-plugin"},
            ),
        )
        _, optional_errors, optional_skips = discover_hooks(root, "runtime", "ubuntu")
        check("optional hook success", not optional_errors, optional_errors)
        check(
            "optional hook skips",
            len(optional_skips) == 2
            and all("optional hook" in message for message in optional_skips),
            optional_skips,
        )

        (alpha / "validation/ci-ubuntu.sh").write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
        )
        (beta / "validation/ci-ubuntu.sh").write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
        )
        (alpha / "validation/ci-windows.ps1").write_text(
            "exit 0\n", encoding="utf-8"
        )
        observed: list[tuple[str, str, tuple[str, ...]]] = []

        def record_hook(hook: Hook, _environment: Mapping[str, str]) -> int:
            observed.append((hook.plugin.name, hook.name, hook.command))
            return 0

        ubuntu_result = run_phase(root, "runtime", "ubuntu", executor=record_hook)
        windows_result = run_phase(root, "runtime", "windows", executor=record_hook)
        check(
            "platform hook execution",
            ubuntu_result == 0 and windows_result == 0,
            (ubuntu_result, windows_result),
        )
        check(
            "platform command selection",
            observed[0][0:2] == ("alpha-plugin", "validation/ci-ubuntu.sh")
            and observed[0][2][0] == "bash"
            and observed[-1][0:2] == ("alpha-plugin", "validation/ci-windows.ps1")
            and observed[-1][2][0:3] == ("pwsh", "-NoProfile", "-File"),
            observed,
        )

        write_static_hook(beta, failure_mode="self-test")
        log.write_text("", encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            failure_result = run_phase(
                root, "static", base_environment={"HOOK_LOG": str(log)}
            )
        failure_message = stderr.getvalue()
        check("child failure result", failure_result == 1, failure_result)
        check(
            "named failure propagation",
            "plugin beta-plugin" in failure_message
            and "validate.py --self-test" in failure_message
            and "exit code 7" in failure_message,
            failure_message,
        )

        write_catalog(root, ())
        empty_result = run_phase(root, "static", base_environment=environment)
        check("empty catalog", empty_result == 0, empty_result)

    if failures:
        for failure in failures:
            print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"Plugin runner self-tests passed: {cases} cases")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the stable discovery-runner command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("static", "runtime"))
    parser.add_argument("--platform", choices=("ubuntu", "windows"))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root to validate (default: current directory)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run disposable discovery and execution fixtures",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run self-tests or execute one validation phase."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return run_self_tests()
    if args.phase is None:
        print("ERROR: --phase is required unless --self-test is used", file=sys.stderr)
        return 2
    if args.phase == "runtime" and args.platform is None:
        print("ERROR: runtime validation requires --platform", file=sys.stderr)
        return 2
    if not args.root.exists() or not args.root.is_dir():
        print(f"ERROR: {args.root}: root must be a readable directory", file=sys.stderr)
        return 2
    return run_phase(args.root, args.phase, args.platform)


if __name__ == "__main__":
    raise SystemExit(main())
