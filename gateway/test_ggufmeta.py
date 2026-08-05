"""Tests for reading a served context window out of a GGUF header.

The fixtures are real GGUF headers, written byte by byte. Reading a binary
format from a hand-built file is the only way to know the parser walks it
rather than happening to find the right number.
"""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ggufmeta  # noqa: E402


def _string(text):
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _kv(key, kind, packed):
    return _string(key) + struct.pack("<I", kind) + packed


def write_gguf(path, pairs, tensors=0):
    """A GGUF with exactly these metadata pairs and no tensor data."""
    body = b""
    for key, kind, packed in pairs:
        body += _kv(key, kind, packed)
    header = (b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", tensors)
              + struct.pack("<Q", len(pairs)))
    with open(path, "wb") as fh:
        fh.write(header + body)
    return path


U32, U64, F32, STR, ARR = 4, 10, 6, 8, 9


class TestTrainedContext(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _model(self, pairs, name="m.gguf"):
        return write_gguf(os.path.join(self.dir, name), pairs)

    def test_a_plain_context_length_is_read(self):
        path = self._model([("qwen2vl.context_length", U32, struct.pack("<I", 128000))])
        self.assertEqual(ggufmeta.trained_context(path), 128000)

    def test_the_original_length_wins_over_the_stretched_one(self):
        # A model shipped with YaRN advertises the STRETCHED window as its
        # context_length. Serving there means paying for degraded attention
        # across a window nobody fills; the original length is the honest one.
        path = self._model([
            ("qwen35.context_length", U32, struct.pack("<I", 1048576)),
            ("qwen35.rope.scaling.original_context_length", U32, struct.pack("<I", 262144)),
        ])
        self.assertEqual(ggufmeta.trained_context(path), 262144)

    def test_the_architecture_prefix_does_not_have_to_be_known(self):
        # Keys are namespaced by architecture, and the caller should not have
        # to know which architecture it is holding.
        path = self._model([("somethingnew.context_length", U64, struct.pack("<Q", 4096))])
        self.assertEqual(ggufmeta.trained_context(path), 4096)

    def test_a_header_larger_than_one_read_is_still_walked(self):
        # The first version slurped a fixed 8 MB prefix and parsed that. One
        # publisher's build of an architecture already handled here carries a
        # metadata block past it, and the model silently fell back to the
        # global default with nothing said. The reader pulls more on demand
        # now, so there is no size to get wrong.
        filler = b"".join(_string("x" * 1024) for _ in range(12000))   # ~12 MB
        path = self._model([
            ("tokenizer.ggml.tokens", ARR,
             struct.pack("<I", STR) + struct.pack("<Q", 12000) + filler),
            ("qwen35.context_length", U32, struct.pack("<I", 262144)),
        ])
        self.assertGreater(os.path.getsize(path), 8 << 20)
        self.assertEqual(ggufmeta.trained_context(path), 262144)

    def test_a_file_with_no_context_key_answers_nothing(self):
        path = self._model([("general.architecture", STR, _string("mystery"))])
        self.assertIsNone(ggufmeta.trained_context(path))

    def test_an_impossible_array_count_is_refused_without_walking_it(self):
        # A declared count is a number in a file, not a fact. A header claiming
        # tens of millions of zero-length strings sent the per-element loop
        # spinning for a dozen seconds of blocking CPU - and this parser runs
        # on the path of every /v1/models call, so that is a denial of service
        # from a file sitting in the model store.
        import time
        path = os.path.join(self.dir, "hostile.gguf")
        body = (_string("tokenizer.ggml.tokens") + struct.pack("<I", ARR)
                + struct.pack("<I", STR) + struct.pack("<Q", 40_000_000))
        head = (b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)
                + struct.pack("<Q", 2))
        with open(path, "wb") as fh:
            fh.write(head + body + b"\x00" * (1 << 20))
        started = time.monotonic()
        self.assertIsNone(ggufmeta.trained_context(path))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_an_impossible_pair_count_is_refused(self):
        path = os.path.join(self.dir, "pairs.gguf")
        with open(path, "wb") as fh:
            fh.write(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)
                     + struct.pack("<Q", 5_000_000_000) + b"\x00" * 4096)
        self.assertIsNone(ggufmeta.trained_context(path))

    def test_a_real_vocabulary_sized_array_still_parses(self):
        # The bound has to reject the impossible without rejecting the large:
        # real vocabularies run to hundreds of thousands of entries.
        vocab = b"".join(_string("tok%d" % i) for i in range(200000))
        path = self._model([
            ("tokenizer.ggml.tokens", ARR,
             struct.pack("<I", STR) + struct.pack("<Q", 200000) + vocab),
            ("qwen35.context_length", U32, struct.pack("<I", 131072)),
        ])
        self.assertEqual(ggufmeta.trained_context(path), 131072)

    def test_a_file_that_is_not_a_gguf_answers_nothing(self):
        path = os.path.join(self.dir, "not.gguf")
        with open(path, "wb") as fh:
            fh.write(b"this is not a model")
        self.assertIsNone(ggufmeta.trained_context(path))

    def test_a_long_array_is_stepped_over_not_parsed(self):
        # Vocabularies are hundreds of thousands of strings and sit BEFORE the
        # keys that matter in some files. Parsing them would be slow; failing
        # on them would be worse.
        vocab = b"".join(_string("tok%d" % i) for i in range(500))
        path = self._model([
            ("tokenizer.ggml.tokens", ARR, struct.pack("<I", STR) + struct.pack("<Q", 500) + vocab),
            ("qwen35.context_length", U32, struct.pack("<I", 32768)),
        ])
        self.assertEqual(ggufmeta.trained_context(path), 32768)

    def test_a_numeric_array_is_stepped_over_too(self):
        path = self._model([
            ("qwen35.rope.dimension_sections", ARR,
             struct.pack("<I", U32) + struct.pack("<Q", 4) + struct.pack("<4I", 11, 11, 10, 0)),
            ("qwen35.context_length", U32, struct.pack("<I", 8192)),
        ])
        self.assertEqual(ggufmeta.trained_context(path), 8192)


class TestRopeStretch(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _model(self, pairs, name="m.gguf"):
        return write_gguf(os.path.join(self.dir, name), pairs)

    def test_a_factor_above_one_is_a_stretch(self):
        path = self._model([("qwen35.rope.scaling.factor", F32, struct.pack("<f", 4.0))])
        self.assertTrue(ggufmeta.rope_is_stretched(path))

    def test_a_factor_of_one_is_not(self):
        path = self._model([("qwen35.rope.scaling.factor", F32, struct.pack("<f", 1.0))])
        self.assertFalse(ggufmeta.rope_is_stretched(path))

    def test_no_factor_at_all_is_not(self):
        path = self._model([("qwen2vl.context_length", U32, struct.pack("<I", 128000))])
        self.assertFalse(ggufmeta.rope_is_stretched(path))


class TestMtpHead(unittest.TestCase):
    """A build that can draft against itself.

    Publishers ship two builds of the same weights at the same quant, one with
    the head and one without. The file names are a convention; this key is the
    fact. Measured on the real pair: 40.9 against 21.7 tokens/s on predictable
    text, 31.9 against 20.6 on code.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _model(self, pairs, name="m.gguf"):
        return write_gguf(os.path.join(self.dir, name), pairs)

    def test_a_head_is_detected(self):
        path = self._model([("qwen35.nextn_predict_layers", U32, struct.pack("<I", 1))])
        self.assertTrue(ggufmeta.has_mtp_head(path))

    def test_a_build_without_one_is_not(self):
        path = self._model([("qwen35.context_length", U32, struct.pack("<I", 8192))])
        self.assertFalse(ggufmeta.has_mtp_head(path))

    def test_zero_layers_is_no_head(self):
        # The key present and zero means the build declares the field and has
        # nothing behind it; the flag would then be a promise llama.cpp cannot
        # keep.
        path = self._model([("qwen35.nextn_predict_layers", U32, struct.pack("<I", 0))])
        self.assertFalse(ggufmeta.has_mtp_head(path))

    def test_an_unreadable_file_is_not_assumed_to_have_one(self):
        path = os.path.join(self.dir, "junk.gguf")
        with open(path, "wb") as fh:
            fh.write(b"nope")
        self.assertFalse(ggufmeta.has_mtp_head(path))

    def test_the_architecture_prefix_does_not_matter(self):
        path = self._model([("newarch.nextn_predict_layers", U32, struct.pack("<I", 2))])
        self.assertTrue(ggufmeta.has_mtp_head(path))


class TestServePlan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _model_dir(self, name, pairs, entry="weights.gguf", projector=False):
        directory = os.path.join(self.dir, name)
        os.makedirs(directory, exist_ok=True)
        write_gguf(os.path.join(directory, entry), pairs)
        if projector:
            write_gguf(os.path.join(directory, "mmproj-f16.gguf"),
                       [("clip.has_vision_encoder", U32, struct.pack("<I", 1))])
        with open(os.path.join(directory, ".entry"), "w") as fh:
            fh.write(entry + "\n")
        return directory

    def test_the_cap_bounds_a_large_window(self):
        d = self._model_dir("big", [("a.context_length", U32, struct.pack("<I", 262144))])
        self.assertEqual(ggufmeta.serve_plan(d, 32768)[0], 32768)

    def test_a_small_window_is_not_inflated_to_the_cap(self):
        # The old behaviour: one number for everything, which asked a model
        # for more than it was ever trained on.
        d = self._model_dir("small", [("a.context_length", U32, struct.pack("<I", 4096))])
        self.assertEqual(ggufmeta.serve_plan(d, 32768)[0], 4096)

    def test_no_cap_means_the_trained_window(self):
        d = self._model_dir("free", [("a.context_length", U32, struct.pack("<I", 65536))])
        self.assertEqual(ggufmeta.serve_plan(d, 0)[0], 65536)

    def test_rope_is_turned_off_at_or_below_the_trained_window(self):
        d = self._model_dir("yarn", [
            ("a.context_length", U32, struct.pack("<I", 1048576)),
            ("a.rope.scaling.original_context_length", U32, struct.pack("<I", 262144)),
            ("a.rope.scaling.factor", F32, struct.pack("<f", 4.0)),
        ])
        served, rope_off, _mtp = ggufmeta.serve_plan(d, 262144)
        self.assertEqual(served, 262144)
        self.assertTrue(rope_off)

    def test_rope_is_left_alone_for_a_model_that_does_not_stretch(self):
        d = self._model_dir("plain", [("a.context_length", U32, struct.pack("<I", 128000))])
        self.assertEqual(ggufmeta.serve_plan(d, 262144), (128000, False, False))

    def test_the_projector_is_never_mistaken_for_the_weights(self):
        # A projector has no context_length, so reading it would answer None
        # and drop the model back to the global default silently.
        d = self._model_dir("seeing", [("a.context_length", U32, struct.pack("<I", 128000))],
                            projector=True)
        self.assertEqual(ggufmeta.serve_plan(d, 262144)[0], 128000)

    def test_a_missing_entry_marker_still_finds_the_weights(self):
        d = self._model_dir("bare", [("a.context_length", U32, struct.pack("<I", 8192))],
                            projector=True)
        os.unlink(os.path.join(d, ".entry"))
        self.assertEqual(ggufmeta.serve_plan(d, 262144)[0], 8192)

    def test_a_projector_named_after_the_model_is_still_skipped(self):
        # `<model>.mmproj-Q8_0.gguf` is one publisher's spelling, so the word is
        # not at the front and an anchored startswith test does not see it.
        #
        # The quant tag is lowercase here on purpose, and that is the whole
        # test. With mradermacher's actual uppercase names the old check got
        # the right answer by luck: sorted() compares "Q" (0x51) against "m"
        # (0x6D), so the weights came first and were returned before the
        # projector was ever reached. Lowercase "q" is 0x71, the projector
        # sorts first, and the anchored check then returns it as the weights -
        # a file carrying no context_length, so the window comes back None and
        # the caller silently falls back to a default for a model that
        # declared one.
        directory = os.path.join(self.dir, "late-projector")
        os.makedirs(directory, exist_ok=True)
        write_gguf(os.path.join(directory, "model.q6_k.gguf"),
                   [("a.context_length", U32, struct.pack("<I", 32768))])
        write_gguf(os.path.join(directory, "model.mmproj-f16.gguf"),
                   [("clip.has_vision_encoder", U32, struct.pack("<I", 1))])
        self.assertEqual(sorted(os.listdir(directory))[0], "model.mmproj-f16.gguf",
                         "the fixture must put the projector first or it tests nothing")
        self.assertEqual(ggufmeta.serve_plan(directory, 262144)[0], 32768)

    def test_a_projector_spelled_in_another_case_is_skipped_as_well(self):
        # The pull side matches with `grep -i`, so a `MMProj` spelling gets
        # downloaded. A case-sensitive test here then reads it as the weights
        # and answers about a file with no context_length in it - and because
        # the answer is None, the caller quietly serves a default window for a
        # model that declared one. Filename sorts first so the wrong file is
        # genuinely reachable, not merely possible.
        directory = os.path.join(self.dir, "shouty-projector")
        os.makedirs(directory, exist_ok=True)
        write_gguf(os.path.join(directory, "model.q6_k.gguf"),
                   [("a.context_length", U32, struct.pack("<I", 32768))])
        write_gguf(os.path.join(directory, "model.MMProj-f16.gguf"),
                   [("clip.has_vision_encoder", U32, struct.pack("<I", 1))])
        self.assertEqual(sorted(os.listdir(directory))[0], "model.MMProj-f16.gguf",
                         "the fixture must put the projector first or it tests nothing")
        self.assertEqual(ggufmeta.serve_plan(directory, 262144)[0], 32768)

    def test_a_plan_walks_the_header_once(self):
        # The reason this is fast, and the thing a refactor would quietly undo.
        # It used to ask four separate readers for one fact each, and a key that
        # is ABSENT is only known to be absent at the end of the header - so a
        # model with no MTP head and no rope factor paid three full walks of a
        # 12 MB block. Counted rather than timed: a timing test on a warm page
        # cache proves nothing about how many times the file was read.
        d = self._model_dir("counted", [
            ("a.context_length", U32, struct.pack("<I", 32768)),
        ])
        ggufmeta._FACTS_CACHE.clear()
        calls = []
        real = ggufmeta.metadata
        ggufmeta.metadata = lambda p, k: (calls.append(p), real(p, k))[1]
        try:
            ggufmeta.serve_plan(d, 32768)
        finally:
            ggufmeta.metadata = real
        self.assertEqual(len(calls), 1, "one walk per plan, got %d" % len(calls))

    def test_a_second_plan_does_not_read_the_file_again(self):
        d = self._model_dir("twice", [("a.context_length", U32, struct.pack("<I", 32768))])
        ggufmeta._FACTS_CACHE.clear()
        ggufmeta.serve_plan(d, 32768)
        calls = []
        real = ggufmeta.metadata
        ggufmeta.metadata = lambda p, k: (calls.append(p), real(p, k))[1]
        try:
            self.assertEqual(ggufmeta.serve_plan(d, 32768)[0], 32768)
        finally:
            ggufmeta.metadata = real
        self.assertEqual(calls, [], "the second plan re-read the file")

    def test_the_cache_holds_facts_and_not_the_plan(self):
        # Two callers ask about the same file with different ceilings - the
        # gateway's FXLLA_CTX and a backend launched with another. Caching the
        # ANSWER instead of the facts would serve the first caller's window to
        # the second, which is the class of bug this whole module exists to
        # prevent: a reported context that is not the one being served.
        d = self._model_dir("two-caps", [("a.context_length", U32, struct.pack("<I", 262144))])
        ggufmeta._FACTS_CACHE.clear()
        self.assertEqual(ggufmeta.serve_plan(d, 8192)[0], 8192)
        self.assertEqual(ggufmeta.serve_plan(d, 262144)[0], 262144)
        self.assertEqual(ggufmeta.serve_plan(d, 0)[0], 262144)

    def test_a_rewritten_file_is_read_again(self):
        # Keyed on mtime and size, so replacing the weights - a re-pull, a
        # different quant into the same directory - is a different file and not
        # a stale answer that outlives it.
        d = self._model_dir("replaced", [("a.context_length", U32, struct.pack("<I", 4096))])
        ggufmeta._FACTS_CACHE.clear()
        self.assertEqual(ggufmeta.serve_plan(d, 262144)[0], 4096)
        entry = os.path.join(d, "weights.gguf")
        write_gguf(entry, [("a.context_length", U32, struct.pack("<I", 131072))])
        os.utime(entry, (0, 0))     # a different mtime, whichever way it moved
        self.assertEqual(ggufmeta.serve_plan(d, 262144)[0], 131072)

    def test_the_cache_cannot_grow_without_bound(self):
        # A gateway lives for days and nothing evicts on its own.
        ggufmeta._FACTS_CACHE.clear()
        for i in range(ggufmeta._FACTS_CACHE_MAX + 5):
            d = self._model_dir("many%d" % i,
                                [("a.context_length", U32, struct.pack("<I", 2048))])
            ggufmeta.serve_plan(d, 2048)
        self.assertLessEqual(len(ggufmeta._FACTS_CACHE), ggufmeta._FACTS_CACHE_MAX)

    def test_a_plan_carries_the_mtp_verdict(self):
        d = self._model_dir("speculating", [
            ("a.context_length", U32, struct.pack("<I", 32768)),
            ("a.nextn_predict_layers", U32, struct.pack("<I", 1)),
        ])
        self.assertEqual(ggufmeta.serve_plan(d, 32768), (32768, False, True))

    def test_the_projector_does_not_decide_the_mtp_verdict(self):
        # A projector carries neither key, so reading it instead of the
        # weights would silently answer "no head" for a build that has one.
        d = self._model_dir("both", [
            ("a.context_length", U32, struct.pack("<I", 32768)),
            ("a.nextn_predict_layers", U32, struct.pack("<I", 1)),
        ], projector=True)
        self.assertTrue(ggufmeta.serve_plan(d, 32768)[2])

    def test_stretch_opts_into_the_advertised_window(self):
        # The window a YaRN model headlines is reachable only because the
        # scaling is baked in, so asking for it means accepting what the
        # scaling costs - which is why it is opt-in rather than the default.
        d = self._model_dir("yarn2", [
            ("a.context_length", U32, struct.pack("<I", 1048576)),
            ("a.rope.scaling.original_context_length", U32, struct.pack("<I", 262144)),
            ("a.rope.scaling.factor", F32, struct.pack("<f", 4.0)),
        ])
        served, rope_off, _ = ggufmeta.serve_plan(d, 1048576, stretch=True)
        self.assertEqual(served, 1048576)
        self.assertFalse(rope_off, "the stretch is the reason it is reachable")

    def test_stretch_capped_below_the_trained_window_gets_neither(self):
        # Asking for the stretch and then capping under the trained length
        # buys nothing: the scaling is a cost with no window to show for it.
        d = self._model_dir("yarn3", [
            ("a.context_length", U32, struct.pack("<I", 1048576)),
            ("a.rope.scaling.original_context_length", U32, struct.pack("<I", 262144)),
            ("a.rope.scaling.factor", F32, struct.pack("<f", 4.0)),
        ])
        served, rope_off, _ = ggufmeta.serve_plan(d, 32768, stretch=True)
        self.assertEqual(served, 32768)
        self.assertTrue(rope_off)

    def test_stretch_changes_nothing_for_a_model_without_one(self):
        d = self._model_dir("flat", [("a.context_length", U32, struct.pack("<I", 128000))])
        self.assertEqual(ggufmeta.serve_plan(d, 262144, stretch=True),
                         ggufmeta.serve_plan(d, 262144))

    def test_the_default_is_the_trained_window(self):
        # Stated as a test because it is a choice, not an accident: the
        # headline number is not what gets served unless asked for.
        d = self._model_dir("yarn4", [
            ("a.context_length", U32, struct.pack("<I", 1048576)),
            ("a.rope.scaling.original_context_length", U32, struct.pack("<I", 262144)),
            ("a.rope.scaling.factor", F32, struct.pack("<f", 4.0)),
        ])
        self.assertEqual(ggufmeta.serve_plan(d, 1048576)[0], 262144)

    def test_an_unreadable_model_leaves_the_caller_on_its_default(self):
        directory = os.path.join(self.dir, "empty")
        os.makedirs(directory, exist_ok=True)
        self.assertEqual(ggufmeta.serve_plan(directory, 32768), (None, False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
