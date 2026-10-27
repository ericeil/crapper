from pathlib import Path

from crapper.analyze import analyze_files
from crapper.coverage import CoverageBundle, parse_jacoco_index
from crapper.languages.kotlin import file_class, functions_in_source

SCORE = """
package demo

class Sample {
    fun score(a: Boolean, b: Boolean, values: List<Int>, name: String?): Int {
        for (value in values) {
        }
        while (a) {
        }
        do {
        } while (b)
        if (a && b || values.isEmpty()) {
        }
        val size = name?.length ?: 0
        return try {
            when (size) {
                0 -> 1
                1, 2 -> 2
                else -> 3
            }
        } catch (ex: RuntimeException) {
            4
        }
    }
}
"""

NESTED = """
package demo

class Sample {
    fun outer(a: Boolean): Int {
        val callback = { if (a) 1 else 2 }
        fun local(b: Boolean) = if (b) 1 else 0
        val runnable = object : Runnable {
            override fun run() {
                if (a) {
                }
            }
        }
        class Local {
            fun inner() = if (a) 1 else 0
        }
        return callback() + local(a)
    }
}
"""


def _rows(source: str, path: str = "src/demo/Sample.kt"):
    return [
        (function.namespace, function.name, function.complexity)
        for function in functions_in_source(source, path)
    ]


def test_counts_branches_when_entries_and_null_safe_operators():
    # for, while, do, if, &&, ||, ?., ?:, three when entries, catch.
    assert _rows(SCORE) == [("demo.Sample", "score", 13)]


def test_lambdas_and_local_functions_stay_in_the_enclosing_function():
    # The lambda's `if` and the local function's `if`; not the anonymous
    # object's or the local class's.
    assert _rows(NESTED) == [("demo.Sample", "outer", 3)]


def test_names_follow_the_classes_jacoco_reports():
    source = """
package demo.pkg

fun top(x: Int?) = x ?: 0

fun String.shout(): String = uppercase()

class Board {
    fun place(x: Int) {
        if (x > 0) return
    }

    inner class Inner {
        fun tick() {}
    }

    companion object {
        fun create() = Board()
    }

    object Named {
        fun get() = 1
    }

    abstract fun skipped()

    constructor(x: Int) {
        if (x > 0) {}
    }

    init {
        if (true) {}
    }
}

object Registry {
    fun lookup() = 1
}
"""
    functions = functions_in_source(source, "src/demo/pkg/board-utils.kt")
    assert [(f.namespace, f.name, f.jacoco_class) for f in functions] == [
        ("demo.pkg.Board_utilsKt", "top", "demo.pkg.Board_utilsKt"),
        ("demo.pkg.Board_utilsKt", "shout", "demo.pkg.Board_utilsKt"),
        ("demo.pkg.Board", "place", "demo.pkg.Board"),
        ("demo.pkg.Board.Inner", "tick", "demo.pkg.Board$Inner"),
        ("demo.pkg.Board.Companion", "create", "demo.pkg.Board$Companion"),
        ("demo.pkg.Board.Named", "get", "demo.pkg.Board$Named"),
        ("demo.pkg.Registry", "lookup", "demo.pkg.Registry"),
    ]
    assert [f.complexity for f in functions] == [2, 1, 2, 1, 1, 1, 1]
    assert functions[2].start_line == 9
    assert functions[2].end_line == 11
    assert functions[2].language == "kotlin"


def test_interface_defaults_named_companions_and_enum_entries():
    source = """
package demo

interface Shape {
    fun area(): Double = 0.0

    companion object Factory {
        fun unit() = 1
    }
}

enum class Mode {
    FAST {
        override fun speed() = 2
    },
    SLOW;

    open fun speed() = 1
}
"""
    functions = functions_in_source(source, "src/demo/Shape.kt")
    assert [(f.namespace, f.name, f.jacoco_class) for f in functions] == [
        ("demo.Shape", "area", "demo.Shape$DefaultImpls"),
        ("demo.Shape.Factory", "unit", "demo.Shape$Factory"),
        ("demo.Mode", "speed", "demo.Mode$FAST"),
        ("demo.Mode", "speed", "demo.Mode"),
    ]


def test_file_class_follows_jvm_name_and_multifile_parts():
    assert file_class("src/util.kt", "") == ("UtilKt", "UtilKt")
    assert file_class("src/9lives.kt", "") == ("_9livesKt", "_9livesKt")
    named = '@file:JvmName("Strings")\npackage demo\n'
    assert file_class("src/util.kt", named) == ("Strings", "Strings")
    multi = '@file:JvmName("Strings")\n@file:JvmMultifileClass\npackage demo\n'
    assert file_class("src/util.kt", multi) == ("Strings", "Strings__UtilKt")


def test_top_level_function_in_the_default_package():
    functions = functions_in_source("fun main() {}\n", "Main.kt")
    assert [(f.namespace, f.name, f.jacoco_class) for f in functions] == [
        ("MainKt", "main", "MainKt")
    ]


def test_context_receiver_functions_are_scored(capsys):
    source = """
package demo

context(Foo)
fun choose(a: Boolean, ready: Boolean) = if (a && ready) 1 else 0

context(SimpleDominanceAnalysis<NBId>)
@JvmName("dominates2")
infix fun LTACCmd.dominates(y: LTACCmd): Boolean {
    if (y == this) return true
    return false
}

fun plain() = context(1) + 1
"""
    functions = functions_in_source(source, "src/demo/Receivers.kt")
    assert [(f.name, f.namespace, f.complexity, f.start_line) for f in functions] == [
        ("choose", "demo.ReceiversKt", 3, 5),
        ("dominates", "demo.ReceiversKt", 2, 8),
        ("plain", "demo.ReceiversKt", 1, 14),
    ]
    assert capsys.readouterr().err == ""


def test_context_function_types_do_not_swallow_the_object(capsys):
    source = """
package demo

object Box {
    fun <T> use(f: context(Foo)() -> T): Int {
        if (f == null) return 0
        return 1
    }

    fun other(a: Boolean) = if (a) 1 else 2

    fun suspendable(f: context(Foo) suspend () -> Int) = if (f == null) 0 else 1
}
"""
    functions = functions_in_source(source, "src/demo/Box.kt")
    assert [(f.namespace, f.name, f.complexity) for f in functions] == [
        ("demo.Box", "use", 2),
        ("demo.Box", "other", 2),
        ("demo.Box", "suspendable", 2),
    ]
    assert capsys.readouterr().err == ""


def test_a_call_named_context_is_not_a_receiver(capsys):
    source = """
package demo

class Tokens {
    fun exp(): Int {
        return listOf(1).context(0)
    }

    private fun param(a: Boolean): Int {
        if (a) return 1
        return 0
    }

    private fun List<Int>.context(ctx: Int): Int {
        if (ctx > 0 && isNotEmpty()) return this[0]
        return 0
    }
}

fun caller(): Int {
    val x = context(1)
    return x
}
"""
    functions = functions_in_source(source, "src/demo/Tokens.kt")
    assert [(f.namespace, f.name, f.complexity) for f in functions] == [
        ("demo.Tokens", "exp", 1),
        ("demo.Tokens", "param", 2),
        ("demo.Tokens", "context", 3),
        ("demo.TokensKt", "caller", 1),
    ]
    assert capsys.readouterr().err == ""


def test_context_in_strings_and_comments_is_not_a_function(capsys):
    source = """
package demo

val note = "context(Foo) fun hidden() { if (true) 1 else 0 }"
// context(Foo) fun commented() { if (true) 1 else 0 }
/*
context(Foo)
fun commentedToo() { if (true) {} }
*/

fun real(a: Boolean) = if (a) 1 else 0
"""
    functions = functions_in_source(source, "src/demo/Notes.kt")
    assert [(f.name, f.complexity) for f in functions] == [("real", 2)]
    assert capsys.readouterr().err == ""


def test_a_file_with_parse_errors_warns(capsys):
    functions_in_source("package demo\n\nfun ok() {}\n", "src/Ok.kt")
    assert capsys.readouterr().err == ""
    functions_in_source(
        "class Board {\n    fun tick() {}\n    companion object { fun create() = Board() }\n}\n",
        "src/Board.kt",
    )
    assert capsys.readouterr().err == ""
    functions_in_source("package demo\n\nclass Broken {\n    fun f( {\n}\n", "src/Broken.kt")
    assert capsys.readouterr().err == (
        "src/Broken.kt: Kotlin parse error; scores in this file may be wrong.\n"
    )


def test_backticked_operators_use_the_jvm_name():
    source = """
package demo

class Ops {
    operator fun `==`(other: Any?) = this === other
    infix fun <T> T.`to?`(v: T?) = v
    fun `return`() = 1
}
"""
    functions = functions_in_source(source, "src/demo/Ops.kt")
    assert [(f.name, f.jacoco_name, f.namespace) for f in functions] == [
        ("==", None, "demo.Ops"),
        ("to?", None, "demo.Ops"),
        ("return", None, "demo.Ops"),
    ]


def test_function_jvm_name_is_the_lookup_name_and_inline_has_no_zero():
    source = """
package demo

@JvmName("getLhs1")
fun getLhs(): Int {
    if (true) return 1
    return 0
}

inline fun <reified T> enumSetOf(x: T) = x

@JvmName("deserializeFileExt")
inline fun <reified T> String.deserializeFile(): T = x
"""
    functions = functions_in_source(source, "src/demo/Names.kt")
    assert [(f.name, f.jacoco_name, f.unmatched_coverage) for f in functions] == [
        ("getLhs", "getLhs1", 0.0),
        ("enumSetOf", None, None),
        ("deserializeFile", "deserializeFileExt", None),
    ]


def test_jmh_source_set_has_no_coverage_unless_the_class_was_compiled(tmp_path):
    path = tmp_path / "lib" / "src" / "jmh" / "kotlin" / "datastructures" / "MapBenchmark.kt"
    path.parent.mkdir(parents=True)
    path.write_text(
        "package datastructures\n\nfun pickObj(): Int {\n    if (true) return 1\n    return 0\n}\n",
        encoding="utf-8",
    )
    missing = analyze_files([path], tmp_path, CoverageBundle(jacoco={}))
    assert missing[0].name == "pickObj"
    assert missing[0].coverage is None
    present = analyze_files(
        [path],
        tmp_path,
        CoverageBundle(
            jacoco=parse_jacoco_index(
                """
<report>
  <package name="datastructures">
    <class name="datastructures/MapBenchmarkKt">
      <method name="pickObj" line="3">
        <counter type="INSTRUCTION" missed="1" covered="3"/>
      </method>
    </class>
  </package>
</report>
"""
            )
        ),
    )
    assert present[0].coverage == 75.0


def test_where_identifier_escaped_dollar_and_while_comparison_parse(capsys):
    source = """
package demo

fun <T> keep(x: T): T where T : Any {
    if (x == null) return x
    return x
}

fun rewrite(where: List<Int>): Int {
    if (where.none { it > 0 }) {
        return@flatMap Stream.empty<Pair<A, B>>()
    }
    where.mapIndexedNotNull { index, simple ->
        if (simple > 0) index else null
    }
    return where.size
}

fun pairs(): Pair<String, Int> {
    val where = "block"
    return where to 1
}

fun sym(name: String, args: List<String>) =
    "${name}\\$${args.joinToString("\\$$") { it }}"

fun merge(out: MutableList<Int>): Int {
    var i = 0
    while (i < out.size - 1) {
        if (i > 0) return i
        i++
    }
    return i
}

fun shift(by: Int) = when {
    by < 0 -> 1
    by > 0 -> 2
    else -> 0
}
"""
    functions = functions_in_source(source, "src/demo/Repairs.kt")
    by_name = {f.name: f for f in functions}
    assert set(by_name) == {"keep", "rewrite", "pairs", "sym", "merge", "shift"}
    assert by_name["keep"].complexity == 2
    assert by_name["rewrite"].complexity == 3
    assert by_name["merge"].complexity == 3
    assert by_name["shift"].complexity == 4
    assert by_name["sym"].start_line == 24
    assert capsys.readouterr().err == ""


def _fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_a_broken_class_keeps_the_class_name(capsys):
    # The string never closes, so the class is an ERROR node. The header
    # (`class`, the name, `{`) is still there, and the function keeps that name
    # instead of the file facade.
    functions = functions_in_source(_fixture("broken_class.kt"), "src/demo/Foo.kt")
    assert ("before", "demo.Foo") in [(f.name, f.namespace) for f in functions]
    assert "demo.FooKt" not in {f.namespace for f in functions if f.name == "before"}
    assert "parse error" in capsys.readouterr().err


def test_an_escaped_dollar_does_not_drop_the_class(capsys):
    functions = functions_in_source(_fixture("dollar_class.kt"), "src/demo/Foo.kt")
    assert [(f.name, f.namespace) for f in functions] == [
        ("before", "demo.Foo"),
        ("after", "demo.Foo"),
        ("top", "demo.FooKt"),
    ]
    assert capsys.readouterr().err == ""


def test_inline_miss_is_na_and_a_missing_method_on_a_known_class_is_zero(tmp_path):
    path = tmp_path / "src" / "main" / "kotlin" / "demo" / "Sets.kt"
    path.parent.mkdir(parents=True)
    path.write_text(
        "package demo\n"
        "inline fun <reified T> enumSetOf(x: T) = x\n"
        "fun plain(): Int {\n    if (true) return 1\n    return 0\n}\n"
        "class Ghost {\n    fun gone() = 1\n}\n",
        encoding="utf-8",
    )
    report = """
<report>
  <package name="demo">
    <class name="demo/SetsKt">
      <method name="plain" line="3">
        <counter type="INSTRUCTION" missed="4" covered="0"/>
      </method>
    </class>
  </package>
</report>
"""
    entries = analyze_files([path], tmp_path, CoverageBundle(jacoco=parse_jacoco_index(report)))
    by_name = {entry.name: entry for entry in entries}
    assert by_name["enumSetOf"].coverage is None
    assert by_name["enumSetOf"].crap is None
    assert by_name["plain"].coverage == 0.0
    assert by_name["gone"].coverage is None
    joined = analyze_files(
        [path],
        tmp_path,
        CoverageBundle(
            jacoco=parse_jacoco_index(
                """
<report>
  <package name="demo">
    <class name="demo/SetsKt">
      <method name="enumSetOf" line="2">
        <counter type="INSTRUCTION" missed="0" covered="2"/>
      </method>
      <method name="getLhs1" line="3">
        <counter type="INSTRUCTION" missed="1" covered="1"/>
      </method>
    </class>
    <class name="demo/Ops">
      <method name="==" line="2">
        <counter type="INSTRUCTION" missed="0" covered="4"/>
      </method>
    </class>
  </package>
</report>
"""
            )
        ),
    )
    assert {entry.name: entry.coverage for entry in joined}["enumSetOf"] == 100.0
