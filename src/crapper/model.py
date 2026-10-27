from dataclasses import dataclass


@dataclass(frozen=True)
class Function:
    """One scored unit: a function or method."""

    name: str
    namespace: str
    complexity: int
    start_line: int
    end_line: int
    path: str
    language: str
    jacoco_class: str | None = None
    # JVM method name when it differs from `name` (`@JvmName`).
    jacoco_name: str | None = None
    # Score used when the coverage report has no method for this function.
    # 0.0 keeps a real miss in the ranking. None means there is no class-file
    # method to measure, so the row stays N/A instead of a silent 0%.
    unmatched_coverage: float | None = 0.0
    # UTF-8 byte span of the function. -1 when the language does not record it.
    # Mutator uses the span to give a nested handler the sites inside it.
    start_byte: int = -1
    end_byte: int = -1


@dataclass(frozen=True)
class Entry:
    """One row of the CRAP report and of `.metrics/crap.edn`."""

    name: str
    namespace: str
    complexity: int
    coverage: float | None
    crap: float | None
    path: str
    language: str
