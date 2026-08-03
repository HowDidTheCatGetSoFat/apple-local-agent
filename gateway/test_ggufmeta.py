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

    def test_a_file_with_no_context_key_answers_nothing(self):
        path = self._model([("general.architecture", STR, _string("mystery"))])
        self.assertIsNone(ggufmeta.trained_context(path))

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

    def test_an_unreadable_model_leaves_the_caller_on_its_default(self):
        directory = os.path.join(self.dir, "empty")
        os.makedirs(directory, exist_ok=True)
        self.assertEqual(ggufmeta.serve_plan(directory, 32768), (None, False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
