#!/usr/bin/env python3
"""fxlla code graph: index Python symbols and references, query the graph.

Uses the standard library `ast` module and a SQLite store under <store>/graph.
Definitions (functions, classes, methods), references (calls), and the enclosing
scope of each call are extracted so you can find where a symbol is defined, who
references it, and who calls it.

Usage (normally driven via `fxlla graph`):
  codegraph.py index <path...>   index Python files or directories
  codegraph.py def <name>        where a symbol is defined
  codegraph.py refs <name>       where a symbol is referenced
  codegraph.py callers <name>    which functions call a symbol
  codegraph.py ls                indexed files
  codegraph.py rm                clear the graph
"""
import argparse
import ast
import json
import os
import sqlite3
import sys

STORE = os.environ.get("FXLLA_STORE", "")
GRAPH_DIR = os.path.join(STORE, "graph")
DB_PATH = os.path.join(GRAPH_DIR, "graph.db")


def _db():
    os.makedirs(GRAPH_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS defs "
                "(name TEXT, qualname TEXT, kind TEXT, file TEXT, line INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS refs "
                "(name TEXT, file TEXT, line INTEGER, caller TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS defs_name ON defs(name)")
    con.execute("CREATE INDEX IF NOT EXISTS refs_name ON refs(name)")
    return con


class _Visitor(ast.NodeVisitor):
    def __init__(self, file):
        self.file = file
        self.names = []   # scope stack of names
        self.kinds = []   # parallel stack of kinds
        self.defs = []    # (name, qualname, kind, file, line)
        self.refs = []    # (name, file, line, caller)

    def _visit_def(self, node, kind):
        qual = ".".join(self.names + [node.name])
        self.defs.append((node.name, qual, kind, self.file, node.lineno))
        self.names.append(node.name)
        self.kinds.append(kind)
        self.generic_visit(node)
        self.names.pop()
        self.kinds.pop()

    def visit_FunctionDef(self, node):
        parent_class = bool(self.kinds) and self.kinds[-1] == "class"
        self._visit_def(node, "method" if parent_class else "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._visit_def(node, "class")

    def visit_Call(self, node):
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name:
            self.refs.append((name, self.file, node.lineno, ".".join(self.names)))
        self.generic_visit(node)


def _gather(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                if (os.sep + ".git" + os.sep) in (root + os.sep) or root.endswith(os.sep + ".git"):
                    continue
                for n in names:
                    if n.endswith(".py"):
                        files.append(os.path.join(root, n))
        elif os.path.isfile(p) and p.endswith(".py"):
            files.append(p)
    return files


def cmd_index(args):
    files = _gather(args.paths)
    if not files:
        sys.exit("no Python files found in the given paths")
    con = _db()
    total_defs = total_refs = 0
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                tree = ast.parse(fh.read(), filename=f)
        except (SyntaxError, ValueError):
            continue
        v = _Visitor(f)
        v.visit(tree)
        con.execute("DELETE FROM defs WHERE file=?", (f,))
        con.execute("DELETE FROM refs WHERE file=?", (f,))
        con.executemany("INSERT INTO defs VALUES (?,?,?,?,?)", v.defs)
        con.executemany("INSERT INTO refs VALUES (?,?,?,?)", v.refs)
        total_defs += len(v.defs)
        total_refs += len(v.refs)
    con.commit()
    print(f"indexed {len(files)} files: {total_defs} defs, {total_refs} refs")


def cmd_ls(_args):
    con = _db()
    rows = con.execute("SELECT file, COUNT(*) FROM defs GROUP BY file ORDER BY file").fetchall()
    if not rows:
        print("(graph is empty)")
        return
    for f, n in rows:
        print(f"{f}\t{n} defs")


def cmd_rm(_args):
    con = _db()
    con.execute("DELETE FROM defs")
    con.execute("DELETE FROM refs")
    con.commit()
    print("graph cleared")


def _emit(args, items, fmt):
    if getattr(args, "json", False):
        print(json.dumps(items))
        return
    if not items:
        print("(none)")
        return
    for it in items:
        print(fmt(it))


def cmd_def(args):
    con = _db()
    rows = con.execute(
        "SELECT qualname, kind, file, line FROM defs WHERE name=? OR qualname=? "
        "ORDER BY file, line", (args.name, args.name)).fetchall()
    _emit(args,
          [{"qualname": q, "kind": k, "file": f, "line": ln} for q, k, f, ln in rows],
          lambda r: f"{r['kind']:8} {r['qualname']}  {r['file']}:{r['line']}")


def cmd_refs(args):
    con = _db()
    rows = con.execute(
        "SELECT file, line, caller FROM refs WHERE name=? ORDER BY file, line",
        (args.name,)).fetchall()
    _emit(args,
          [{"file": f, "line": ln, "caller": c} for f, ln, c in rows],
          lambda r: f"{r['file']}:{r['line']}" + (f"  in {r['caller']}" if r['caller'] else ""))


def cmd_callers(args):
    con = _db()
    rows = con.execute(
        "SELECT DISTINCT caller, file FROM refs WHERE name=? AND caller<>'' "
        "ORDER BY caller", (args.name,)).fetchall()
    _emit(args,
          [{"caller": c, "file": f} for c, f in rows],
          lambda r: f"{r['caller']}  ({r['file']})")


def cmd_impact(args):
    # Transitive callers (breadth-first over the call graph): the blast radius
    # of changing a symbol. Name-approximate, so cap the depth.
    con = _db()
    seen = {args.name}
    result = []
    frontier = [args.name]
    depth = 0
    max_depth = getattr(args, "depth", None) or 5
    while frontier and depth < max_depth:
        depth += 1
        placeholders = ",".join("?" * len(frontier))
        rows = con.execute(
            f"SELECT DISTINCT caller FROM refs WHERE name IN ({placeholders}) AND caller<>''",
            tuple(frontier)).fetchall()
        nxt = []
        for (caller,) in rows:
            leaf = caller.split(".")[-1]
            if leaf not in seen:
                seen.add(leaf)
                result.append({"depth": depth, "caller": caller})
                nxt.append(leaf)
        frontier = nxt
    _emit(args, result, lambda r: f"{'  ' * r['depth']}{r['caller']} (depth {r['depth']})")


def main():
    p = argparse.ArgumentParser(prog="fxlla-graph")
    sub = p.add_subparsers(dest="cmd", required=True)
    ix = sub.add_parser("index")
    ix.add_argument("paths", nargs="+")
    for name in ("def", "refs", "callers"):
        sp = sub.add_parser(name)
        sp.add_argument("name")
        sp.add_argument("-j", "--json", action="store_true")
    im = sub.add_parser("impact")
    im.add_argument("name")
    im.add_argument("--depth", type=int, default=5)
    im.add_argument("-j", "--json", action="store_true")
    sub.add_parser("ls")
    sub.add_parser("rm")
    args = p.parse_args()
    {
        "index": cmd_index, "ls": cmd_ls, "rm": cmd_rm,
        "def": cmd_def, "refs": cmd_refs, "callers": cmd_callers,
        "impact": cmd_impact,
    }[args.cmd](args)


if __name__ == "__main__":
    if not STORE or not os.path.isdir(STORE):
        sys.exit("FXLLA_STORE is not set or does not exist")
    main()
