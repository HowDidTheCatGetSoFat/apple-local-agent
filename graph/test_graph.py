import ast
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
codegraph = importlib.import_module("codegraph")
mcp_server = importlib.import_module("graph_mcp")

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
