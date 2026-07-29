import importlib
import os
import sys
import tempfile
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


class TestEnsureColdLoad(unittest.TestCase):
    """The loader path must return a real model_field, not the None sentinel,
    on the very first request to a freshly-loaded model."""

    def _ensure(self, alias):
        m = gw.Manager()
        saved = (gw.downloaded_models, gw.subprocess.Popen, gw.Manager._wait_ready)
        try:
            gw.downloaded_models = lambda: {alias: {"size_mb": 1}}
            gw.subprocess.Popen = lambda *a, **k: _FakeProc()
            gw.Manager._wait_ready = lambda self, port, timeout=180: True
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

            def wait_ready(self, port, timeout=180):
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
