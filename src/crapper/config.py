"""Optional crapper.toml in the project root."""

from __future__ import annotations

import tomllib
from pathlib import Path

from crapper.runners import validate_jacoco_version

_KEYS = ("gradle-args", "maven-args", "jacoco-version")


class ConfigError(Exception):
    """crapper.toml is present but not usable."""


def build_tool_args(root: Path) -> tuple[list[str], list[str], str | None]:
    """Gradle arguments, Maven arguments, and an optional JaCoCo version.

    A missing file means no extra arguments and no version override.
    Command-line flags are added after the argument lists, so a one-off run
    can extend the project's defaults. `--jacoco-version` replaces the
    version from this file.
    """

    path = root / "crapper.toml"
    if not path.is_file():
        return [], [], None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a table")
    unknown = sorted(set(data) - set(_KEYS))
    if unknown:
        names = ", ".join(unknown)
        raise ConfigError(f"{path}: unknown setting {names}")
    return (
        _string_list(path, data, "gradle-args"),
        _string_list(path, data, "maven-args"),
        _jacoco_version(path, data),
    )


def _jacoco_version(path: Path, data: dict) -> str | None:
    if "jacoco-version" not in data:
        return None
    value = data["jacoco-version"]
    if not isinstance(value, str):
        raise ConfigError(f"{path}: jacoco-version must be a string")
    try:
        return validate_jacoco_version(value)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _string_list(path: Path, data: dict, key: str) -> list[str]:
    if key not in data:
        return []
    value = data[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{path}: {key} must be an array of strings")
    return list(value)
