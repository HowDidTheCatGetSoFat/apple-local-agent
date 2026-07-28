import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
core = importlib.import_module("core")
mcp_server = importlib.import_module("mcp_server")


class TestCore(unittest.TestCase):
    def test_chunk_overlap(self):
        self.assertEqual(len(core.chunk_text("x" * 2000, size=800, overlap=120)), 3)

    def test_chunk_empty(self):
        self.assertEqual(core.chunk_text("   "), [])

    def test_cosine(self):
        self.assertAlmostEqual(core._cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(core._cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(core._cosine([0, 0], [1, 1]), 0.0)

    def test_pack_roundtrip(self):
        v = [0.1, 0.2, 0.3]
        self.assertEqual([round(x, 4) for x in core._unpack(core._pack(v))], v)


class TestMCP(unittest.TestCase):
    def test_initialize(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "fxlla-rag")

    def test_tools_list(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([t["name"] for t in r["result"]["tools"]], ["rag_search"])

    def test_notification_no_response(self):
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
