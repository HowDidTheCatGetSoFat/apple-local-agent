import ast
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
codegraph = importlib.import_module("codegraph")
mcp_server = importlib.import_module("graph_mcp")

try:
    import kuzu  # noqa: F401
    _HAVE_KUZU = True
except Exception:
    _HAVE_KUZU = False

SNIPPET = """
class A:
    def m(self):
        helper()

def helper():
    pass

def top():
    A().m()
"""


class TestVisitor(unittest.TestCase):
    def _visit(self):
        v = codegraph._Visitor("x.py")
        v.visit(ast.parse(SNIPPET))
        return v

    def test_defs(self):
        v = self._visit()
        kinds = {name: kind for name, _qual, kind, _f, _l in v.defs}
        self.assertEqual(set(kinds), {"A", "m", "helper", "top"})
        self.assertEqual(kinds["A"], "class")
        self.assertEqual(kinds["m"], "method")
        self.assertEqual(kinds["helper"], "function")

    def test_qualname_and_caller(self):
        v = self._visit()
        quals = {name: qual for name, qual, _k, _f, _l in v.defs}
        self.assertEqual(quals["m"], "A.m")
        callers = {name: caller for name, _f, _l, caller in v.refs}
        self.assertEqual(callers["helper"], "A.m")
        self.assertEqual(callers["m"], "top")


def _run(cmd, **kw):
    # Call a cmd_* function with json output and return the parsed result.
    ns = types.SimpleNamespace(json=True, **kw)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd(ns)
    return json.loads(buf.getvalue() or "[]")


@unittest.skipUnless(_HAVE_KUZU, "kuzu not installed (run under uv run --with kuzu)")
class TestGraphKuzu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.py = os.path.join(self.tmp, "sample.py")
        with open(self.py, "w") as f:
            f.write(SNIPPET)
        # Redirect the module store to a fresh graph and reset the cached conn.
        codegraph.GRAPH_DIR = self.tmp
        codegraph.DB_PATH = os.path.join(self.tmp, "graph.kuzu")
        codegraph._CONN = None
        with contextlib.redirect_stdout(io.StringIO()):
            codegraph.cmd_index(types.SimpleNamespace(paths=[self.py]))

    def tearDown(self):
        codegraph._CONN = None

    def test_def(self):
        rows = _run(codegraph.cmd_def, name="helper")
        self.assertEqual([r["qualname"] for r in rows], ["helper"])
        self.assertEqual(rows[0]["kind"], "function")

    def test_callers(self):
        rows = _run(codegraph.cmd_callers, name="helper")
        self.assertEqual([r["caller"] for r in rows], ["A.m"])

    def test_impact_transitive_with_depth(self):
        rows = _run(codegraph.cmd_impact, name="helper", depth=5)
        by_caller = {r["caller"]: r["depth"] for r in rows}
        self.assertEqual(by_caller, {"A.m": 1, "top": 2})

    def test_unused(self):
        names = {r["qualname"] for r in _run(codegraph.cmd_unused)}
        self.assertIn("top", names)       # nothing references top
        self.assertNotIn("helper", names)  # A.m references helper

    def test_reindex_is_idempotent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            codegraph.cmd_index(types.SimpleNamespace(paths=[self.py]))
        self.assertEqual(len(_run(codegraph.cmd_def, name="helper")), 1)


class TestMCP(unittest.TestCase):
    def test_initialize(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "fxlla-graph")

    def test_tools_list(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(
            {t["name"] for t in r["result"]["tools"]},
            {"find_definition", "find_references", "find_callers",
             "find_impact", "list_unused"})


if __name__ == "__main__":
    unittest.main()
