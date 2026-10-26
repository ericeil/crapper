"""Optional crapper.toml in the project root."""

from __future__ import annotations

import tomllib
from pathlib import Path

_KEYS = ("gradle-args", "maven-args")


class ConfigError(Exception):
    """crapper.toml is present but not usable."""


def build_tool_args(root: Path) -> tuple[list[str], list[str]]:
    """Gradle and Maven arguments from `crapper.toml`, in that order.

    A missing file means no extra arguments. Command-line flags are added
    after these, so a one-off run can extend the project's defaults.
    """

    path = root / "crapper.toml"
    if not path.is_file():
        return [], []
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
    return _string_list(path, data, "gradle-args"), _string_list(path, data, "maven-args")


def _string_list(path: Path, data: dict, key: str) -> list[str]:
    if key not in data:
        return []
    value = data[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{path}: {key} must be an array of strings")
    return list(value)
