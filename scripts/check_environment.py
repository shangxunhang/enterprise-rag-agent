"""Validate the reproducible runtime without mutating the active environment."""

from __future__ import annotations

import argparse
import importlib
from importlib import metadata
import os
from pathlib import Path
import re
import sys
from typing import Iterable

import tomli


EXPECTED_PYTHON = (3, 10)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HOST_TORCH_VERSION = "2.4.1"
_PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^;\s]+)")


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _provider_configuration_errors() -> list[str]:
    enabled = os.getenv("REAL_PROVIDER_ACCEPTANCE", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return []
    required = ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if not os.getenv("MODEL_PROVIDER", "").strip():
        missing.append("MODEL_PROVIDER")
    return [
        "real-provider acceptance is enabled but these variables are missing: "
        + ", ".join(missing)
    ] if missing else []


def _declared_pins(*, extras: Iterable[str]) -> dict[str, str]:
    """Read exact application pins from the sole maintained source."""

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomli.load(handle)["project"]
    declarations = list(project.get("dependencies") or [])
    optional = dict(project.get("optional-dependencies") or {})
    for extra in extras:
        declarations.extend(optional.get(extra) or [])
    pins: dict[str, str] = {}
    for declaration in declarations:
        match = _PIN_PATTERN.match(str(declaration))
        if match is None:
            raise ValueError(
                "environment validation requires exact pyproject pins: "
                f"{declaration}"
            )
        name, version = match.groups()
        pins[name.lower().replace("_", "-")] = version
    return pins


def check_environment(
    *,
    core_only: bool = False,
    require_local_rag: bool = False,
    require_local_model: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    if sys.version_info[:2] != EXPECTED_PYTHON:
        errors.append(
            "Python 3.10.x is required; create/activate the documented "
            "enterprise-rag-agent environment."
        )

    required_extras: tuple[str, ...] = (
        () if core_only else ("test", "providers")
    )
    if require_local_rag:
        required_extras += ("local-rag",)
    if require_local_model:
        required_extras += ("local-model",)
    expected_packages = _declared_pins(extras=required_extras)
    sync_command = "uv sync --frozen" + "".join(
        f" --extra {extra}" for extra in required_extras
    )
    for name, expected in expected_packages.items():
        actual = _package_version(name)
        if actual != expected:
            errors.append(
                f"{name} expected {expected}, found {actual or 'not installed'}; "
                f"reinstall with: {sync_command}"
            )

    for module_name, install_hint in (
        ("langgraph.checkpoint.sqlite", "langgraph-checkpoint-sqlite"),
        ("langchain_openai", "langchain-openai"),
    ):
        if core_only and module_name == "langchain_openai":
            continue
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(
                f"cannot import {module_name}: {exc}; install {install_hint} "
                "from uv.lock"
            )

    if not core_only:
        torch_version = _package_version("torch")
        if torch_version is None:
            notes.append(
                "host CUDA PyTorch is not installed in this environment; "
                "uv intentionally does not manage it"
            )
        else:
            try:
                torch = importlib.import_module("torch")
            except Exception as exc:
                errors.append(f"cannot import host CUDA PyTorch: {exc}")
            else:
                cuda_version = getattr(
                    getattr(torch, "version", None),
                    "cuda",
                    None,
                )
                cuda_available = bool(torch.cuda.is_available())
                notes.append(
                    f"torch={torch_version} "
                    f"cuda_build={cuda_version or 'none'} "
                    f"cuda_available={cuda_available}"
                )
                if torch_version.split("+", 1)[0] != EXPECTED_HOST_TORCH_VERSION:
                    errors.append(
                        "host PyTorch expected "
                        f"{EXPECTED_HOST_TORCH_VERSION}, found {torch_version}"
                    )
                if cuda_version is None:
                    errors.append(
                        "PyTorch is not a CUDA build. Install the validated "
                        "host wheel separately; do not add torch to uv extras."
                    )
        for optional_extra in ("local-rag", "local-model"):
            optional_pins = _declared_pins(extras=(optional_extra,))
            for name, expected in optional_pins.items():
                if name in expected_packages:
                    continue
                actual = _package_version(name)
                if actual is not None and actual != expected:
                    errors.append(
                        f"optional {name} expected {expected}, found {actual}"
                    )
                elif actual is None:
                    notes.append(
                        f"optional {name} is not installed "
                        f"({optional_extra} extra not active)"
                    )
        errors.extend(_provider_configuration_errors())

    lock_file = PROJECT_ROOT / "uv.lock"
    if not lock_file.is_file():
        errors.append("uv.lock is missing; run `uv lock` with Python 3.10")
    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="skip optional Milvus/local-model/provider configuration checks",
    )
    parser.add_argument(
        "--require-local-rag",
        action="store_true",
        help="require the pinned local-rag packages to be installed",
    )
    parser.add_argument(
        "--require-local-model",
        action="store_true",
        help="require the pinned local-model packages and host CUDA runtime",
    )
    args = parser.parse_args()
    if args.core_only and (
        args.require_local_rag or args.require_local_model
    ):
        parser.error(
            "--core-only cannot be combined with --require-local-rag "
            "or --require-local-model"
        )
    errors, notes = check_environment(
        core_only=args.core_only,
        require_local_rag=args.require_local_rag,
        require_local_model=args.require_local_model,
    )
    for note in notes:
        print(f"INFO: {note}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Environment validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
