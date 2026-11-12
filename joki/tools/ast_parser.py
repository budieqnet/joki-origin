"""AST parsing terpusat via tree-sitter untuk semua bahasa.

Modul ini menyediakan dua API publik:
  - parse_imports(content: bytes, ext: str) -> list[str] | None
  - parse_symbols(content: bytes, ext: str) -> list[tuple[str, int]] | None

Return None berarti tree-sitter tidak tersedia / parse gagal → caller wajib
fallback ke regex. Tidak pernah raise.

Grammar yang didukung (semua di-pin ke versi kompatibel ABI 13-14 untuk
tree-sitter==0.23.x):
  Python, JavaScript, TypeScript, Go, Java, PHP, Rust, Ruby, C#

Lazy-load: grammar hanya di-import saat pertama kali dibutuhkan dan di-cache.
Jika paket grammar tidak terinstal, bahasa itu dianggap tidak tersedia dan
fungsi mengembalikan None tanpa error.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Cache parser per bahasa (lazy-init)
# ---------------------------------------------------------------------------
_PARSER_CACHE: dict[str, object] = {}   # ext → Parser | False (unavailable)
_LANG_CACHE: dict[str, object] = {}     # ext → Language | False


def _get_parser(ext: str):
    """Return tree_sitter.Parser untuk ekstensi, atau None bila tidak tersedia."""
    if ext in _PARSER_CACHE:
        p = _PARSER_CACHE[ext]
        return p if p is not False else None
    lang = _get_language(ext)
    if lang is None:
        _PARSER_CACHE[ext] = False
        return None
    try:
        from tree_sitter import Parser
        p = Parser(lang)
        _PARSER_CACHE[ext] = p
        return p
    except Exception:  # noqa: BLE001
        _PARSER_CACHE[ext] = False
        return None


def _get_language(ext: str):
    """Return tree_sitter.Language untuk ekstensi, atau None bila tidak tersedia."""
    if ext in _LANG_CACHE:
        v = _LANG_CACHE[ext]
        return v if v is not False else None
    lang = _load_language(ext)
    _LANG_CACHE[ext] = lang if lang is not None else False
    return lang


def _load_language(ext: str):
    """Import grammar dan buat Language. Return None bila gagal."""
    try:
        from tree_sitter import Language
        if ext == ".py":
            import tree_sitter_python as m
            return Language(m.language())
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            import tree_sitter_javascript as m
            return Language(m.language())
        if ext in (".ts", ".tsx", ".mts", ".cts"):
            import tree_sitter_typescript as m
            # TypeScript dan TSX punya entry terpisah
            if ext == ".tsx":
                return Language(m.language_tsx())
            return Language(m.language_typescript())
        if ext == ".go":
            import tree_sitter_go as m
            return Language(m.language())
        if ext == ".java":
            import tree_sitter_java as m
            return Language(m.language())
        if ext == ".php":
            import tree_sitter_php as m
            return Language(m.language_php())
        if ext == ".rs":
            import tree_sitter_rust as m
            return Language(m.language())
        if ext == ".rb":
            import tree_sitter_ruby as m
            return Language(m.language())
        if ext == ".cs":
            import tree_sitter_c_sharp as m
            return Language(m.language())
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Helper traversal
# ---------------------------------------------------------------------------

def _iter_nodes(node):
    """Iterasi semua node secara depth-first (non-rekursif)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _child_text(node, type_name: str) -> str | None:
    """Teks anak pertama dengan tipe tertentu, atau None."""
    for c in node.children:
        if c.type == type_name:
            return c.text.decode("utf-8", errors="replace")
    return None


def _first_child(node, *type_names: str):
    """Anak pertama dengan tipe salah satu type_names, atau None."""
    for c in node.children:
        if c.type in type_names:
            return c
    return None


def _node_text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node.text else ""


# ---------------------------------------------------------------------------
# Import parsers per bahasa
# ---------------------------------------------------------------------------

def _imports_python(root) -> list[str]:
    """Python: import x / from x import y (termasuk nested + relative)."""
    results: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            results.append(name)

    for node in _iter_nodes(root):
        if node.type == "import_statement":
            # import a, b, c
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    sub = _first_child(child, "dotted_name") or child
                    add(_node_text(sub))
        elif node.type == "import_from_statement":
            # from x import y  |  from . import y  |  from ..x import y
            dots = ""
            module_name = ""
            for child in node.children:
                if child.type == "relative_import":
                    dots = _node_text(_first_child(child, "import_prefix") or child)
                    mod_node = _first_child(child, "dotted_name")
                    module_name = _node_text(mod_node) if mod_node else ""
                elif child.type == "dotted_name" and not module_name:
                    module_name = _node_text(child)
            full = dots + module_name
            add(full or dots)
    return results


def _imports_js_ts(root) -> list[str]:
    """JavaScript/TypeScript: import ... from 'x', require('x'), export ... from 'x',
    dynamic import('x'), await import('x')."""
    results: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        if s and s not in seen:
            seen.add(s)
            results.append(s)

    for node in _iter_nodes(root):
        # import ... from 'specifier'
        if node.type in ("import_statement", "export_statement"):
            for child in _iter_nodes(node):
                if child.type == "string_fragment":
                    add(_node_text(child))
                    break
        # require('specifier') / import('specifier') — call_expression
        # Fungsinya bisa identifier "require" atau keyword "import"
        elif node.type == "call_expression":
            fn = node.children[0] if node.children else None
            if fn is not None:
                fn_text = _node_text(fn) if fn.type in ("identifier", "import") else None
                if fn_text in ("require", "import"):
                    args = _first_child(node, "arguments")
                    if args:
                        for arg in args.children:
                            if arg.type == "string":
                                frag = _first_child(arg, "string_fragment")
                                if frag:
                                    add(_node_text(frag))
                                break
    return results


def _imports_go(root) -> list[str]:
    """Go: import "x" / import ( "x" ... )."""
    results: list[str] = []
    seen: set[str] = set()

    for node in _iter_nodes(root):
        if node.type == "import_spec":
            for child in node.children:
                if child.type == "interpreted_string_literal":
                    # konten di dalam tanda kutip
                    for c in child.children:
                        if c.type == "interpreted_string_literal_content":
                            val = _node_text(c)
                            if val and val not in seen:
                                seen.add(val)
                                results.append(val)
    return results


def _imports_java(root) -> list[str]:
    """Java: import a.b.c;"""
    results: list[str] = []
    seen: set[str] = set()

    for node in _iter_nodes(root):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier", "asterisk"):
                    val = _node_text(child)
                    # ambil nama top-level paket (sebelum titik pertama)
                    top = val.split(".")[0] if "." in val else val
                    # simpan full path juga
                    if val and val not in seen:
                        seen.add(val)
                        results.append(val)
                    break
    return results


def _imports_php(root) -> list[str]:
    """PHP: use A\\B\\C; / require_once 'x'; / include 'x';"""
    results: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip().rstrip(";").strip("\"'")
        if s and s not in seen:
            seen.add(s)
            results.append(s)

    for node in _iter_nodes(root):
        if node.type == "namespace_use_declaration":
            # use A\B\C;
            for child in _iter_nodes(node):
                if child.type == "qualified_name":
                    add(_node_text(child))
        elif node.type in ("require_once_expression", "require_expression",
                           "include_once_expression", "include_expression"):
            for child in node.children:
                if child.type in ("string", "encapsed_string"):
                    for sc in child.children:
                        if sc.type == "string_content":
                            add(_node_text(sc))
                    break
    return results


def _imports_rust(root) -> list[str]:
    """Rust: use a::b::c; / extern crate x;"""
    results: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        if s and s not in seen:
            seen.add(s)
            results.append(s)

    for node in _iter_nodes(root):
        if node.type == "use_declaration":
            # Ambil teks penuh dari child use_tree/scoped_identifier/identifier
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier",
                                  "use_wildcard", "use_list", "use_as_clause"):
                    add(_node_text(child))
                    break
        elif node.type == "extern_crate_declaration":
            for child in node.children:
                if child.type == "identifier":
                    add(_node_text(child))
                    break
    return results


def _imports_ruby(root) -> list[str]:
    """Ruby: require 'x' / require_relative 'x'."""
    results: list[str] = []
    seen: set[str] = set()

    for node in _iter_nodes(root):
        if node.type == "call":
            fn = _first_child(node, "identifier")
            if fn and _node_text(fn) in ("require", "require_relative"):
                args = _first_child(node, "argument_list")
                if args:
                    for arg in args.children:
                        if arg.type == "string":
                            sc = _first_child(arg, "string_content")
                            if sc:
                                val = _node_text(sc)
                                if val and val not in seen:
                                    seen.add(val)
                                    results.append(val)
                        break
    return results


def _imports_csharp(root) -> list[str]:
    """C#: using X.Y.Z;"""
    results: list[str] = []
    seen: set[str] = set()

    for node in _iter_nodes(root):
        if node.type == "using_directive":
            for child in node.children:
                if child.type in ("qualified_name", "identifier"):
                    val = _node_text(child)
                    if val and val not in seen:
                        seen.add(val)
                        results.append(val)
                    break
    return results


_IMPORT_DISPATCH = {
    ".py":  _imports_python,
    ".js":  _imports_js_ts,
    ".jsx": _imports_js_ts,
    ".mjs": _imports_js_ts,
    ".cjs": _imports_js_ts,
    ".ts":  _imports_js_ts,
    ".tsx": _imports_js_ts,
    ".mts": _imports_js_ts,
    ".cts": _imports_js_ts,
    ".go":  _imports_go,
    ".java": _imports_java,
    ".php": _imports_php,
    ".rs":  _imports_rust,
    ".rb":  _imports_ruby,
    ".cs":  _imports_csharp,
}


# ---------------------------------------------------------------------------
# Symbol parsers per bahasa
# ---------------------------------------------------------------------------

def _symbols_python(root) -> list[tuple[str, int]]:
    """Python: def/async def/class (termasuk nested)."""
    results: list[tuple[str, int]] = []
    for node in _iter_nodes(root):
        if node.type in ("function_definition", "decorated_definition",
                         "class_definition"):
            # decorated_definition → ambil def/class di dalamnya
            target = node
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type in ("function_definition", "class_definition"):
                        target = child
                        break
            name_node = _first_child(target, "identifier")
            if name_node:
                results.append((_node_text(name_node), target.start_point[0] + 1))
    # sort by line number
    results.sort(key=lambda x: x[1])
    return results


def _symbols_js_ts(root) -> list[tuple[str, int]]:
    """JavaScript/TypeScript: function decl, class decl, method, arrow func."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type == "function_declaration":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "class_declaration":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "method_definition":
            name_node = _first_child(node, "property_identifier", "identifier",
                                     "computed_property_name")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "lexical_declaration":
            # const foo = () => ...  / const bar = function() ...
            for vd in node.children:
                if vd.type == "variable_declarator":
                    val = _first_child(vd, "arrow_function", "function")
                    if val:
                        name_node = _first_child(vd, "identifier")
                        if name_node:
                            results.append((_node_text(name_node), node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


def _symbols_go(root) -> list[tuple[str, int]]:
    """Go: func / type Struct."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type == "function_declaration":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "method_declaration":
            name_node = _first_child(node, "field_identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = _first_child(child, "type_identifier")
                    if name_node:
                        results.append((_node_text(name_node), node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


def _symbols_java(root) -> list[tuple[str, int]]:
    """Java: class / method declarations (top-level + nested)."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type in ("class_declaration", "interface_declaration",
                         "enum_declaration", "record_declaration"):
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "method_declaration":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


def _symbols_php(root) -> list[tuple[str, int]]:
    """PHP: class / function / method."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type in ("class_declaration", "interface_declaration",
                         "trait_declaration", "enum_declaration"):
            name_node = _first_child(node, "name")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type in ("function_definition", "method_declaration"):
            name_node = _first_child(node, "name")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


def _symbols_rust(root) -> list[tuple[str, int]]:
    """Rust: fn / struct / enum / trait / impl."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type == "function_item":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type in ("struct_item", "enum_item", "trait_item",
                           "type_item", "const_item"):
            name_node = _first_child(node, "type_identifier", "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "impl_item":
            # impl Foo / impl Trait for Foo → pakai type_identifier pertama
            name_node = _first_child(node, "type_identifier", "generic_type")
            if name_node:
                results.append((f"impl {_node_text(name_node)}", node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


def _symbols_ruby(root) -> list[tuple[str, int]]:
    """Ruby: class / module / method (def)."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type in ("class", "module"):
            name_node = _first_child(node, "constant")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "method":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "singleton_method":
            # def self.foo
            name_node = None
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


def _symbols_csharp(root) -> list[tuple[str, int]]:
    """C#: class / method / interface / struct / enum."""
    results: list[tuple[str, int]] = []

    for node in _iter_nodes(root):
        if node.type in ("class_declaration", "interface_declaration",
                         "struct_declaration", "enum_declaration",
                         "record_declaration"):
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))
        elif node.type == "method_declaration":
            name_node = _first_child(node, "identifier")
            if name_node:
                results.append((_node_text(name_node), node.start_point[0] + 1))

    results.sort(key=lambda x: x[1])
    return results


_SYMBOL_DISPATCH = {
    ".py":  _symbols_python,
    ".js":  _symbols_js_ts,
    ".jsx": _symbols_js_ts,
    ".mjs": _symbols_js_ts,
    ".cjs": _symbols_js_ts,
    ".ts":  _symbols_js_ts,
    ".tsx": _symbols_js_ts,
    ".mts": _symbols_js_ts,
    ".cts": _symbols_js_ts,
    ".go":  _symbols_go,
    ".java": _symbols_java,
    ".php": _symbols_php,
    ".rs":  _symbols_rust,
    ".rb":  _symbols_ruby,
    ".cs":  _symbols_csharp,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_imports(content: bytes | str, ext: str) -> list[str] | None:
    """Parse imports dari content menggunakan tree-sitter.

    Parameters
    ----------
    content : bytes | str
        Konten file. Dikonversi ke bytes bila str.
    ext : str
        Ekstensi file termasuk titik, contoh: '.py', '.ts', '.go'.

    Returns
    -------
    list[str] | None
        List import specifier (deduplicated, ordered), atau None bila
        tree-sitter tidak tersedia / parse gagal (caller harus fallback ke regex).
    """
    ext = ext.lower()
    if ext not in _IMPORT_DISPATCH:
        return None
    parser = _get_parser(ext)
    if parser is None:
        return None
    try:
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        tree = parser.parse(content)
        return _IMPORT_DISPATCH[ext](tree.root_node)
    except Exception:  # noqa: BLE001
        return None


def parse_symbols(content: bytes | str, ext: str) -> list[tuple[str, int]] | None:
    """Parse simbol (fungsi/class/struct) dari content menggunakan tree-sitter.

    Parameters
    ----------
    content : bytes | str
        Konten file.
    ext : str
        Ekstensi file termasuk titik, contoh: '.py', '.ts', '.go'.

    Returns
    -------
    list[tuple[str, int]] | None
        List (name, lineno) urut berdasar baris, atau None bila
        tree-sitter tidak tersedia / parse gagal (caller harus fallback ke regex).
    """
    ext = ext.lower()
    if ext not in _SYMBOL_DISPATCH:
        return None
    parser = _get_parser(ext)
    if parser is None:
        return None
    try:
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        tree = parser.parse(content)
        return _SYMBOL_DISPATCH[ext](tree.root_node)
    except Exception:  # noqa: BLE001
        return None


def is_available(ext: str) -> bool:
    """True bila grammar untuk ekstensi tersedia dan bisa dipakai."""
    return _get_parser(ext.lower()) is not None


def available_languages() -> list[str]:
    """Return list ekstensi yang grammar-nya tersedia."""
    return [ext for ext in list(_IMPORT_DISPATCH.keys()) if is_available(ext)]
