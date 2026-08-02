import importlib
import json
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

class TestVisionForModelsThatCannotSee(unittest.TestCase):
    """An image reaching a text model used to be a crash.

    `mlx_lm.server` raises "Only 'text' content type is supported" on any
    non-text part, so sending a picture to a coding model failed outright. The
    gateway now reads it with a model that can and hands the chosen one a
    description: one request in, one answer out, two models used. The point is
    that the capability lives behind the endpoint rather than in whatever is
    calling it - a client with no MCP support at all still gets it.
    """

    def setUp(self):
        # The description cache lives at module scope, which makes any test
        # touching it order-dependent on every other one. Cleared per test so a
        # cached description from an earlier case cannot answer a later one.
        gw._SEEN.clear()
        del gw._SEEN_ORDER[:]
        self.addCleanup(gw._SEEN.clear)
        self.addCleanup(lambda: gw._SEEN_ORDER.__delitem__(slice(None)))

    def _body(self, model="coder", text="what is this?"):
        return {"model": model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}

    def _stub_reader(self, answer="a red square with the word HOLA"):
        calls = []

        def fake(part, asked):
            calls.append((part, asked))
            return "seer", answer

        saved = gw._read_image
        gw._read_image = fake
        self.addCleanup(setattr, gw, "_read_image", saved)
        return calls

    def _stub_roles(self, vision=("seer",)):
        """Which models the catalog declares as readers.

        Only the catalog is faked. _can_see is left real so that what these
        tests exercise is the actual rule - declaration and projector both -
        rather than a stub standing in for it. Each declared model gets a
        projector on disk so the pair genuinely agrees."""
        for alias in vision:
            self._make_model(alias, projector=True)
        saved_role = gw._role_aliases
        gw._role_aliases = lambda role: set(vision) if role == "vision" else set()
        self.addCleanup(setattr, gw, "_role_aliases", saved_role)

    def test_the_image_becomes_text_for_a_model_that_cannot_see(self):
        self._stub_roles()
        self._stub_reader()
        body = self._body()
        self.assertEqual(gw.add_vision(body, "coder"), "seer")
        parts = body["messages"][0]["content"]
        self.assertTrue(all(p["type"] == "text" for p in parts))
        self.assertIn("HOLA", parts[1]["text"])

    def test_the_replacement_says_it_is_a_description(self):
        # The answering model must not reply as though it had seen the image.
        self._stub_roles()
        self._stub_reader()
        body = self._body()
        gw.add_vision(body, "coder")
        said = body["messages"][0]["content"][1]["text"]
        self.assertIn("an image was attached", said)
        self.assertIn("seer", said)

    def test_a_model_that_sees_gets_the_image_itself(self):
        # A description is strictly lossier than the thing it describes.
        self._stub_roles()
        calls = self._stub_reader()
        body = self._body(model="seer")
        self.assertIsNone(gw.add_vision(body, "seer"))
        self.assertEqual(calls, [])
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image_url")

    def test_a_request_without_an_image_is_untouched(self):
        self._stub_roles()
        calls = self._stub_reader()
        body = {"model": "coder", "messages": [{"role": "user", "content": "hola"}]}
        before = json.dumps(body, sort_keys=True)
        self.assertIsNone(gw.add_vision(body, "coder"))
        self.assertEqual(json.dumps(body, sort_keys=True), before)
        self.assertEqual(calls, [])

    def test_every_image_is_read_not_only_the_first(self):
        self._stub_roles()
        calls = self._stub_reader()
        body = self._body()
        body["messages"][0]["content"].append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}})
        gw.add_vision(body, "coder")
        self.assertEqual(len(calls), 2)

    def test_the_users_words_reach_the_reader_as_context(self):
        # Relevance without confirmation: the reader is told what was asked so
        # it covers the right things, and told to report rather than verify.
        self._stub_roles()
        calls = self._stub_reader()
        gw.add_vision(self._body(text="is the lettering right?"), "coder")
        self.assertEqual(calls[0][1], "is the lettering right?")

    def test_it_can_be_turned_off(self):
        self._stub_roles()
        calls = self._stub_reader()
        saved = gw.VISION_ROUTING
        gw.VISION_ROUTING = False
        self.addCleanup(setattr, gw, "VISION_ROUTING", saved)
        body = self._body()
        self.assertIsNone(gw.add_vision(body, "coder"))
        self.assertEqual(calls, [])

    def test_no_vision_model_is_an_error_naming_the_fix(self):
        # Silence would forward the image and surface as the backend's own
        # "Only 'text' content type is supported", which names nothing useful.
        self._stub_roles(vision=())
        saved = os.environ.pop("FXLLA_VISION_MODEL", None)
        self.addCleanup(lambda: os.environ.__setitem__("FXLLA_VISION_MODEL", saved)
                        if saved is not None else None)
        with self.assertRaises(RuntimeError) as ctx:
            gw._read_image({"type": "image_url"}, "")
        self.assertIn("role 'vision'", str(ctx.exception))

    def test_the_words_around_the_image_survive(self):
        # Only the image slot may change. An over-eager rewrite that dropped or
        # mangled the user's own text would be invisible to every assertion
        # that only inspects the image slot.
        self._stub_roles()
        self._stub_reader()
        body = self._body(text="fix the parser")
        gw.add_vision(body, "coder")
        self.assertEqual(body["messages"][0]["content"][0],
                         {"type": "text", "text": "fix the parser"})

    def test_each_image_is_replaced_in_its_own_slot(self):
        self._stub_roles()
        saved = gw._read_image
        gw._read_image = lambda part, asked: ("seer", part["image_url"]["url"])
        self.addCleanup(setattr, gw, "_read_image", saved)
        body = self._body()
        body["messages"][0]["content"].append(
            {"type": "image_url", "image_url": {"url": "SECOND"}})
        gw.add_vision(body, "coder")
        parts = body["messages"][0]["content"]
        self.assertIn("data:image/png;base64,AAAA", parts[1]["text"])
        self.assertIn("SECOND", parts[2]["text"])

    def test_a_malformed_body_is_not_reported_as_a_vision_failure(self):
        # This runs on EVERY request. Anything raising here turns a request
        # that used to be forwarded into a 502 blamed on vision.
        self._stub_roles()
        self._stub_reader()
        for messages in ([None], ["a string"], [{"content": None}],
                         [{"content": ["not a dict"]}], "not a list", None):
            body = {"model": "coder", "messages": messages}
            self.assertIsNone(gw.add_vision(body, "coder"), repr(messages))

    def test_a_non_string_text_part_does_not_abort_the_read(self):
        self._stub_roles()
        calls = self._stub_reader()
        body = self._body()
        body["messages"][0]["content"][0] = {"type": "text", "text": {"oops": 1}}
        gw.add_vision(body, "coder")
        self.assertEqual(len(calls), 1)

    def test_it_reads_the_latest_user_turn_for_context(self):
        self._stub_roles()
        calls = self._stub_reader()
        body = self._body(text="the newest question")
        body["messages"].insert(0, {"role": "user", "content": "an older one"})
        body["messages"].insert(1, {"role": "assistant", "content": "sure"})
        gw.add_vision(body, "coder")
        self.assertEqual(calls[0][1], "the newest question")

    def _make_model(self, alias, projector=False):
        """A model directory, optionally with a multimodal projector in it."""
        path = os.path.join(_MODELS, alias)
        os.makedirs(path, exist_ok=True)
        if projector:
            open(os.path.join(path, "mmproj-f16.gguf"), "wb").close()
        return path

    def test_a_projector_is_what_reaches_llama_server(self):
        # bin/fxlla passes --mmproj when it finds one, so that file alone
        # decides whether an image can physically reach the model.
        self._make_model("no-eyes")
        self._make_model("has-eyes", projector=True)
        self.assertFalse(gw._has_projector("no-eyes"))
        self.assertTrue(gw._has_projector("has-eyes"))

    def test_an_undeclared_projector_does_not_make_a_model_trusted(self):
        # The regression this exists for: a model that ships a vision tower it
        # inherited and never tuned. The file is there, so it COULD be handed
        # the image, but nobody declared the eyes worth using.
        self._make_model("inherited-eyes", projector=True)
        self._stub_roles()
        self.assertTrue(gw._has_projector("inherited-eyes"))
        self.assertFalse(gw._can_see("inherited-eyes"))

    def test_a_declaration_without_a_projector_is_not_enough_either(self):
        # The other direction: the catalog can claim a role the disk cannot
        # honour, and a pull that fetched no projector is exactly that.
        # _stub_roles is bypassed here on purpose - it lays down a projector
        # for what it declares, which is the very thing being withheld.
        self._make_model("claims-eyes")
        saved = gw._role_aliases
        gw._role_aliases = lambda role: {"claims-eyes"} if role == "vision" else set()
        self.addCleanup(setattr, gw, "_role_aliases", saved)
        self.assertFalse(gw._can_see("claims-eyes"))

    def test_both_together_are_what_forwards_an_image_untouched(self):
        self._make_model("real-eyes", projector=True)
        self._stub_roles(vision=["real-eyes"])
        self.assertTrue(gw._can_see("real-eyes"))

    def test_an_undeclared_projector_still_gets_a_description(self):
        # End to end: the model could have taken the image, and is handed a
        # description anyway because its vision was never declared.
        self._make_model("inherited-eyes", projector=True)
        self._stub_roles()
        calls = self._stub_reader()
        body = self._body()
        self.assertEqual(gw.add_vision(body, "inherited-eyes"), "seer")
        self.assertEqual(len(calls), 1)
        self.assertEqual(body["messages"][-1]["content"][1]["type"], "text")

    def test_a_missing_catalog_falls_back_to_the_projector(self):
        # With the catalog moved there is no declaration to read AND no reader
        # to find, so demanding one would turn a working vision model into a
        # 502. The projector is the only evidence left, so it decides.
        self._make_model("has-eyes", projector=True)
        self._make_model("no-eyes")
        saved = gw.CATALOG
        gw.CATALOG = "/nonexistent/models.conf"
        self.addCleanup(setattr, gw, "CATALOG", saved)
        self.assertTrue(gw._can_see("has-eyes"))
        self.assertFalse(gw._can_see("no-eyes"))

    def test_an_image_free_request_never_touches_the_disk(self):
        # add_vision runs on every request. Answering "is there an image" in
        # memory first keeps the common case off the filesystem entirely.
        def explode(alias):
            raise AssertionError("the disk was read for a request with no image")

        saved = gw._has_projector
        gw._has_projector = explode
        self.addCleanup(setattr, gw, "_has_projector", saved)
        body = {"messages": [{"role": "user", "content": "no image here"}]}
        self.assertIsNone(gw.add_vision(body, "coder"))

    def test_an_override_naming_a_blind_model_is_refused(self):
        # Sending an image to a text model would surface as a vision failure
        # blaming the reader rather than the misconfiguration.
        os.makedirs(os.path.join(_MODELS, "no-eyes"), exist_ok=True)
        os.environ["FXLLA_VISION_MODEL"] = "no-eyes"
        self.addCleanup(os.environ.pop, "FXLLA_VISION_MODEL", None)
        with self.assertRaises(RuntimeError) as ctx:
            gw._vision_alias()
        self.assertIn("projector", str(ctx.exception))

    def test_the_same_image_is_read_once_across_turns(self):
        # An OpenAI client resends the whole conversation every turn. Without a
        # cache the picture from turn one is re-read on every later turn, paying
        # its cost again and describing it differently each time, so the
        # answering model watches the same image change its mind.
        self._stub_roles()
        calls = self._stub_reader()
        first = self._body()
        gw.add_vision(first, "coder")
        second = self._body()          # same image, next turn
        gw.add_vision(second, "coder")
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["messages"][0]["content"][1]["text"],
                         second["messages"][0]["content"][1]["text"])

    def test_a_different_image_is_read_again(self):
        self._stub_roles()
        calls = self._stub_reader()
        gw.add_vision(self._body(), "coder")
        other = self._body()
        other["messages"][0]["content"][1]["image_url"]["url"] = "data:image/png;base64,ZZZZ"
        gw.add_vision(other, "coder")
        self.assertEqual(len(calls), 2)

    def test_the_cache_does_not_grow_without_bound(self):
        self._stub_roles()
        self._stub_reader()
        for i in range(gw._SEEN_MAX + 10):
            body = self._body()
            body["messages"][0]["content"][1]["image_url"]["url"] = "u%d" % i
            gw.add_vision(body, "coder")
        self.assertLessEqual(len(gw._SEEN), gw._SEEN_MAX)
        self.assertEqual(len(gw._SEEN), len(gw._SEEN_ORDER))

    def test_too_many_images_is_refused_with_the_limit_named(self):
        # Each is read serially with its own timeout, so a large batch holds the
        # connection for as long as all of them take.
        self._stub_roles()
        calls = self._stub_reader()
        body = self._body()
        for i in range(gw.MAX_IMAGES):
            body["messages"][0]["content"].append(
                {"type": "image_url", "image_url": {"url": "extra%d" % i}})
        with self.assertRaises(RuntimeError) as ctx:
            gw.add_vision(body, "coder")
        self.assertIn(str(gw.MAX_IMAGES), str(ctx.exception))
        self.assertEqual(calls, [], "nothing should be read once it is refused")

    def test_exactly_the_limit_is_allowed(self):
        # The legitimate case the rule must not catch: comparing a few renders.
        self._stub_roles()
        self._stub_reader()
        body = self._body()
        body["messages"][0]["content"] = [{"type": "text", "text": "compare"}] + [
            {"type": "image_url", "image_url": {"url": "n%d" % i}}
            for i in range(gw.MAX_IMAGES)]
        self.assertEqual(gw.add_vision(body, "coder"), "seer")


if __name__ == "__main__":
    unittest.main()
