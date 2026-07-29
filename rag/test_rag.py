import importlib
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
core = importlib.import_module("core")
mcp_server = importlib.import_module("rag_mcp")

try:
    import sqlite_vec  # noqa: F401
    _HAVE_VEC = True
except Exception:
    _HAVE_VEC = False


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

    def test_index_enabled(self):
        for on in ("1", "true", "YES", "On"):
            os.environ["FXLLA_KB_INDEX"] = on
            self.assertTrue(core._index_enabled())
        for off in ("", "0", "false", "no"):
            os.environ["FXLLA_KB_INDEX"] = off
            self.assertFalse(core._index_enabled())
        os.environ.pop("FXLLA_KB_INDEX", None)

    def test_vec_table_quoted(self):
        self.assertEqual(core._vec_table("my.kb-1"), '"vec_my.kb-1"')

    def test_load_vec_disabled_returns_none(self):
        # With the index off, _load_vec never touches the extension.
        os.environ.pop("FXLLA_KB_INDEX", None)
        con = sqlite3.connect(":memory:")
        self.assertIsNone(core._load_vec(con))


@unittest.skipUnless(_HAVE_VEC, "sqlite-vec not installed (run under uv run --with sqlite-vec)")
class TestVecIndex(unittest.TestCase):
    # Builds a tiny store directly (no embedder) and checks the KNN index
    # returns the same ordering as the brute-force cosine scan.
    def _con(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE chunks (kb TEXT, source TEXT, idx INTEGER, text TEXT, emb BLOB)")
        vecs = {"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0], "c": [0.9, 0.1, 0.0]}
        for i, (name, v) in enumerate(vecs.items()):
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?)",
                        ("k", name, i, name, core._pack(v)))
        con.commit()
        return con

    def test_indexed_matches_bruteforce(self):
        os.environ["FXLLA_KB_INDEX"] = "1"
        try:
            con = self._con()
            vec = core._load_vec(con)
            self.assertIsNotNone(vec)
            q = [1.0, 0.0, 0.0]
            top = core._search_indexed(con, vec, "k", q, 2)
            self.assertEqual([r[1] for r in top], ["a", "c"])
            self.assertAlmostEqual(top[0][0], 1.0, places=4)
        finally:
            os.environ.pop("FXLLA_KB_INDEX", None)

    def test_rebuild_on_stale(self):
        os.environ["FXLLA_KB_INDEX"] = "1"
        try:
            con = self._con()
            vec = core._load_vec(con)
            core._ensure_vec_index(con, vec, "k")
            self.assertEqual(con.execute('SELECT COUNT(*) FROM "vec_k"').fetchone()[0], 3)
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?)",
                        ("k", "d", 3, "d", core._pack([0.0, 0.0, 1.0])))
            con.commit()
            core._ensure_vec_index(con, vec, "k")
            self.assertEqual(con.execute('SELECT COUNT(*) FROM "vec_k"').fetchone()[0], 4)
        finally:
            os.environ.pop("FXLLA_KB_INDEX", None)


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
