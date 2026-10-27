"""Kotlin functions and cyclomatic complexity, following java.py.

Every `fun` with a body is an entry: top-level, member, extension, and inside
`object` / `companion object`. Local functions and lambdas stay inside the
enclosing function. Secondary constructors and `init` blocks are omitted, as
Java omits constructors.

`:namespace` is the class JaCoCo reports, written with dots
(`pkg.Outer.Inner`, `pkg.Class.Companion`), and `pkg.FileNameKt` (or the
`@file:JvmName`) for a top-level function. The binary name (`pkg.Outer$Inner`)
is kept for coverage lookup.

tree-sitter-kotlin does not know context receivers. A clause such as
`context(Foo)` before `fun` / `val`, or a function type `context(Foo) () -> R`,
is blanked (same length, so lines do not move) before parsing. Without that,
the grammar treats `object Name { ... }` as an expression and drops every
function in the object.

A few other constructs are repaired the same way, and only when the rewrite
cannot move a line or swallow a real `where` clause:

- `\\$` inside a regular string is an escaped dollar. The grammar reads it as
  interpolation and can drop the rest of the file, so the two characters
  become spaces.
- A statement that starts with the identifier `where` (``where.foo`` or
  ``where to x``) gets a semicolon in the indent when that line is already a
  parse error and the semicolon removes it. A real `where T : Bound` clause
  is left alone, as is a `where.foo` the grammar already accepts. `by < 0 ->`
  already parses as a `when` entry.
- `while (i < out.size - 1)` is parsed as a type argument. The member is
  parenthesized (`i<(out).size`) only when that removes a parse error.
"""

import re
import sys
from pathlib import Path

from crapper.languages.treesitter import (
    child_of_type,
    complexity,
    end_line,
    node_text,
    parse,
    start_line,
)
from crapper.model import Function

_JVM_NAME = re.compile(r'@file\s*:\s*JvmName\s*\(\s*"([^"]+)"\s*\)')
_MULTIFILE = re.compile(r"@file\s*:\s*JvmMultifileClass\b")
_TYPES = {"class_declaration", "object_declaration", "companion_object"}
# Walked to find entries. Anything else, such as a property initializer or a
# lambda, belongs to a function or is not a function at all.
_CONTAINERS = _TYPES | {
    "source_file",
    "class_body",
    "enum_class_body",
    "enum_entry",
    "ERROR",
}
_OWNED_ELSEWHERE = _TYPES | {"class_body", "enum_class_body", "object_literal"}
# Words that may sit between a context-receiver clause and the declaration.
_MODIFIERS = {
    "public",
    "private",
    "protected",
    "internal",
    "override",
    "open",
    "abstract",
    "final",
    "suspend",
    "inline",
    "tailrec",
    "operator",
    "infix",
    "external",
    "expect",
    "actual",
    "inner",
    "annotation",
    "crossinline",
    "noinline",
}
_DECLARATIONS = {"fun", "val", "var", "get", "set"}

_DECISIONS = {
    "if_expression",
    "for_statement",
    "while_statement",
    "do_while_statement",
    "catch_block",
    "when_entry",
    "conjunction_expression",
    "disjunction_expression",
    "elvis_expression",
    "?.",
}


def _is_decision(node) -> bool:
    return node.type in _DECISIONS


def _skip(node) -> bool:
    return node.type in _OWNED_ELSEWHERE


def _package_name(data: bytes, root) -> str | None:
    header = child_of_type(root, "package_header")
    if header is None:
        return None
    ident = child_of_type(header, "identifier")
    if ident is None:
        return None
    return ".".join(
        node_text(data, part) for part in ident.children if part.type == "simple_identifier"
    )


def file_class(path: str, source: str) -> tuple[str, str]:
    """The facade class for top-level functions, and the class JaCoCo reports.

    A `@file:JvmMultifileClass` file compiles its code into `Facade__FileKt`.
    """

    stem = re.sub(r"[^\w$]", "_", Path(path).stem)
    if stem[:1].isdigit():
        stem = "_" + stem
    part = stem[:1].upper() + stem[1:] + "Kt"
    named = _JVM_NAME.search(source)
    if named is None:
        return part, part
    if _MULTIFILE.search(source):
        return named.group(1), f"{named.group(1)}__{part}"
    return named.group(1), named.group(1)


def _type_name(data: bytes, node) -> str:
    ident = child_of_type(node, "type_identifier")
    if ident is not None:
        return node_text(data, ident)
    return "Companion"


def _is_interface(node) -> bool:
    return (
        node is not None
        and node.type == "class_declaration"
        and child_of_type(node, "interface") is not None
    )


def _enum_entry_name(data: bytes, node) -> str | None:
    if node.type != "enum_entry":
        return None
    ident = child_of_type(node, "simple_identifier")
    return node_text(data, ident) if ident is not None else None


def _type_chain(data: bytes, node) -> tuple[list[str], list[str]]:
    """Source names for `:namespace` and binary names for JaCoCo.

    An enum entry with a body is a class of its own to JaCoCo (`E$A`) but not
    to uml-viewer, and a default method in an interface lives in
    `I$DefaultImpls`.
    """

    names: list[str] = []
    binary: list[str] = []
    current = node.parent
    if current is not None and _is_interface(current.parent):
        binary.append("DefaultImpls")
    while current is not None:
        if current.type in _TYPES:
            names.append(_type_name(data, current))
            binary.append(names[-1])
        entry = _enum_entry_name(data, current)
        if entry is not None:
            binary.append(entry)
        current = current.parent
    names.reverse()
    binary.reverse()
    return names, binary


def _qualify(package: str | None, names: list[str], nested: str) -> str:
    body = nested.join(names)
    return f"{package}.{body}" if package else body


def _blank_context_receivers(source: str) -> str:
    """Replace context-receiver clauses with spaces of the same length.

    Newlines stay, so a function's line numbers are the lines in the file.
    Strings and comments are left alone, including a call named `context`.
    """

    if "context" not in source:
        return source
    chars = list(source)
    i = 0
    while i < len(chars):
        i = _scan_context(chars, i)
    return "".join(chars)


def _scan_context(chars: list[str], i: int) -> int:
    skipped = _skip_trivia(chars, i)
    if skipped != i:
        return skipped
    if _is_word(chars, i, "context"):
        keyword_end = i + len("context")
        open_paren = _skip_ws(chars, keyword_end)
        if open_paren < len(chars) and chars[open_paren] == "(":
            close_paren = _match_paren(chars, open_paren)
            if close_paren is not None and _introduces_receiver(chars, i, close_paren + 1):
                _blank(chars, i, close_paren)
                return close_paren + 1
    return i + 1


def _introduces_receiver(chars: list[str], keyword_at: int, i: int) -> bool:
    """True for `context(T) fun` and for a function type `context(T) () -> R`.

    A call named `context` is left alone. `.context(ctx)` is a method, and
    `val x = context(1)` is a call even when a `fun` follows it.
    """

    if _is_function_type(chars, i):
        return True
    if not _at_declaration_position(chars, keyword_at):
        return False
    i = _skip_declaration_prefix(chars, i)
    if i >= len(chars):
        return False
    word, _end = _read_word(chars, i)
    return word in _DECLARATIONS


def _is_function_type(chars: list[str], i: int) -> bool:
    i = _skip_declaration_prefix(chars, i)
    if i >= len(chars) or chars[i] != "(":
        return False
    close_paren = _match_paren(chars, i)
    if close_paren is None:
        return False
    arrow = _skip_ws(chars, close_paren + 1)
    return arrow + 1 < len(chars) and chars[arrow] == "-" and chars[arrow + 1] == ">"


def _at_declaration_position(chars: list[str], i: int) -> bool:
    """True when `context` starts a declaration, not an expression."""

    line_start = i
    while line_start > 0 and chars[line_start - 1] not in "\n\r":
        line_start -= 1
    cursor = line_start
    while cursor < i:
        while cursor < i and chars[cursor] in " \t":
            cursor += 1
        if cursor >= i:
            return True
        if chars[cursor] == "@":
            end = _skip_annotation(chars, cursor)
            if end <= cursor:
                return False
            cursor = end
            continue
        word, end = _read_word(chars, cursor)
        if word in _MODIFIERS and end <= i:
            cursor = end
            continue
        return False
    return True


def _skip_declaration_prefix(chars: list[str], i: int) -> int:
    while True:
        i = _skip_ws(chars, i)
        if i >= len(chars):
            return i
        if chars[i] == "@":
            i = _skip_annotation(chars, i)
            continue
        word, end = _read_word(chars, i)
        if word not in _MODIFIERS:
            return i
        i = end


def _skip_annotation(chars: list[str], i: int) -> int:
    i += 1
    if i < len(chars) and chars[i] == "[":
        close = _match_closer(chars, i, "[", "]")
        return i if close is None else close + 1
    while i < len(chars) and (chars[i].isalnum() or chars[i] in "._:"):
        i += 1
    i = _skip_ws(chars, i)
    if i < len(chars) and chars[i] == "(":
        close = _match_paren(chars, i)
        return i if close is None else close + 1
    return i


def _skip_trivia(chars: list[str], i: int) -> int:
    if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "/":
        return _skip_line_comment(chars, i)
    if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "*":
        return _skip_block_comment(chars, i)
    if chars[i] == '"':
        return _skip_string(chars, i)
    if chars[i] == "'":
        return _skip_char(chars, i)
    return i


def _skip_line_comment(chars: list[str], i: int) -> int:
    newline = _find_char(chars, "\n", i)
    return len(chars) if newline is None else newline


def _skip_block_comment(chars: list[str], i: int) -> int:
    i += 2
    depth = 1
    while i < len(chars) and depth:
        if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "*":
            depth += 1
            i += 2
            continue
        if i + 1 < len(chars) and chars[i] == "*" and chars[i + 1] == "/":
            depth -= 1
            i += 2
            continue
        i += 1
    return i


def _skip_string(chars: list[str], i: int) -> int:
    if _starts_with(chars, i, '"""'):
        return _skip_raw_string(chars, i)
    i += 1
    while i < len(chars):
        if chars[i] == "\\":
            i += 2
            continue
        if chars[i] == "$":
            i = _skip_interpolation(chars, i)
            continue
        if chars[i] == '"':
            return i + 1
        if chars[i] == "\n":
            return i
        i += 1
    return i


def _skip_raw_string(chars: list[str], i: int) -> int:
    i += 3
    while i < len(chars):
        if _starts_with(chars, i, '"""'):
            return i + 3
        if chars[i] == "$":
            i = _skip_interpolation(chars, i)
            continue
        i += 1
    return i


def _skip_interpolation(chars: list[str], i: int) -> int:
    if i + 1 < len(chars) and chars[i + 1] == "{":
        close = _match_closer(chars, i + 1, "{", "}")
        return i + 1 if close is None else close + 1
    if i + 1 < len(chars) and (chars[i + 1].isalpha() or chars[i + 1] == "_"):
        i += 2
        while i < len(chars) and (chars[i].isalnum() or chars[i] == "_"):
            i += 1
        return i
    return i + 1


def _skip_char(chars: list[str], i: int) -> int:
    if i + 2 < len(chars) and chars[i + 1] == "\\" and chars[i + 2] == "'":
        return i + 4 if i + 3 < len(chars) and chars[i + 3] == "'" else i + 1
    if i + 2 < len(chars) and chars[i + 1] != "'" and chars[i + 1] != "\n" and chars[i + 2] == "'":
        return i + 3
    if i + 1 < len(chars) and chars[i + 1] == "\\":
        end = i + 2
        while end < len(chars) and chars[end] != "'" and chars[end] != "\n":
            end += 1
        return end + 1 if end < len(chars) and chars[end] == "'" else i + 1
    return i + 1


def _match_paren(chars: list[str], i: int) -> int | None:
    return _match_closer(chars, i, "(", ")")


def _match_closer(chars: list[str], i: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    while i < len(chars):
        skipped = _skip_trivia(chars, i)
        if skipped != i:
            i = skipped
            continue
        if chars[i] == open_char:
            depth += 1
        elif chars[i] == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _skip_ws(chars: list[str], i: int) -> int:
    while i < len(chars) and chars[i] in " \t\r\n":
        i += 1
    return i


def _read_word(chars: list[str], i: int) -> tuple[str, int]:
    if i >= len(chars) or not (chars[i].isalpha() or chars[i] == "_"):
        return "", i
    end = i + 1
    while end < len(chars) and (chars[end].isalnum() or chars[end] == "_"):
        end += 1
    return "".join(chars[i:end]), end


def _is_word(chars: list[str], i: int, word: str) -> bool:
    if not _starts_with(chars, i, word):
        return False
    before_ok = i == 0 or not (chars[i - 1].isalnum() or chars[i - 1] == "_")
    after = i + len(word)
    after_ok = after >= len(chars) or not (chars[after].isalnum() or chars[after] == "_")
    return before_ok and after_ok


def _starts_with(chars: list[str], i: int, text: str) -> bool:
    return chars[i : i + len(text)] == list(text)


def _find_char(chars: list[str], needle: str, i: int) -> int | None:
    while i < len(chars):
        if chars[i] == needle:
            return i
        i += 1
    return None


def _blank(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end + 1):
        if chars[index] not in "\n\r":
            chars[index] = " "


# Source sets whose classes are not in the main JaCoCo output. A function
# here has no coverage number unless that source set was compiled and the
# report actually contains its class.
_NON_MAIN_SOURCE_SETS = {"jmh", "benchmark", "benchmarks"}
_HEADER_NOISE = {
    "primary_constructor",
    "modifiers",
    "type_parameters",
    "delegation_specifier",
    "type_constraints",
    "user_type",
    "nullable_type",
    "function_type",
    "line_comment",
    "multiline_comment",
    ":",
}


def _non_main_source(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if "src" not in parts:
        return False
    index = parts.index("src")
    return index + 1 < len(parts) - 1 and parts[index + 1] in _NON_MAIN_SOURCE_SETS


def _bare_identifier(text: str) -> str:
    """Drop the backticks around an escaped identifier. The JVM name has none."""

    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return text[1:-1]
    return text


def _prepare_kotlin(source: str) -> str:
    """Blank the constructs the grammar misreads, without moving lines."""

    if "context" not in source and "\\$" not in source:
        return source
    chars = list(source)
    if "\\$" in source:
        _blank_escaped_dollars(chars)
    text = "".join(chars)
    if "context" in text:
        text = _blank_context_receivers(text)
    return text


def _blank_escaped_dollars(chars: list[str]) -> None:
    """Turn `\\$` in a regular string into spaces.

    In a regular string `\\$` is a literal dollar. The grammar treats `$` as
    the start of interpolation, and a long string full of escaped dollars
    (a script embedded in Kotlin) then swallows the rest of the file.
    Raw strings do not use backslash escapes, so they are left alone.
    """

    i = 0
    while i < len(chars):
        skipped = _skip_trivia_except_string(chars, i)
        if skipped != i:
            i = skipped
            continue
        if chars[i] == '"':
            if _starts_with(chars, i, '"""'):
                i = _skip_raw_string(chars, i)
                continue
            i = _blank_dollars_in_string(chars, i)
            continue
        i += 1


def _skip_trivia_except_string(chars: list[str], i: int) -> int:
    if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "/":
        return _skip_line_comment(chars, i)
    if i + 1 < len(chars) and chars[i] == "/" and chars[i + 1] == "*":
        return _skip_block_comment(chars, i)
    if chars[i] == "'":
        return _skip_char(chars, i)
    return i


def _blank_dollars_in_string(chars: list[str], i: int) -> int:
    i += 1
    while i < len(chars):
        if chars[i] == "\\":
            if i + 1 < len(chars) and chars[i + 1] == "$":
                chars[i] = " "
                chars[i + 1] = " "
            i += 2
            continue
        if chars[i] == "$":
            i = _skip_interpolation(chars, i)
            continue
        if chars[i] == '"':
            return i + 1
        if chars[i] == "\n":
            return i
        i += 1
    return i


def _separate_where_statements(chars: list[str], spans: list[tuple[int, int]]) -> bool:
    """Put a semicolon before a statement that starts with the identifier `where`.

    `where` is also the type-constraint keyword. At the start of a line the
    grammar can keep it as a constraint and then fail on `where.foo` or
    `where to x`. A constraint is `where T : Bound`, which is not rewritten.
    The semicolon is inserted only on a line that already has a parse error,
    so a `where.foo` the grammar already accepts is left alone.
    """

    changed = False
    i = 0
    line_start = 0
    while i < len(chars):
        skipped = _skip_trivia(chars, i)
        if skipped != i:
            line_start = _line_start_after(chars, i, skipped, line_start)
            i = skipped
            continue
        if chars[i] == "\n":
            line_start = i + 1
            i += 1
            continue
        if _is_word(chars, i, "where") and _only_indent(chars, line_start, i):
            line_end = _find_char(chars, "\n", i)
            line_end = len(chars) - 1 if line_end is None else line_end
            if (
                _where_is_a_statement(chars, i + len("where"))
                and i > line_start
                and _span_overlaps(chars, line_start, line_end, spans)
            ):
                chars[i - 1] = ";"
                changed = True
            i += len("where")
            continue
        i += 1
    return changed


def _repair_where_statements(source: str, tree) -> str | None:
    spans = _error_byte_spans(tree)
    if not spans or "where" not in source:
        return None
    chars = list(source)
    if not _separate_where_statements(chars, spans):
        return None
    repaired = "".join(chars)
    _data, repaired_tree = parse(repaired, "kotlin")
    if _error_count(repaired_tree) < _error_count(tree):
        return repaired
    return None


def _line_start_after(chars: list[str], start: int, end: int, line_start: int) -> int:
    last = line_start
    for index in range(start, end):
        if chars[index] == "\n":
            last = index + 1
    return last


def _only_indent(chars: list[str], start: int, end: int) -> bool:
    return all(chars[index] in " \t" for index in range(start, end))


def _where_is_a_statement(chars: list[str], i: int) -> bool:
    nxt = _skip_ws(chars, i)
    if nxt < len(chars) and chars[nxt] == ".":
        return True
    if not _is_word(chars, nxt, "to"):
        return False
    after = _skip_ws(chars, nxt + len("to"))
    return after >= len(chars) or chars[after] != ":"


def _error_count(tree) -> int:
    count = 0
    stack = [tree.root_node]
    while stack:
        current = stack.pop()
        if current.type == "ERROR":
            count += 1
        stack.extend(current.children)
    return count


def _error_byte_spans(tree) -> list[tuple[int, int]]:
    spans = []
    stack = [tree.root_node]
    while stack:
        current = stack.pop()
        if current.type == "ERROR":
            spans.append((current.start_byte, current.end_byte))
        stack.extend(current.children)
    return spans


def _repair_while_comparisons(source: str, tree) -> str | None:
    """Parenthesize `while (i < out.size ...)` when the grammar calls `<` a type argument.

    The rewrite is kept only when it removes an ERROR node. A comparison that
    already parses, including `by < 0 ->`, is not changed.
    """

    spans = _error_byte_spans(tree)
    if not spans or "while" not in source:
        return None
    chars = list(source)
    changed = False
    i = 0
    while i < len(chars):
        skipped = _skip_trivia(chars, i)
        if skipped != i:
            i = skipped
            continue
        if _is_word(chars, i, "while"):
            open_paren = _skip_ws(chars, i + len("while"))
            if open_paren < len(chars) and chars[open_paren] == "(":
                close = _match_paren(chars, open_paren)
                if close is not None and _span_overlaps(chars, i, close, spans):
                    if _rewrite_while_condition(chars, open_paren + 1, close):
                        changed = True
                i = (close + 1) if close is not None else open_paren + 1
                continue
        i += 1
    if not changed:
        return None
    repaired = "".join(chars)
    _data, repaired_tree = parse(repaired, "kotlin")
    if _error_count(repaired_tree) < _error_count(tree):
        return repaired
    return None


def _span_overlaps(chars: list[str], start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    start_byte = len("".join(chars[:start]).encode("utf-8"))
    end_byte = start_byte + len("".join(chars[start : end + 1]).encode("utf-8"))
    return any(span_start < end_byte and span_end > start_byte for span_start, span_end in spans)


def _rewrite_while_condition(chars: list[str], start: int, end: int) -> bool:
    """Replace ` < name.` with `<(name).` inside one while condition.

    Both sides of `<` are spaces in the source this repairs, and the
    replacement has the same length, so later lines stay put.
    """

    changed = False
    i = start
    while i < end:
        skipped = _skip_trivia(chars, i)
        if skipped != i:
            i = skipped
            continue
        if (
            chars[i] == "<"
            and i > start
            and chars[i - 1] == " "
            and i + 1 < end
            and chars[i + 1] == " "
        ):
            name_end = _identifier_end(chars, i + 2, end)
            if name_end is not None and name_end < end and chars[name_end] == ".":
                ident = "".join(chars[i + 2 : name_end])
                replacement = "<(" + ident + ")"
                chars[i - 1 : name_end] = list(replacement)
                changed = True
                i = i - 1 + len(replacement)
                continue
        i += 1
    return changed


def _identifier_end(chars: list[str], i: int, end: int) -> int | None:
    if i >= end or not (chars[i].isalpha() or chars[i] == "_"):
        return None
    while i + 1 < end and (chars[i + 1].isalnum() or chars[i + 1] == "_"):
        i += 1
    return i + 1


def _real_parse_error(node) -> bool:
    """True for an ERROR node, not the grammar's missing automatic semicolon.

    tree-sitter-kotlin marks some valid one-line members as
    `MISSING _automatic_semicolon`. That sets `has_error` without an ERROR
    node, and the functions are still in the tree.
    """

    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "ERROR" or current.is_error:
            return True
        stack.extend(current.children)
    return False


def _warn_parse_errors(path: str, tree) -> None:
    if _real_parse_error(tree.root_node):
        print(
            f"{path}: Kotlin parse error; scores in this file may be wrong.",
            file=sys.stderr,
        )


def _is_inline(node) -> bool:
    modifiers = child_of_type(node, "modifiers")
    if modifiers is None:
        return False
    for child in modifiers.children:
        if child.type == "function_modifier" and any(part.type == "inline" for part in child.children):
            return True
    return False


def _function_jvm_name(data: bytes, node) -> str | None:
    """The `@JvmName` on this function, when it renames the JVM method."""

    modifiers = child_of_type(node, "modifiers")
    if modifiers is None:
        return None
    found = None
    for child in modifiers.children:
        if child.type != "annotation" or not _annotation_named(data, child, "JvmName"):
            continue
        text = _annotation_string(data, child)
        if text:
            found = text
    return found


def _annotation_named(data: bytes, node, name: str) -> bool:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "type_identifier" and node_text(data, current) == name:
            return True
        stack.extend(current.children)
    return False


def _annotation_string(data: bytes, node) -> str | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "string_literal":
            text = node_text(data, current)
            if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
                return text[1:-1]
            return text
        stack.extend(current.children)
    return None


def _classes_from_error(data: bytes, node) -> list[tuple[str, str]] | None:
    """Class names recovered when the class header parsed but the body did not.

    A broken string can make the whole file one ERROR node whose children are
    still `class`, the name, `{`, and the functions. Those functions would
    otherwise be filed under `FileKt`.
    """

    error = None
    contained = node
    current = node
    while current.parent is not None:
        if current.parent.type == "ERROR":
            error = current.parent
            contained = current
            break
        current = current.parent
    if error is None:
        return None
    stack: list[tuple[str, str]] = []
    pending_kind: str | None = None
    pending_name: str | None = None
    for sibling in error.children:
        if sibling.start_byte == contained.start_byte and sibling.type == contained.type:
            break
        kind = sibling.type
        if kind in {"class", "interface", "enum"}:
            pending_kind = kind
            pending_name = None
            continue
        if kind == "companion":
            pending_kind = "companion"
            pending_name = "Companion"
            continue
        if kind == "object":
            if pending_kind != "companion":
                pending_kind = "object"
                pending_name = None
            continue
        if kind == "simple_identifier" and pending_kind in {
            "class",
            "interface",
            "enum",
            "object",
            "companion",
        }:
            pending_name = node_text(data, sibling)
            continue
        if kind == "{":
            if pending_kind and pending_name:
                stack.append((pending_name, pending_kind))
            pending_kind = None
            pending_name = None
            continue
        if kind == "}":
            if stack:
                stack.pop()
            pending_kind = None
            pending_name = None
            continue
        if kind in _HEADER_NOISE:
            continue
        pending_kind = None
        pending_name = None
    return stack or None


class _Reader:
    def __init__(self, source: str, path: str):
        self.path = path
        # Repairs run before parsing. file_class still sees the original text,
        # so @file:JvmName is unchanged and line numbers stay the file's lines.
        prepared = _prepare_kotlin(source)
        self.data, self.tree = parse(prepared, "kotlin")
        repaired = _repair_where_statements(prepared, self.tree)
        if repaired is not None:
            prepared = repaired
            self.data, self.tree = parse(prepared, "kotlin")
        repaired = _repair_while_comparisons(prepared, self.tree)
        if repaired is not None:
            self.data, self.tree = parse(repaired, "kotlin")
        self.package = _package_name(self.data, self.tree.root_node)
        self.file_class = file_class(path, source)

    def classes(self, node) -> tuple[str, str]:
        names, binary = _type_chain(self.data, node)
        if not names:
            recovered = _classes_from_error(self.data, node)
            if recovered:
                names = [name for name, _kind in recovered]
                binary = list(names)
                if recovered[-1][1] == "interface":
                    binary.append("DefaultImpls")
        if not names:
            facade, part = self.file_class
            return _qualify(self.package, [facade], "."), _qualify(self.package, [part], "$")
        return _qualify(self.package, names, "."), _qualify(self.package, binary, "$")

    def function(self, node) -> Function | None:
        ident = child_of_type(node, "simple_identifier")
        if ident is None or child_of_type(node, "function_body") is None:
            return None
        namespace, jacoco_class = self.classes(node)
        name = _bare_identifier(node_text(self.data, ident))
        jvm_name = _function_jvm_name(self.data, node)
        if jvm_name == name:
            jvm_name = None
        unmatched = None if _is_inline(node) or _non_main_source(self.path) else 0.0
        return Function(
            name=name,
            namespace=namespace,
            complexity=complexity(node, _is_decision, _skip),
            start_line=start_line(node),
            end_line=end_line(node),
            path=self.path,
            language="kotlin",
            jacoco_class=jacoco_class,
            jacoco_name=jvm_name,
            unmatched_coverage=unmatched,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
        )

    def functions(self) -> list[Function]:
        found: list[Function] = []
        stack = [self.tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "function_declaration":
                function = self.function(node)
                if function is not None:
                    found.append(function)
            elif node.type in _CONTAINERS:
                stack.extend(reversed(node.children))
        return found


def functions_in_source(
    source: str, path: str, project_root: str | None = None
) -> list[Function]:
    del project_root
    reader = _Reader(source, path)
    _warn_parse_errors(path, reader.tree)
    return reader.functions()
