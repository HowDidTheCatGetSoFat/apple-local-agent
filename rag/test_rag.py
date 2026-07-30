import contextlib
import http.server
import importlib
import os
import socket
import tempfile
import threading
import time
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


class _Health(http.server.BaseHTTPRequestHandler):
    """Answers /health like llama-server does, and nothing else."""
    def do_GET(self):
        self.send_response(200 if self.path.startswith("/health") else 404)
        self.end_headers()
        self.wfile.write(b"{}")
    def log_message(self, *_a):
        pass


@contextlib.contextmanager
def _fake_server():
    """A live health endpoint on a free port, standing in for llama-server."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    saved = core.EMBED_PORT
    core.EMBED_PORT = srv.server_address[1]
    try:
        yield core.EMBED_PORT
    finally:
        core.EMBED_PORT = saved
        srv.shutdown()
        srv.server_close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestEmbedderLifecycle(unittest.TestCase):
    def test_a_slow_poll_interval_would_dominate_startup(self):
        # The regression this guards: the server was ready in ~0.17s but the poll
        # interval was one second, so the first probe missed it and every query
        # slept through a server that was already up. Asserting the constant is
        # not enough - it does not prove the loop uses it. This times the real
        # loop against a server that starts listening shortly after the spawn.
        port = _free_port()
        saved_port, saved_model = core.EMBED_PORT, core._embed_model
        saved_popen = core.subprocess.Popen
        core.EMBED_PORT = port
        core._embed_model = lambda: "/dev/null"

        class _Alive:
            def poll(self):
                return None
            def terminate(self):
                pass
            def wait(self, timeout=None):
                return 0

        srv = {}

        def _fake_popen(*_a, **_k):
            def serve():
                time.sleep(0.15)  # what a real model load costs, roughly
                srv["s"] = http.server.HTTPServer(("127.0.0.1", port), _Health)
                srv["s"].serve_forever()
            threading.Thread(target=serve, daemon=True).start()
            return _Alive()

        core.subprocess.Popen = _fake_popen
        try:
            begin = time.monotonic()
            with core.Embedder():
                elapsed = time.monotonic() - begin
            # Ready at 0.15s. A one-second interval cannot come in under 0.6s.
            self.assertLess(elapsed, 0.6, "startup is dominated by the poll interval")
        finally:
            core.EMBED_PORT, core._embed_model = saved_port, saved_model
            core.subprocess.Popen = saved_popen
            if "s" in srv:
                srv["s"].shutdown()
                srv["s"].server_close()

    def test_starting_a_server_takes_an_exclusive_lock(self):
        # Asserts the mechanism, not cross-process behaviour: a unit test cannot
        # race two interpreters. Without the lock, ten concurrent searches each
        # started a server and up to four in ten failed after losing the bind;
        # that was verified by running fifty real searches, not here.
        port = _free_port()
        saved = (core.EMBED_PORT, core._embed_model, core.subprocess.Popen,
                 core.fcntl.flock, core.KB_DIR)
        core.EMBED_PORT = port
        core._embed_model = lambda: "/dev/null"
        core.KB_DIR = tempfile.mkdtemp()
        modes = []
        core.fcntl.flock = lambda fh, mode: modes.append(mode)
        srv = {}

        class _Alive:
            def poll(self):
                return None
            def terminate(self):
                pass
            def wait(self, timeout=None):
                return 0

        def _fake_popen(*_a, **_k):
            def serve():
                time.sleep(0.05)
                srv["s"] = http.server.HTTPServer(("127.0.0.1", port), _Health)
                srv["s"].serve_forever()
            threading.Thread(target=serve, daemon=True).start()
            return _Alive()

        core.subprocess.Popen = _fake_popen
        try:
            with core.Embedder():
                pass
            self.assertIn(core.fcntl.LOCK_EX, modes, "spawning was not serialized")
        finally:
            (core.EMBED_PORT, core._embed_model, core.subprocess.Popen,
             core.fcntl.flock, core.KB_DIR) = saved
            if "s" in srv:
                srv["s"].shutdown()
                srv["s"].server_close()

    def test_ready_deadline_stays_generous(self):
        # A large embedding model on an external disk can take a while to load.
        # An earlier revision quietly halved this while fixing the interval.
        self.assertGreaterEqual(core._READY_TIMEOUT, 120.0)

    def test_health_timeout_tolerates_a_loaded_machine(self):
        # Too short a timeout reports a working-but-slow server as absent, which
        # then fails the query outright.
        self.assertGreaterEqual(core._HEALTH_TIMEOUT, 3.0)

    def test_healthy_is_false_when_nothing_listens(self):
        saved = core.EMBED_PORT
        core.EMBED_PORT = _free_port()
        try:
            self.assertFalse(core._server_healthy(timeout=0.5))
        finally:
            core.EMBED_PORT = saved

    def test_reuses_a_live_server_without_spawning(self):
        with _fake_server():
            saved = core.subprocess.Popen

            def _no(*_a, **_k):
                raise AssertionError("spawned a server while one was listening")

            core.subprocess.Popen = _no
            try:
                with core.Embedder() as emb:
                    self.assertIsNone(emb.proc)  # None means borrowed
            finally:
                core.subprocess.Popen = saved

    def test_borrowed_server_is_left_running(self):
        with _fake_server() as port:
            with core.Embedder():
                pass
            self.assertTrue(core._server_healthy(timeout=1.0), "killed a borrowed server")
            self.assertEqual(port, core.EMBED_PORT)

    def test_a_server_we_started_is_stopped(self):
        class _Spy:
            def __init__(self):
                self.terminated = False
            def terminate(self):
                self.terminated = True
            def wait(self, timeout=None):
                return 0

        emb = core.Embedder.__new__(core.Embedder)
        emb.proc, emb._lock = _Spy(), None
        emb.__exit__(None, None, None)
        self.assertTrue(emb.proc.terminated)

    def test_a_rejected_request_exits_with_a_message_not_a_traceback(self):
        # A server answering /health but rejecting /v1/embeddings is the shape of
        # a chat server on the embedding port. The MCP layer forwards stderr to a
        # model verbatim, so a traceback there is a 2 KB tool result.
        with _fake_server():  # _Health 404s on POST
            emb = core.Embedder.__new__(core.Embedder)
            emb.proc, emb._lock = None, None
            with self.assertRaises(SystemExit) as ctx:
                emb.embed(["x"])
            message = str(ctx.exception)
            # The hint only the dedicated HTTPError branch produces. Without it
            # the rejection falls through to the connection-reset retry, which
            # both wastes a round trip and reports the wrong cause.
            self.assertIn("--embeddings", message)
            self.assertLess(len(message), 400)


class TestDimensionGuard(unittest.TestCase):
    # A server for a different model returns a different width, and _cosine zips
    # to the shorter vector: no error, just meaningless scores, and on add a base
    # permanently holding two widths.
    def _con(self, dim):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE chunks (kb TEXT, source TEXT, idx INTEGER, "
                    "text TEXT, emb BLOB)")
        con.execute("INSERT INTO chunks VALUES ('k','s',0,'t',?)",
                    (core._pack([0.1] * dim),))
        return con

    def test_reports_the_stored_width(self):
        self.assertEqual(core._kb_dim(self._con(768), "k"), 768)

    def test_none_for_an_empty_base(self):
        self.assertIsNone(core._kb_dim(self._con(768), "other"))

    def test_a_mismatch_refuses(self):
        with self.assertRaises(SystemExit) as ctx:
            core._require_dim(self._con(768), "k", [0.1] * 384)
        self.assertIn("384", str(ctx.exception))
        self.assertIn("768", str(ctx.exception))

    def test_a_match_passes(self):
        core._require_dim(self._con(768), "k", [0.1] * 768)

    def test_an_empty_base_accepts_any_width(self):
        core._require_dim(self._con(768), "fresh", [0.1] * 384)


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
