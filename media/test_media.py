import importlib
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
media = importlib.import_module("generate")
media_mcp = importlib.import_module("media_mcp")


def _tiny_png(path):
    """Write a minimal valid 1x1 PNG (> 1 KB via a padding chunk)."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    pad = chunk(b"tEXt", b"x" * 2000)  # push size over the 1 KB floor
    with open(path, "wb") as f:
        f.write(media.PNG_MAGIC + chunk(b"IHDR", ihdr) + pad
                + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


class TestBuildCommand(unittest.TestCase):
    def test_turbo_defaults_steps(self):
        cmd = media.build_command(media.MODELS["z-image-turbo"], "cat", "/o.png")
        self.assertEqual(cmd[0], "mflux-generate-z-image-turbo")
        self.assertIn("--steps", cmd)
        self.assertEqual(cmd[cmd.index("--steps") + 1], "8")
        self.assertIn("--prompt", cmd)
        self.assertNotIn("--base-model", cmd)

    def test_flux1_passes_base_model(self):
        cmd = media.build_command(media.MODELS["schnell"], "cat", "/o.png")
        self.assertEqual(cmd[0], "mflux-generate")
        self.assertIn("--base-model", cmd)
        self.assertEqual(cmd[cmd.index("--base-model") + 1], "schnell")

    def test_explicit_steps_override(self):
        cmd = media.build_command(media.MODELS["z-image-turbo"], "cat", "/o.png", steps=3)
        self.assertEqual(cmd[cmd.index("--steps") + 1], "3")

    def test_aspect_excludes_width_height(self):
        cmd = media.build_command(media.MODELS["z-image-turbo"], "cat", "/o.png",
                                  aspect="1:1", width=512, height=512)
        self.assertIn("--aspect", cmd)
        self.assertNotIn("--width", cmd)

    def test_width_height_without_aspect(self):
        cmd = media.build_command(media.MODELS["z-image-turbo"], "cat", "/o.png",
                                  width=768, height=512)
        self.assertEqual(cmd[cmd.index("--width") + 1], "768")
        self.assertEqual(cmd[cmd.index("--height") + 1], "512")

    def test_low_ram_and_metadata_flags(self):
        cmd = media.build_command(media.MODELS["z-image-turbo"], "cat", "/o.png",
                                  low_ram=True, metadata=True)
        self.assertIn("--low-ram", cmd)
        self.assertIn("--metadata", cmd)

    def test_no_steps_default_omits_flag(self):
        cmd = media.build_command(media.MODELS["dev"], "cat", "/o.png")
        self.assertNotIn("--steps", cmd)


class TestValidateOutput(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(RuntimeError):
            media.validate_output("/no/such/file.png")

    def test_too_small(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(media.PNG_MAGIC + b"short")
            path = f.name
        try:
            with self.assertRaises(RuntimeError):
                media.validate_output(path)
        finally:
            os.unlink(path)

    def test_not_a_png(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x00" * 4096)
            path = f.name
        try:
            with self.assertRaises(RuntimeError):
                media.validate_output(path)
        finally:
            os.unlink(path)

    def test_valid_png_passes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ok.png")
            _tiny_png(path)
            media.validate_output(path)  # must not raise


class TestUnknownModel(unittest.TestCase):
    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError):
            media.generate_image("cat", model="does-not-exist")

    def test_empty_prompt_raises(self):
        with self.assertRaises(ValueError):
            media.generate_image("")


class TestBuildVideoCommand(unittest.TestCase):
    def test_stage_and_mandatory_frame_rate(self):
        cmd = media.build_video_command("a cat", "/o.mp4", bin_path="ltx")
        self.assertEqual(cmd[:3], ["ltx", "generate", "--distilled"])
        self.assertIn("--frame-rate", cmd)
        self.assertEqual(cmd[cmd.index("--frame-rate") + 1], "24")
        self.assertIn("--prompt", cmd)

    def test_stage_override(self):
        cmd = media.build_video_command("a cat", "/o.mp4", stage="two-stages-hq", bin_path="ltx")
        self.assertIn("--two-stages-hq", cmd)
        self.assertNotIn("--distilled", cmd)

    def test_unknown_stage_raises(self):
        with self.assertRaises(ValueError):
            media.build_video_command("a cat", "/o.mp4", stage="nonsense")

    def test_optional_flags(self):
        cmd = media.build_video_command("a cat", "/o.mp4", frames=49, width=768,
                                        height=512, seed=42, low_ram=True, bin_path="ltx")
        for flag, val in (("--frames", "49"), ("--width", "768"),
                          ("--height", "512"), ("--seed", "42")):
            self.assertEqual(cmd[cmd.index(flag) + 1], val)
        self.assertIn("--low-ram", cmd)

    def test_uses_configured_bin(self):
        cmd = media.build_video_command("a cat", "/o.mp4", bin_path="/venv/bin/ltx-2-mlx")
        self.assertEqual(cmd[0], "/venv/bin/ltx-2-mlx")


class TestValidateVideoOutput(unittest.TestCase):
    def test_missing(self):
        with self.assertRaises(RuntimeError):
            media.validate_video_output("/no/such.mp4")

    def test_not_mp4(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"\x00" * 20480)  # big enough, but no ftyp box
            path = f.name
        try:
            with self.assertRaises(RuntimeError):
                media.validate_video_output(path)
        finally:
            os.unlink(path)

    def test_valid_mp4_passes(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20480)
            path = f.name
        try:
            media.validate_video_output(path)  # must not raise
        finally:
            os.unlink(path)

    def test_empty_prompt_raises(self):
        with self.assertRaises(ValueError):
            media.generate_video("")


class TestMCP(unittest.TestCase):
    def test_initialize(self):
        r = media_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "fxlla-media")

    def test_tools_list(self):
        r = media_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({t["name"] for t in r["result"]["tools"]},
                         {"generate_image", "generate_video"})

    def test_unknown_tool(self):
        r = media_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        self.assertIn("error", r)

    def test_generate_requires_prompt(self):
        self.assertTrue(media_mcp.run_generate({}).startswith("error"))

    def test_generate_video_requires_prompt(self):
        self.assertTrue(media_mcp.run_generate_video({}).startswith("error"))


if __name__ == "__main__":
    unittest.main()
