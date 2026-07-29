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


class TestBuildEditCommand(unittest.TestCase):
    def test_builds_edit_invocation(self):
        cmd = media.build_edit_command("make it night", "/in.png", "/out.png",
                                       bin_path="qwen-edit")
        self.assertEqual(cmd[0], "qwen-edit")
        self.assertEqual(cmd[cmd.index("--prompt") + 1], "make it night")
        self.assertEqual(cmd[cmd.index("--image-paths") + 1], "/in.png")
        self.assertEqual(cmd[cmd.index("--output") + 1], "/out.png")
        self.assertIn("--quantize", cmd)

    def test_seed_and_quantize(self):
        cmd = media.build_edit_command("x", "/in.png", "/out.png", seed=7,
                                       quantize=4, bin_path="qwen-edit")
        self.assertEqual(cmd[cmd.index("--seed") + 1], "7")
        self.assertEqual(cmd[cmd.index("--quantize") + 1], "4")

    def test_missing_image_raises(self):
        with self.assertRaises(ValueError):
            media.build_edit_command("x", "", "/out.png")

    def test_empty_prompt_raises(self):
        with self.assertRaises(ValueError):
            media.build_edit_command("", "/in.png", "/out.png")

    def test_missing_input_path_raises(self):
        with self.assertRaises(ValueError):
            media.generate_edit("x", "/no/such/input.png")


class TestBuildUpscaleCommand(unittest.TestCase):
    def test_builds_upscale_invocation(self):
        cmd = media.build_upscale_command("/in.png", "/out.png", bin_path="seedvr2")
        self.assertEqual(cmd[0], "seedvr2")
        self.assertEqual(cmd[cmd.index("--image-path") + 1], "/in.png")
        self.assertEqual(cmd[cmd.index("--output") + 1], "/out.png")
        self.assertNotIn("--resolution", cmd)

    def test_scale_maps_to_resolution(self):
        cmd = media.build_upscale_command("/in.png", "/out.png", scale="2x",
                                          bin_path="seedvr2")
        self.assertEqual(cmd[cmd.index("--resolution") + 1], "2x")

    def test_missing_image_raises(self):
        with self.assertRaises(ValueError):
            media.build_upscale_command("", "/out.png")

    def test_missing_input_path_raises(self):
        with self.assertRaises(ValueError):
            media.generate_upscale("/no/such/input.png")


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


class TestFreeGpu(unittest.TestCase):
    def test_keep_skips(self):
        # keep=True must not attempt any network call; simply returns.
        media.free_gpu("image", keep=True)

    def test_no_gateway_is_swallowed(self):
        # Point at a closed port; connection refused must be caught, not raised.
        saved = (media.GATEWAY_PORT, media.KEEP_MODELS)
        try:
            media.GATEWAY_PORT = "9"  # discard port, nothing listening
            media.KEEP_MODELS = False
            media.free_gpu("video")  # must not raise
        finally:
            media.GATEWAY_PORT, media.KEEP_MODELS = saved


class TestMCP(unittest.TestCase):
    def test_initialize(self):
        r = media_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "fxlla-media")

    def test_tools_list(self):
        r = media_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({t["name"] for t in r["result"]["tools"]},
                         {"generate_image", "generate_video", "generate_speech",
                          "edit_image", "upscale_image"})

    def test_edit_and_upscale_require_image(self):
        tools = {t["name"]: t for t in
                 media_mcp.handle({"jsonrpc": "2.0", "id": 9,
                                   "method": "tools/list"})["result"]["tools"]}
        self.assertIn("image", tools["edit_image"]["inputSchema"]["required"])
        self.assertIn("prompt", tools["edit_image"]["inputSchema"]["required"])
        self.assertEqual(tools["upscale_image"]["inputSchema"]["required"], ["image"])

    def test_unknown_tool(self):
        r = media_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        self.assertIn("error", r)

    def test_generate_requires_prompt(self):
        self.assertTrue(media_mcp.run_generate({}).startswith("error"))

    def test_generate_video_requires_prompt(self):
        self.assertTrue(media_mcp.run_generate_video({}).startswith("error"))

    def test_generate_speech_requires_text(self):
        self.assertTrue(media_mcp.run_generate_speech({}).startswith("error"))

    def test_edit_requires_image(self):
        self.assertTrue(media_mcp.run_edit({"prompt": "x"}).startswith("error"))

    def test_edit_requires_prompt(self):
        self.assertTrue(media_mcp.run_edit({"image": "/in.png"}).startswith("error"))

    def test_upscale_requires_image(self):
        self.assertTrue(media_mcp.run_upscale({}).startswith("error"))


class TestBuildVoiceCommand(unittest.TestCase):
    def test_requires_reference(self):
        with self.assertRaises(ValueError):
            media.build_voice_command("hola", "/o.wav", ref="")

    def test_builds_backend_invocation(self):
        cmd = media.build_voice_command("hola", "/o.wav", ref="/r.wav",
                                        model="repo/x", lang="es", speed=1.1,
                                        python="/venv/bin/python", backend="/b.py")
        self.assertEqual(cmd[0], "/venv/bin/python")
        self.assertEqual(cmd[1], "/b.py")
        self.assertEqual(cmd[cmd.index("--text") + 1], "hola")
        self.assertEqual(cmd[cmd.index("--ref") + 1], "/r.wav")
        self.assertEqual(cmd[cmd.index("--lang") + 1], "es")
        self.assertEqual(cmd[cmd.index("--model") + 1], "repo/x")
        self.assertEqual(cmd[cmd.index("--speed") + 1], "1.1")

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            media.generate_speech("", ref="/r.wav")


class TestValidateWavOutput(unittest.TestCase):
    def test_missing(self):
        with self.assertRaises(RuntimeError):
            media.validate_wav_output("/no/such.wav")

    def test_not_wav(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"\x00" * 4096)
            path = f.name
        try:
            with self.assertRaises(RuntimeError):
                media.validate_wav_output(path)
        finally:
            os.unlink(path)

    def test_valid_wav_passes(self):
        import wave
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ok.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x01" * 2048)
            media.validate_wav_output(path)  # must not raise


if __name__ == "__main__":
    unittest.main()
