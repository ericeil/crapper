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
