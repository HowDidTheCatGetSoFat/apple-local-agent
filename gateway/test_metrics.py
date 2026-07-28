import importlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
metrics = importlib.import_module("metrics")


def _sse(*objs):
    return "".join("data: " + json.dumps(o) + "\n\n" for o in objs).encode()


class TestPathFilter(unittest.TestCase):
    def test_completion_paths(self):
        self.assertTrue(metrics.is_completion_path("/v1/chat/completions"))
        self.assertTrue(metrics.is_completion_path("/v1/completions"))
        self.assertTrue(metrics.is_completion_path("/v1/chat/completions/"))
        self.assertTrue(metrics.is_completion_path("/v1/chat/completions?x=1"))

    def test_non_completion_paths(self):
        self.assertFalse(metrics.is_completion_path("/v1/embeddings"))
        self.assertFalse(metrics.is_completion_path("/v1/models"))
        self.assertFalse(metrics.is_completion_path("/health"))


class TestStreamMetrics(unittest.TestCase):
    def test_chat_deltas_counted(self):
        m = metrics.StreamMetrics(start=0.0)
        m.feed(_sse(
            {"choices": [{"delta": {"role": "assistant"}}]},   # role only, no text
            {"choices": [{"delta": {"content": "he"}}]},
            {"choices": [{"delta": {"content": "llo"}}]},
        ))
        m.feed(b"data: [DONE]\n\n")
        # feed() stamps self.first from a real monotonic clock, so measure the
        # window relative to it rather than an arbitrary end.
        ttft_ms, tps, tokens = m.result(end=m.first + 1.0)
        self.assertEqual(tokens, 2)
        self.assertIsNotNone(ttft_ms)
        self.assertEqual(tps, 2.0)

    def test_legacy_completion_text(self):
        m = metrics.StreamMetrics(start=0.0)
        m.feed(_sse({"choices": [{"text": "a"}]}, {"choices": [{"text": "b"}]}))
        _ttft, _tps, tokens = m.result(end=1.0)
        self.assertEqual(tokens, 2)

    def test_usage_overrides_delta_count(self):
        m = metrics.StreamMetrics(start=0.0)
        m.feed(_sse(
            {"choices": [{"delta": {"content": "x"}}]},
            {"choices": [{"delta": {"content": "y"}}]},
            {"choices": [], "usage": {"completion_tokens": 42}},
        ))
        _ttft, _tps, tokens = m.result(end=1.0)
        self.assertEqual(tokens, 42)

    def test_first_token_time_from_start(self):
        m = metrics.StreamMetrics(start=10.0)
        m.first = 10.5
        m.deltas = 5
        ttft_ms, tps, _tokens = m.result(end=11.0)
        self.assertEqual(ttft_ms, 500)
        self.assertEqual(tps, 10.0)  # 5 tokens over 0.5 s

    def test_no_tokens_yields_none(self):
        m = metrics.StreamMetrics(start=0.0)
        m.feed(_sse({"choices": [{"delta": {"role": "assistant"}}]}))
        ttft_ms, tps, tokens = m.result(end=1.0)
        self.assertEqual(tokens, 0)
        self.assertIsNone(ttft_ms)
        self.assertIsNone(tps)

    def test_split_across_chunks(self):
        m = metrics.StreamMetrics(start=0.0)
        payload = _sse({"choices": [{"delta": {"content": "hi"}}]})
        m.feed(payload[:7])
        m.feed(payload[7:])
        _ttft, _tps, tokens = m.result(end=1.0)
        self.assertEqual(tokens, 1)

    def test_malformed_json_ignored(self):
        m = metrics.StreamMetrics(start=0.0)
        m.feed(b"data: {not json}\n\n")
        m.feed(b": comment line\n\n")
        _ttft, _tps, tokens = m.result(end=1.0)
        self.assertEqual(tokens, 0)

    def test_realistic_chat_stream(self):
        # A response shaped like a real OpenAI-compatible chat stream: an opening
        # role-only chunk, content deltas, a finish_reason chunk, then [DONE].
        raw = (
            b'data: {"choices":[{"index":0,"delta":{"role":"assistant"},'
            b'"finish_reason":null}]}\n\n'
            b'data: {"choices":[{"index":0,"delta":{"content":"Hello"},'
            b'"finish_reason":null}]}\n\n'
            b'data: {"choices":[{"index":0,"delta":{"content":" world"},'
            b'"finish_reason":null}]}\n\n'
            b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n'
        )
        m = metrics.StreamMetrics(start=0.0)
        # deliver in odd-sized slices to exercise buffering across chunk edges
        for i in range(0, len(raw), 13):
            m.feed(raw[i:i + 13])
        _ttft, _tps, tokens = m.result(end=m.first + 1.0)
        self.assertEqual(tokens, 2)  # "Hello" and " world"; role/finish carry no text


class TestUsageFromJson(unittest.TestCase):
    def test_reads_completion_tokens(self):
        body = json.dumps({"usage": {"completion_tokens": 17}}).encode()
        self.assertEqual(metrics.usage_from_json(body), 17)

    def test_missing_usage(self):
        self.assertIsNone(metrics.usage_from_json(b'{"choices": []}'))

    def test_garbage(self):
        self.assertIsNone(metrics.usage_from_json(b"not json"))


class TestStatsFile(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("FXLLA_STATS_FILE", "XDG_STATE_HOME")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_env_wins(self):
        os.environ["FXLLA_STATS_FILE"] = "/custom/s.jsonl"
        os.environ["XDG_STATE_HOME"] = "/state"
        self.assertEqual(metrics.stats_file(), "/custom/s.jsonl")

    def test_xdg_state_home(self):
        os.environ["XDG_STATE_HOME"] = "/state"
        self.assertEqual(metrics.stats_file(), "/state/fxlla/stats.jsonl")

    def test_default_under_home(self):
        self.assertTrue(metrics.stats_file().endswith(
            os.path.join(".local", "state", "fxlla", "stats.jsonl")))


class TestAppendSample(unittest.TestCase):
    def test_append_and_trim(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "stats.jsonl")
            for i in range(50):
                metrics.append_sample(
                    path, metrics.build_sample(i, "m", "mlx", 100, 10, 5.0), cap=10)
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertLessEqual(len(lines), 10)
            last = json.loads(lines[-1])
            self.assertEqual(last["ts"], 49)
            self.assertEqual(last["source"], "gateway")
            self.assertEqual(last["model"], "m")

    def test_sample_shape(self):
        s = metrics.build_sample(1234, "coder", "gguf", 2048.7, None, None)
        self.assertEqual(s["ts"], 1234)
        self.assertEqual(s["ram_mb"], 2048)
        self.assertIsNone(s["ttft_ms"])
        self.assertIsNone(s["tps"])


if __name__ == "__main__":
    unittest.main()
