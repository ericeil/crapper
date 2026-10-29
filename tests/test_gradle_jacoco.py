"""The JaCoCo init script, executed by Gradle 7.

Gradle 7 bundles JaCoCo 0.8.6, which rejects Kotlin 1.5+ SMAP. The script
upgrades that default and leaves a version the build assigns itself.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from crapper.runners import gradle_init_script

_JDK11_CANDIDATES = (
    Path("/usr/lib/jvm/java-11-openjdk-amd64"),
    Path("/usr/lib/jvm/java-11-openjdk-arm64"),
)


def _jdk11() -> Path | None:
    for path in _JDK11_CANDIDATES:
        if (path / "bin" / "java").is_file():
            return path
    return None


def _gradle7() -> Path | None:
    dists = Path.home() / ".gradle" / "wrapper" / "dists"
    found = sorted(dists.glob("gradle-7.0-bin/*/gradle-7.0/bin/gradle"))
    return found[0] if found else None


def _project(root: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name = 'pin'\ninclude 'plain', 'pinned', 'kts'\n", encoding="utf-8")
    (root / "build.gradle").write_text('println "ROOT ${jacoco.toolVersion}"\n', encoding="utf-8")
    plain = root / "plain"
    plain.mkdir()
    (plain / "build.gradle").write_text(
        "apply plugin: 'java'\nprintln \"PLAIN ${jacoco.toolVersion}\"\n",
        encoding="utf-8",
    )
    pinned = root / "pinned"
    pinned.mkdir()
    (pinned / "build.gradle").write_text(
        "apply plugin: 'java'\njacoco { toolVersion = '0.8.7' }\nprintln \"PINNED ${jacoco.toolVersion}\"\n",
        encoding="utf-8",
    )
    kts = root / "kts"
    kts.mkdir()
    (kts / "build.gradle.kts").write_text(
        'plugins { java }\njacoco { toolVersion = "0.8.11" }\nprintln("KTS ${jacoco.toolVersion}")\n',
        encoding="utf-8",
    )


def _gradle(binary: Path, java_home: Path, root: Path, script: Path) -> str:
    completed = subprocess.run(
        [str(binary), "-p", str(root), "--init-script", str(script), "help", "--console=plain", "--offline"],
        cwd=root,
        env={**os.environ, "JAVA_HOME": str(java_home)},
        text=True,
        capture_output=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    return output


@pytest.fixture(scope="module")
def gradle_7() -> tuple[Path, Path]:
    binary = _gradle7()
    java_home = _jdk11()
    if binary is None or java_home is None:
        pytest.skip("needs a local Gradle 7.0 distribution and JDK 11")
    return binary, java_home


def test_gradle_upgrades_its_bundled_jacoco_and_keeps_a_configured_version(tmp_path, gradle_7):
    binary, java_home = gradle_7
    _project(tmp_path)
    script = tmp_path / "jacoco.gradle"
    script.write_text(gradle_init_script(), encoding="utf-8")
    output = _gradle(binary, java_home, tmp_path, script)
    assert "ROOT 0.8.12" in output
    assert "PLAIN 0.8.12" in output
    assert "PINNED 0.8.7" in output
    assert "KTS 0.8.11" in output


def test_an_explicit_jacoco_version_still_lets_the_build_win(tmp_path, gradle_7):
    binary, java_home = gradle_7
    _project(tmp_path)
    script = tmp_path / "jacoco.gradle"
    script.write_text(gradle_init_script("0.8.5", explicit=True), encoding="utf-8")
    output = _gradle(binary, java_home, tmp_path, script)
    assert "ROOT 0.8.5" in output
    assert "PLAIN 0.8.5" in output
    assert "PINNED 0.8.7" in output
    assert "KTS 0.8.11" in output
