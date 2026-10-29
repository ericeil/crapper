# crapper

CRAP scores for Clojure, Java, Kotlin, Go, TypeScript, Rust, and Python. One run detects the language of each source file, applies that language's complexity and coverage rules, and writes the snapshot [uml-viewer](https://github.com/unclebob/uml-viewer) already reads.

The formula is the one from [crap4clj](https://github.com/unclebob/crap4clj), [crap4java](https://github.com/unclebob/crap4java), and [crap4go](https://github.com/unclebob/crap4go):

```text
CRAP = CC² × (1 − coverage)³ + CC
```

`CC` is cyclomatic complexity. `coverage` is the fraction of the function exercised by tests. A score of 1–5 is low risk, 5–30 is worth a look, and 30+ is complex and under-tested.

## Run

```bash
./crapper
```

The first run creates `.venv` and installs the tool. From a project root it walks the tree, skips `test`, `spec`, `vendor`, `node_modules`, and `target`, and writes two results:

- a table on stdout, worst score first
- `.metrics/crap.edn`, replaced on every successful analysis

```bash
./crapper --no-coverage                  # complexity only; coverage is N/A
./crapper --use-existing-coverage        # read reports already on disk
./crapper src/demo/core.clj src/ui       # these files and trees
./crapper --changed                      # git additions and edits
./crapper combat                         # path fragment, same idea as crap4clj
./crapper --threshold 30                 # exit 2 when the worst score is higher
```

## Snapshot

`.metrics/crap.edn` has the same shape crap4clj writes:

```clojure
{:entries [{:name "place"
            :namespace "demo.Board"
            :complexity 3
            :coverage 75.0
            :crap 3.1}]}
```

uml-viewer groups entries by `:namespace` and joins each operation on `:name`. `nil` coverage is what `--no-coverage` writes: complexity only, and CRAP is `nil` too. A function the coverage report does not mention scores 0%. A Kotlin function that has no JaCoCo method to join (an inline function missing from the class file, a non-main source set that was not compiled into the report, or a class the report does not contain) is `nil` as well, so it is not ranked as 0%.

| Language | `:namespace` | `:name` |
| --- | --- | --- |
| Clojure | the `ns` | the `defn` / `defn-` name |
| Java | `package.Class`, or `package.Outer.Inner` | the method name |
| Kotlin | `package.Class`, `package.Outer.Inner`, or `package.Class.Companion`; `package.FileNameKt` (or the `@file:JvmName`) for a top-level function | the function name |
| Go | the package import path, or `import/path.Receiver` | the function or method name |
| TypeScript | the dotted module path, or `module.Class` | the function or method name |
| Rust | `crate::module`, or `crate::module::Type` | the function or method name |
| Python | the dotted module path, or `module.Class` | the function or method name |

Rename or move is a new entry. There is no identity matching across runs.

## What each language counts

Clojure follows crap4clj: `if` / `when` and their variants, `and`, `or`, `loop`, `catch`, and each clause of `cond`, `condp`, `case`, `cond->`, `cond->>`, `some->`, and `some->>`. Coverage prefers Cloverage's per-line form counts and falls back to LCOV.

Java follows crap4java: `if`, loops, `catch`, `?:`, each `switch` label, and `&&` / `||`. Constructors and methods inside anonymous classes are omitted. Coverage is JaCoCo's `INSTRUCTION` counter, from Maven, or from Gradle when the nearest build file is `build.gradle` or `build.gradle.kts`.

Kotlin (`.kt`) uses the same structural decisions as Java: `if`, loops, `catch`, each `when` entry (including `else`), and `&&` / `||`, plus `?:` and each `?.`. `!!` is not counted. Every `fun` with a body is an entry, including expression bodies, extension functions, functions in an `object` or `companion object`, and context-receiver functions (`context(T) fun` and `context(T) () -> R`). Local functions and lambdas stay inside the enclosing function. Secondary constructors, `init` blocks, and functions inside anonymous objects and local classes are omitted. Coverage is JaCoCo, from the same Maven or Gradle run as Java, joined on the class JaCoCo reports: `Outer$Inner`, `Class$Companion`, `Interface$DefaultImpls`, `Enum$ENTRY`, `FileNameKt`, or the `@file:JvmName` and its `@file:JvmMultifileClass` part. A backticked name joins on the identifier without backticks (`==`, `to?`), and a function-level `@JvmName` joins on that JVM name while the report still shows the source name. A mangled JVM name (`name$module` for `internal`, `name-hash` for inline-class parameters) still joins. An `inline` function the class file does not contain scores N/A, not 0%; an inline function that does have a method uses that method's coverage. Functions under `src/jmh` (and `src/benchmark`) score N/A unless that source set was compiled and its class is in the report. Gradle's `jmh` class directory is included when it exists. A class the report does not contain scores N/A. A file with a parse error is still scored, with a warning, since a function inside the error may score wrong. Before parsing, crapper blanks context receivers and turns an escaped `\$` in a regular string into spaces, so a script literal cannot swallow the enclosing class. A statement that starts with the identifier `where` gets a semicolon only when that removes a parse error; a `where T : Bound` clause is not rewritten, and a `where.foo` the grammar already accepts is left alone. `by < 0 ->` parses as a `when` entry. `while (i < out.size - 1)`, which the grammar reads as a type argument, is parenthesized only when that removes the error. If a class header survives but the body does not, functions keep that class name instead of the file facade.

Go follows crap4go: `if`, `for`, `range`, each `switch` and `select` clause, and `&&` / `||`. Coverage is `go test -coverprofile`.

TypeScript, TSX, and JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`) use the same structural decisions as Java, including `&&` inside JSX, plus `??` and `?.`. Top-level functions, class methods, and top-level arrow functions are entries. An inline Express callback — `.get`, `.post`, `.put`, `.patch`, `.delete`, `.head`, `.options`, `.all`, `.use`, and `.route(path).get(...)` — is its own entry, named `GET /users`. A second callback on that same route is `GET /users#2`. Its decisions are not also charged to the enclosing function. Other nested callbacks stay inside the enclosing function. Coverage is LCOV. When the report has branch records (`BRDA`) inside a function, the score uses those branches; a function with no branches uses line hits. A `coverage` script is used as-is. A Vitest project runs `vitest --coverage` (installing `@vitest/coverage-v8` into `node_modules` when it is missing, without editing `package.json`). Other test scripts are wrapped in `c8`.

Rust counts `if`, loops, each `match` arm, `?`, and `&&` / `||`. `mod tests` is skipped. Coverage is LCOV from `cargo llvm-cov` or `cargo tarpaulin`, run in the nearest directory that contains `Cargo.toml`. When neither tool is installed, the run installs `cargo-llvm-cov`.

Python counts `if`, `elif`, `for`, `while`, `except`, each `match` case, a comprehension filter, a conditional expression, and each `and` / `or`. Nested functions stay inside the enclosing function. Coverage is LCOV from `coverage.py`, running pytest when the project uses it and `unittest` otherwise. `coverage` and `pytest` are installed into the project's interpreter when they are missing.

## Coverage commands

By default a run deletes the previous report for each language it is about to measure and regenerates it:

| Language | Command | Report |
| --- | --- | --- |
| Clojure | `clj -M:cov --lcov` (needs `deps.edn` or `bb.edn`) | `target/coverage/` |
| Java, Kotlin | JaCoCo Maven plugin `0.8.12` in each module with `pom.xml`; otherwise `gradlew` or `gradle` with an init script that applies `jacoco` and selects JaCoCo `0.8.12` when Gradle's bundled version is older, then `test jacocoTestReport`. Every module's exec data is then applied to every module's main classes and to its JMH class directory when that source set was compiled, one XML file per class directory, so a test in one module can cover code in another | `target/site/jacoco/jacoco.xml`, `build/reports/jacoco/crapper/*.xml`, or `build/reports/jacoco/test/jacocoTestReport.xml` |
| Go | `go test ./... -coverprofile=...` | `target/coverage/go/coverage.out` |
| TypeScript | `npm run coverage`, or Vitest `--coverage`, or `npx c8 ... npm test` | `coverage/lcov.info` or `target/coverage/typescript/lcov.info` |
| Rust | `cargo llvm-cov` or `cargo tarpaulin`, per Cargo package | `target/coverage/rust/lcov.info` |
| Python | `coverage run` with pytest or unittest, then `coverage lcov` | `target/coverage/python/lcov.info` |

A missing tool scores that language at 0% and still writes the snapshot. A Gradle or Maven build that exits non-zero does the same when it leaves no JaCoCo XML. When that build fails and a report is on disk, for example because tests failed after JaCoCo wrote its data, the report is used. Pass `--coverage-command` to replace those defaults with one command of your own. `--no-coverage` is the run that leaves coverage and CRAP as N/A.

Gradle and Maven builds sometimes need an extra argument before JaCoCo will record anything. Repeat `--gradle-arg` or `--maven-arg` for each one. A value may start with `-`, and `--gradle-arg=-Dname` is the same as `--gradle-arg -Dname`. Gradle is started with `--continue`, so a failing test task in one module does not skip the remaining modules. Extra Gradle arguments are placed after `test` and before `jacocoTestReport`, because Gradle attaches a task option such as `--tests` to the task named just before it, and `jacocoTestReport` does not accept `--tests`. Maven arguments are appended after the JaCoCo goals. `-D` and `-P` properties apply either way:

```bash
./crapper --gradle-arg=-DJacoco
./crapper --gradle-arg=--tests --gradle-arg=com.example.HashTest
./crapper --maven-arg=-DskipITs
```

The Gradle init script applies JaCoCo before the build script runs. When `jacoco.toolVersion` is still the version bundled with that Gradle, and that version is older than `0.8.12`, the script sets `0.8.12`. Gradle 7's default JaCoCo (`0.8.6`) stops in `jacocoTestReport` on Kotlin 1.5+ class files (`Unexpected SMAP line: *S KotlinDebug`); `0.8.12` reads them. A newer JaCoCo already bundled with Gradle stays in place. A build that assigns `jacoco.toolVersion` keeps that assignment. `--jacoco-version` selects a different version for a build that has not assigned one, including when the bundled version is newer than the one you name:

```bash
./crapper --jacoco-version 0.8.13
```

The same lists, and the JaCoCo version, can live in `crapper.toml` at the project root. Command-line arguments are added after the file, so a one-off run extends the project's defaults. `--jacoco-version` replaces `jacoco-version` from the file. These settings are not passed through to `--coverage-command`. After the Gradle run, JaCoCo CLI `0.8.12` still merges every module's exec data into `build/reports/jacoco/crapper/*.xml`, including when `jacocoTestReport` fails and leaves execution data behind.

```toml
gradle-args = ["-DJacoco"]
maven-args = ["-DskipITs"]
jacoco-version = "0.8.13"
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
