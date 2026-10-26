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


class _Reader:
    def __init__(self, source: str, path: str):
        self.path = path
        # Blank context receivers before parsing. file_class still sees the
        # original text, so @file:JvmName is unchanged.
        self.data, self.tree = parse(_blank_context_receivers(source), "kotlin")
        self.package = _package_name(self.data, self.tree.root_node)
        self.file_class = file_class(path, source)

    def classes(self, node) -> tuple[str, str]:
        names, binary = _type_chain(self.data, node)
        if not names:
            facade, part = self.file_class
            return _qualify(self.package, [facade], "."), _qualify(self.package, [part], "$")
        return _qualify(self.package, names, "."), _qualify(self.package, binary, "$")

    def function(self, node) -> Function | None:
        ident = child_of_type(node, "simple_identifier")
        if ident is None or child_of_type(node, "function_body") is None:
            return None
        namespace, jacoco_class = self.classes(node)
        return Function(
            name=node_text(self.data, ident),
            namespace=namespace,
            complexity=complexity(node, _is_decision, _skip),
            start_line=start_line(node),
            end_line=end_line(node),
            path=self.path,
            language="kotlin",
            jacoco_class=jacoco_class,
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
