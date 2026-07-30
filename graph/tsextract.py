#!/usr/bin/env python3
"""Tree-sitter extraction for the fxlla code graph (non-Python languages).

Produces the same (defs, refs) shape as the standard-library `ast` visitor in
codegraph.py, so both feed the same KuzuDB Def/Ref/CALLS model:
  defs: (name, qualname, kind, file, line)
  refs: (name, file, line, caller)

Grammars come from `tree-sitter-language-pack` (precompiled, 100+ languages),
imported lazily so codegraph.py and its unit tests still import under a plain
system python without tree-sitter installed. `fxlla graph` runs the backend under
`uv run --with tree-sitter --with tree-sitter-language-pack`.
"""
import os

# File extension -> tree-sitter-language-pack language name. Python is handled by
# codegraph.py's ast path, not here.
LANG_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".rb": "ruby",
}

# Per-language node mapping:
#   defs:  node type -> definition kind
#   class_types: def node types that open a class-like scope (nested functions
#                become methods)
#   calls: call node type -> field name holding the callee
_CFG = {
    "javascript": {
        "defs": {"function_declaration": "function", "method_definition": "method",
                 "class_declaration": "class", "generator_function_declaration": "function"},
        "class_types": {"class_declaration"},
        "calls": {"call_expression": "function", "new_expression": "constructor"},
    },
    "typescript": {
        "defs": {"function_declaration": "function", "method_definition": "method",
                 "class_declaration": "class", "interface_declaration": "interface",
                 "abstract_class_declaration": "class"},
        "class_types": {"class_declaration", "interface_declaration",
                        "abstract_class_declaration"},
        "calls": {"call_expression": "function", "new_expression": "constructor"},
    },
    "go": {
        "defs": {"function_declaration": "function", "method_declaration": "method",
                 "type_spec": "type"},
        "class_types": set(),
        "calls": {"call_expression": "function"},
    },
    "rust": {
        "defs": {"function_item": "function", "struct_item": "struct",
                 "impl_item": "impl", "trait_item": "trait", "enum_item": "enum"},
        "class_types": {"impl_item", "trait_item"},
        "calls": {"call_expression": "function", "macro_invocation": "macro"},
    },
    "java": {
        "defs": {"method_declaration": "method", "class_declaration": "class",
                 "interface_declaration": "interface",
                 "constructor_declaration": "method", "enum_declaration": "enum"},
        "class_types": {"class_declaration", "interface_declaration",
                        "enum_declaration"},
        "calls": {"method_invocation": "name", "object_creation_expression": "type"},
    },
    "ruby": {
        "defs": {"method": "method", "class": "class", "module": "module",
                 "singleton_method": "method"},
        "class_types": {"class", "module"},
        "calls": {"call": "method", "method_call": "method"},
    },
}
# C and C++ share a config; TSX reuses the TypeScript one.
_CFG["c"] = {
    "defs": {"function_definition": "function", "struct_specifier": "struct"},
    "class_types": {"struct_specifier"},
    "calls": {"call_expression": "function"},
}
_CFG["cpp"] = {
    "defs": {"function_definition": "function", "class_specifier": "class",
             "struct_specifier": "struct"},
    "class_types": {"class_specifier", "struct_specifier"},
    "calls": {"call_expression": "function"},
}
_CFG["tsx"] = _CFG["typescript"]

_CLASS_SCOPE_KINDS = {"class", "impl", "struct", "interface", "trait", "module",
                      "enum"}
_NAME_NODE_TYPES = ("identifier", "type_identifier", "field_identifier",
                    "property_identifier", "constant", "scoped_type_identifier")


def supported(path):
    return os.path.splitext(path)[1].lower() in LANG_BY_EXT


def _text(node, src):
    return src[node.start_byte:node.end_byte].decode("utf-8", "ignore")


def _def_name(node, src):
    n = node.child_by_field_name("name")
    if n is not None:
        return _text(n, src)
    for c in node.children:
        if c.type in _NAME_NODE_TYPES:
            return _text(c, src)
    # C/C++ bury the name inside the declarator; search shallowly.
    for c in node.children:
        r = _def_name(c, src)
        if r:
            return r
    return None


def _callee_name(node, field, src):
    fn = node.child_by_field_name(field)
    if fn is None:
        return None
    if fn.type in _NAME_NODE_TYPES:
        return _text(fn, src)
    # obj.method(), pkg.Func(), a::b() -> take the trailing name component.
    if fn.type in ("member_expression", "field_expression", "selector_expression",
                   "scoped_identifier", "scoped_type_identifier"):
        prop = (fn.child_by_field_name("property") or fn.child_by_field_name("field")
                or fn.child_by_field_name("name"))
        if prop is not None:
            return _text(prop, src)
        ids = [c for c in fn.children if "identifier" in c.type]
        if ids:
            return _text(ids[-1], src)
    return None


def extract(path, lang):
    """Return (defs, refs) for a source file, matching the ast visitor's shape."""
    import tree_sitter_language_pack as tslp

    cfg = _CFG[lang]
    with open(path, "rb") as fh:
        src = fh.read()
    tree = tslp.get_parser(lang).parse(src)
    defs, refs = [], []

    def walk(node, scope):
        next_scope = scope
        if node.type in cfg["defs"]:
            name = _def_name(node, src)
            if name:
                kind = cfg["defs"][node.type]
                if kind == "function" and scope and scope[-1][1] in _CLASS_SCOPE_KINDS:
                    kind = "method"
                qual = ".".join([s[0] for s in scope] + [name])
                defs.append((name, qual, kind, path, node.start_point[0] + 1))
                next_scope = scope + [(name, cfg["defs"][node.type])]
        if node.type in cfg["calls"]:
            callee = _callee_name(node, cfg["calls"][node.type], src)
            if callee:
                caller = ".".join([s[0] for s in scope])
                refs.append((callee, path, node.start_point[0] + 1, caller))
        for child in node.children:
            walk(child, next_scope)

    walk(tree.root_node, [])
    return defs, refs
