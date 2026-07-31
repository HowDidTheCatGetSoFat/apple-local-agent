import importlib
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The gateway reads FXLLA_STORE at import time; point it at a scratch store
# with two model dirs before importing.
_STORE = tempfile.mkdtemp(prefix="fxlla-gw-")
os.environ["FXLLA_STORE"] = _STORE
_MODELS = os.path.join(_STORE, "models")
for _name, _engine in (("mlx-model", None), ("gguf-model", "gguf")):
    os.makedirs(os.path.join(_MODELS, _name), exist_ok=True)
    if _engine:
        with open(os.path.join(_MODELS, _name, ".engine"), "w") as _f:
            _f.write(_engine + "\n")

gw = importlib.import_module("fxlla_gateway")


class TestDownloadedModels(unittest.TestCase):
    # Embedding models share the store but cannot chat. Serving one spawns
    # llama-server without --embeddings on a BERT, and this list feeds the
    # opencode registration: 'embed' shipped as a selectable chat model once.
    def _store(self, dirs):
        root = tempfile.mkdtemp(prefix="fxlla-dm-")
        for name, source in dirs.items():
            os.makedirs(os.path.join(root, name))
            with open(os.path.join(root, name, ".source"), "w") as fh:
                fh.write(source + "\n")
        return root

    def test_embed_models_are_excluded_by_alias_and_by_repo(self):
        catalog = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        catalog.write(
            "# comment\n"
            "chat  | org/chat-model  | 1GB | dev   | mlx  | note\n"
            "embed | org/embed-model | 1GB | embed | gguf | note\n")
        catalog.close()
        store = self._store({
            "chat": "org/chat-model",
            "embed": "org/embed-model",              # by alias
            "embed-model": "org/embed-model",        # pulled by org/repo form
            "incomplete": "",                        # no real .source content
        })
        os.remove(os.path.join(store, "incomplete", ".source"))
        saved = (gw.MODELS_DIR, gw.CATALOG)
        gw.MODELS_DIR, gw.CATALOG = store, catalog.name
        try:
            self.assertEqual(sorted(gw.downloaded_models()), ["chat"])
        finally:
            gw.MODELS_DIR, gw.CATALOG = saved
            os.unlink(catalog.name)

    def test_model_context_per_engine(self):
        # mlx serves the model's own window (config.json); gguf serves the -c
        # llama-server was started with. Feeding either the wrong one gives
        # opencode a context meter that lies in one direction or the other.
        store = self._store({"m-mlx": "org/a", "m-gguf": "org/b", "m-bare": "org/c"})
        with open(os.path.join(store, "m-mlx", "config.json"), "w") as fh:
            fh.write('{"max_position_embeddings": 262144}')
        with open(os.path.join(store, "m-gguf", ".engine"), "w") as fh:
            fh.write("gguf\n")
        saved = gw.MODELS_DIR
        gw.MODELS_DIR = store
        try:
            self.assertEqual(gw.model_context("m-mlx"), 262144)
            self.assertEqual(gw.model_context("m-gguf"), gw.SERVED_GGUF_CTX)
            self.assertIsNone(gw.model_context("m-bare"))
        finally:
            gw.MODELS_DIR = saved

    def test_a_missing_catalog_excludes_nothing(self):
        # A stranger's checkout with a moved catalog must not hide their
        # models; the filter fails open to the old behavior.
        store = self._store({"m1": "org/m1"})
        saved = (gw.MODELS_DIR, gw.CATALOG)
        gw.MODELS_DIR, gw.CATALOG = store, "/nonexistent/models.conf"
        try:
            self.assertEqual(sorted(gw.downloaded_models()), ["m1"])
        finally:
            gw.MODELS_DIR, gw.CATALOG = saved


class TestEngineDetection(unittest.TestCase):
    def test_default_engine_is_mlx(self):
        self.assertEqual(gw.engine_for("mlx-model"), "mlx")

    def test_gguf_marker(self):
        self.assertEqual(gw.engine_for("gguf-model"), "gguf")

    def test_missing_model_defaults_mlx(self):
        self.assertEqual(gw.engine_for("nope"), "mlx")


class TestModelField(unittest.TestCase):
    def test_mlx_sends_path(self):
        self.assertEqual(gw.model_field_for("mlx-model"),
                         os.path.join(_MODELS, "mlx-model"))

    def test_gguf_sends_alias(self):
        self.assertEqual(gw.model_field_for("gguf-model"), "gguf-model")


class _FakeProc:
    pid = os.getpid()

    def terminate(self):
        pass


class TestWaitReady(unittest.TestCase):
    """A model switch waits on this loop, so its poll interval is the floor on
    switching, and a backend that dies must not hold the whole timeout."""

    def _manager(self):
        return gw.Manager.__new__(gw.Manager)

    def test_poll_interval_does_not_round_up_to_a_second(self):
        # At a one-second interval a backend ready at 1.01s was reported at 2.0s.
        self.assertLessEqual(gw.READY_POLL_INTERVAL, 0.2)

    def test_a_dead_backend_fails_at_once(self):
        class _Dead:
            def poll(self):
                return 1

        start = time.monotonic()
        self.assertFalse(self._manager()._wait_ready(9, timeout=5, proc=_Dead()))
        self.assertLess(time.monotonic() - start, 1.0,
                        "burned the timeout on a backend that had already exited")

    def test_a_live_but_silent_backend_waits_out_the_budget(self):
        # The opposite case: still loading, so the loop must keep waiting.
        class _Alive:
            def poll(self):
                return None

        start = time.monotonic()
        self.assertFalse(self._manager()._wait_ready(9, timeout=0.5, proc=_Alive()))
        self.assertGreaterEqual(time.monotonic() - start, 0.5)

    def test_no_proc_behaves_as_before(self):
        start = time.monotonic()
        self.assertFalse(self._manager()._wait_ready(9, timeout=0.4))
        self.assertGreaterEqual(time.monotonic() - start, 0.4)


class TestEnsureColdLoad(unittest.TestCase):
    """The loader path must return a real model_field, not the None sentinel,
    on the very first request to a freshly-loaded model."""

    def _ensure(self, alias):
        m = gw.Manager()
        saved = (gw.downloaded_models, gw.subprocess.Popen, gw.Manager._wait_ready)
        try:
            gw.downloaded_models = lambda: {alias: {"size_mb": 1}}
            gw.subprocess.Popen = lambda *a, **k: _FakeProc()
            gw.Manager._wait_ready = lambda self, port, timeout=180, proc=None: True
            return m.ensure(alias)
        finally:
            gw.downloaded_models, gw.subprocess.Popen, gw.Manager._wait_ready = saved

    def test_gguf_first_load_returns_alias(self):
        _port, model_field = self._ensure("gguf-model")
        self.assertEqual(model_field, "gguf-model")

    def test_mlx_first_load_returns_path(self):
        _port, model_field = self._ensure("mlx-model")
        self.assertEqual(model_field, os.path.join(_MODELS, "mlx-model"))
        self.assertIsNotNone(model_field)

    def test_load_cancelled_by_concurrent_unload(self):
        m = gw.Manager()
        saved = (gw.downloaded_models, gw.subprocess.Popen, gw.Manager._wait_ready)
        try:
            gw.downloaded_models = lambda: {"gguf-model": {"size_mb": 1}}
            gw.subprocess.Popen = lambda *a, **k: _FakeProc()

            def wait_ready(self, port, timeout=180, proc=None):
                self.epoch += 1   # an unload_all races this load
                return True
            gw.Manager._wait_ready = wait_ready
            with self.assertRaises(RuntimeError):
                m.ensure("gguf-model")
            self.assertEqual(m.backends, {})   # the raced load is not registered
        finally:
            gw.downloaded_models, gw.subprocess.Popen, gw.Manager._wait_ready = saved


class _TermProc:
    def __init__(self):
        self.pid = os.getpid()
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True

    def kill(self):
        pass


class TestUnloadAll(unittest.TestCase):
    def test_unload_frees_waits_and_reports(self):
        m = gw.Manager()
        p1, p2 = _TermProc(), _TermProc()
        m.backends["a"] = gw.Backend("a", 8100, p1, 10, "a", "gguf")
        m.backends["b"] = gw.Backend("b", 8101, p2, 10, "b", "gguf")
        freed = m.unload_all()
        self.assertEqual(set(freed), {"a", "b"})
        self.assertEqual(m.backends, {})
        # terminated AND waited: memory is released before returning
        self.assertTrue(p1.terminated and p1.waited)
        self.assertTrue(p2.terminated and p2.waited)

    def test_unload_bumps_epoch(self):
        m = gw.Manager()
        e = m.epoch
        m.unload_all()
        self.assertEqual(m.epoch, e + 1)

    def test_unload_empty_is_noop(self):
        m = gw.Manager()
        self.assertEqual(m.unload_all(), [])


class TestLoopback(unittest.TestCase):
    def test_loopback_addresses(self):
        for a in ("127.0.0.1", "127.0.0.5", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(gw._is_loopback(a), a)

    def test_non_loopback_addresses(self):
        for a in ("10.0.0.5", "192.168.1.9", "0.0.0.0", "::"):
            self.assertFalse(gw._is_loopback(a), a)


class TestRss(unittest.TestCase):
    def test_own_process_has_rss(self):
        self.assertGreater(gw.rss_mb(os.getpid()), 0)

    def test_bogus_pid_is_zero(self):
        self.assertEqual(gw.rss_mb(-1), 0)


if __name__ == "__main__":
    unittest.main()
