import contextlib
import importlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
media = importlib.import_module("generate")
media_mcp = importlib.import_module("media_mcp")
jobs = importlib.import_module("jobs")
quality = importlib.import_module("quality")


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

    def test_aspect_with_width_height_is_refused(self):
        # This test used to assert the opposite - that aspect silently won and
        # the dimensions were dropped - which is how a request for 512x512
        # produced a 1024x1024 file with nothing said. The old behavior was
        # written down as intent, so the suite defended the bug.
        with self.assertRaises(ValueError):
            media.build_command(media.MODELS["z-image-turbo"], "cat", "/o.png",
                                aspect="1:1", width=512, height=512)

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
                          "edit_image", "upscale_image", "media_job_status",
                          "list_loras", "list_media_models",
                          "list_media_jobs", "cancel_media_job"})

    def test_discovery_and_waiting_exist(self):
        # Both come from watching a real session: the model read the source
        # ten times to learn what it could do, and polled a job 47 times.
        tools = {t["name"]: t for t in
                 media_mcp.handle({"jsonrpc": "2.0", "id": 1,
                                   "method": "tools/list"})["result"]["tools"]}
        self.assertIn("list_media_models", tools)
        self.assertIn("wait_s",
                      tools["media_job_status"]["inputSchema"]["properties"])
        image = tools["generate_image"]["inputSchema"]["properties"]
        for opt in ("negative", "loras", "lora_style", "init_image"):
            self.assertIn(opt, image)

    def test_generators_expose_async(self):
        tools = {t["name"]: t for t in
                 media_mcp.handle({"jsonrpc": "2.0", "id": 10,
                                   "method": "tools/list"})["result"]["tools"]}
        for name in ("generate_image", "generate_video", "generate_speech",
                     "edit_image", "upscale_image"):
            self.assertIn("async", tools[name]["inputSchema"]["properties"], name)
        # The job tools are not generators, so they take no async flag.
        self.assertNotIn("async",
                         tools["media_job_status"]["inputSchema"]["properties"])

    def test_job_tools_require_id(self):
        self.assertTrue(media_mcp.run_job_status({}).startswith("error"))
        self.assertTrue(media_mcp.run_cancel_job({}).startswith("error"))

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


def _write_wav(path, frames, rate=24000, width=2, channels=1):
    import array, wave
    codes = {2: "h", 4: "i"}
    with wave.open(path, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(width); w.setframerate(rate)
        if width == 1:  # 8-bit WAV is unsigned, centered on 128
            w.writeframes(bytes((f + 128) & 0xFF for f in frames))
        elif width == 3:  # 24-bit little-endian
            w.writeframes(b"".join((f & 0xFFFFFF).to_bytes(3, "little") for f in frames))
        else:
            w.writeframes(array.array(codes[width], frames).tobytes())


class TestQualityAudio(unittest.TestCase):
    # Thresholds are deliberately far from real speech (measured peak ~0.95,
    # rms ~0.13), so these fixtures are unambiguous garbage.
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rate = 24000

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def _tone(self, seconds, amp=9000, freq=180, offset=0):
        import math
        n = int(self.rate * seconds)
        return [int(amp * math.sin(2 * math.pi * freq * t / self.rate)) + offset
                for t in range(n)]

    def test_pure_silence_is_rejected(self):
        p = self._path("s.wav"); _write_wav(p, [0] * self.rate * 2)
        self.assertTrue(any("silent" in x for x in quality.check_wav(p)))

    def test_click_then_silence_is_rejected(self):
        # Passes a peak check but is empty to a listener.
        p = self._path("c.wav"); _write_wav(p, [20000] * 50 + [0] * (self.rate * 3))
        self.assertTrue(any("silent for" in x for x in quality.check_wav(p)))

    def test_dc_offset_is_rejected(self):
        p = self._path("dc.wav"); _write_wav(p, self._tone(2, amp=8000, offset=12000))
        self.assertTrue(any("DC offset" in x for x in quality.check_wav(p)))

    def test_speech_like_signal_passes(self):
        # 0.4s of tone, 0.3s of silence, repeated: 43 percent pauses.
        seg = self._tone(0.4)
        gap = [0] * int(self.rate * 0.3)
        p = self._path("ok.wav"); _write_wav(p, (seg + gap) * 4)
        self.assertEqual(quality.check_wav(p), [])

    def test_every_supported_sample_width(self):
        for width in (1, 2, 3, 4):
            p = self._path("w%d.wav" % width)
            amp = {1: 60, 2: 9000, 3: 2000000, 4: 500000000}[width]
            _write_wav(p, self._tone(0.5, amp=amp), width=width)
            self.assertEqual(quality.check_wav(p), [], "width %d" % width)

    def test_24_bit_sign_extension_round_trips(self):
        # The only hand-rolled arithmetic in the module: three little-endian
        # bytes, sign-extended. Check the extremes survive it.
        extremes = [-8388608, -1, 0, 1, 8388607]
        p = self._path("s24.wav"); _write_wav(p, extremes, width=3)
        samples, full_scale, _ch, _rate = quality._samples(p)
        self.assertEqual(list(samples), extremes)
        self.assertEqual(full_scale, 8388608.0)

    def test_a_constant_waveform_is_silence_not_distortion(self):
        # All-zero bytes in an 8-bit file center to -128: full-scale by peak,
        # which used to be reported as distortion.
        p = self._path("z8.wav"); _write_wav(p, [-128] * self.rate, width=1)
        self.assertTrue(any("silent" in x for x in quality.check_wav(p)))

    def test_content_at_the_end_is_not_called_silent(self):
        # The windowed loop used to drop the trailing partial window, so the
        # verdict depended on where in the file the content sat.
        tone = self._tone(0.03, amp=30000)
        lead = [0] * int(self.rate * 0.20)
        tail = self._path("tail.wav"); _write_wav(tail, lead + tone)
        head = self._path("head.wav"); _write_wav(head, tone + lead)
        self.assertEqual(quality.check_wav(tail), quality.check_wav(head))
        self.assertEqual(quality.check_wav(tail), [])

    def test_zero_frame_file_is_reported(self):
        import wave as wavemod
        p = self._path("empty.wav")
        with wavemod.open(p, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.rate)
        self.assertEqual(quality.check_wav(p), ["contains no audio frames"])

    def test_unparseable_format_gets_no_verdict(self):
        # Python's wave reads PCM only. A float32 WAV is valid output this module
        # cannot measure, and "cannot measure" must never mean "reject".
        import struct as st
        n = 2000
        data = b"".join(st.pack("<f", 0.5) for _ in range(n))
        header = (b"RIFF" + st.pack("<I", 36 + len(data)) + b"WAVEfmt "
                  + st.pack("<IHHIIHH", 16, 3, 1, self.rate, self.rate * 4, 4, 32)
                  + b"data" + st.pack("<I", len(data)))
        p = self._path("f32.wav")
        with open(p, "wb") as f:
            f.write(header + data)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(quality.check_wav(p), [])

    def test_unreadable_file_gets_no_verdict(self):
        # A file this module cannot parse must not be rejected: the container
        # checks already passed, and guessing would reject valid output.
        p = self._path("bad.wav")
        with open(p, "wb") as f:
            f.write(b"RIFF____WAVEnonsense")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(quality.check_wav(p), [])

    def test_report_joins_problems_or_returns_none(self):
        self.assertIsNone(quality.report("audio", "/x.wav", []))
        one = quality.report("audio", "/x.wav", ["is silent"])
        self.assertIn("/x.wav", one)
        both = quality.report("audio", "/x.wav", ["is silent", "has a DC offset"])
        self.assertIn("; and ", both)


class TestVideoFacts(unittest.TestCase):
    # The measured truth about a produced file, so a caller cannot report its
    # own request back as the result: one called 49 frames at 24 fps "about 10
    # seconds" (2.04) and declared a 4-8 second requirement met.
    def _probe(self, payload):
        saved = quality._ffprobe
        quality._ffprobe = lambda _p: payload
        return saved

    def test_duration_frames_and_fps_are_read(self):
        saved = self._probe({"streams": [{"nb_frames": "49", "width": 512,
                                          "height": 512, "r_frame_rate": "24/1",
                                          "duration": "2.041667"}]})
        try:
            f = quality.video_facts("/x.mp4")
        finally:
            quality._ffprobe = saved
        self.assertEqual(f["duration_s"], 2.04)
        self.assertEqual(f["frames"], 49)
        self.assertEqual(f["fps"], 24.0)
        self.assertEqual((f["width"], f["height"]), (512, 512))

    def test_container_duration_is_the_fallback(self):
        saved = self._probe({"streams": [{"nb_frames": "10"}],
                             "format": {"duration": "0.5"}})
        try:
            self.assertEqual(quality.video_facts("/x.mp4")["duration_s"], 0.5)
        finally:
            quality._ffprobe = saved

    def test_no_ffprobe_yields_no_facts_rather_than_guesses(self):
        saved = self._probe(None)
        try:
            self.assertEqual(quality.video_facts("/x.mp4"), {})
        finally:
            quality._ffprobe = saved

    def test_unparseable_rate_is_omitted_not_invented(self):
        saved = self._probe({"streams": [{"r_frame_rate": "0/0", "duration": "1"}]})
        try:
            f = quality.video_facts("/x.mp4")
        finally:
            quality._ffprobe = saved
        self.assertNotIn("fps", f)
        self.assertEqual(f["duration_s"], 1.0)


class TestQualityImageVideo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _png(self, name, width, height):
        path = os.path.join(self.tmp, name)
        _tiny_png(path)
        with open(path, "r+b") as f:
            f.seek(16)
            f.write(struct.pack(">II", width, height))
        return path

    def test_zero_dimensions_rejected(self):
        self.assertTrue(any("zero dimensions" in x
                            for x in quality.check_png(self._png("z.png", 0, 0))))

    def test_small_image_is_accepted(self):
        # No arbitrary minimum: validate_output's byte floor already catches
        # truncation, and the repo's own fixtures are 1x1.
        self.assertEqual(quality.check_png(self._png("t.png", 1, 1)), [])

    def test_normal_image_passes(self):
        self.assertEqual(quality.check_png(self._png("n.png", 512, 512)), [])

    def test_headerless_file_rejected(self):
        p = os.path.join(self.tmp, "x.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        self.assertTrue(quality.check_png(p))

    @unittest.skipUnless(shutil.which("ffprobe") and shutil.which("ffmpeg"),
                         "needs ffmpeg/ffprobe")
    def test_real_clip_and_user_requested_short_clips_pass(self):
        # Frame count and duration are caller-controlled, so a one-frame or
        # fraction-of-a-second clip is a request, not a defect.
        import subprocess as sp
        for name, args in (
            ("clip.mp4", ["-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=1"]),
            ("one.mp4", ["-f", "lavfi", "-i", "color=c=red:size=64x64:rate=24",
                         "-frames:v", "1"]),
        ):
            path = os.path.join(self.tmp, name)
            sp.run(["ffmpeg", "-v", "error"] + args + ["-pix_fmt", "yuv420p", path],
                   check=True, capture_output=True)
            self.assertEqual(quality.check_video(path), [], name)

    @unittest.skipUnless(shutil.which("ffprobe") and shutil.which("ffmpeg"),
                         "needs ffmpeg/ffprobe")
    def test_container_without_video_is_reported(self):
        import subprocess as sp
        path = os.path.join(self.tmp, "audio.mp4")
        sp.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=1", path], check=True, capture_output=True)
        self.assertEqual(quality.check_video(path), ["has no video stream"])

    def test_video_without_ffprobe_makes_no_claim(self):
        # No ffprobe means nothing to inspect; guessing would be worse.
        saved = os.environ.get("PATH", "")
        os.environ["PATH"] = "/nonexistent"
        try:
            self.assertEqual(quality.check_video(os.path.join(self.tmp, "any.mp4")), [])
        finally:
            os.environ["PATH"] = saved


class TestQualityGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.png = os.path.join(self.tmp, "a.png")
        _tiny_png(self.png)

    def tearDown(self):
        os.environ.pop("FXLLA_MEDIA_SKIP_QUALITY", None)

    def test_skip_env_disables_the_gate(self):
        for value in ("1", "true", "yes"):
            os.environ["FXLLA_MEDIA_SKIP_QUALITY"] = value
            self.assertTrue(quality.skip_quality_checks(), value)
        for value in ("", "0", "false"):
            os.environ["FXLLA_MEDIA_SKIP_QUALITY"] = value
            self.assertFalse(quality.skip_quality_checks(), value)

    def test_gate_raises_with_the_escape_hatch_in_the_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            media._check_quality("image", self.png, lambda p: ["is blank"])
        self.assertIn("FXLLA_MEDIA_SKIP_QUALITY", str(ctx.exception))

    def test_gate_is_silent_when_there_are_no_problems(self):
        media._check_quality("image", self.png, lambda p: [])

    def test_a_broken_checker_never_fails_the_render(self):
        def boom(_path):
            raise ValueError("checker bug")
        with contextlib.redirect_stderr(io.StringIO()):
            media._check_quality("image", self.png, boom)  # must not raise

    def test_skipped_gate_does_not_call_the_checker(self):
        os.environ["FXLLA_MEDIA_SKIP_QUALITY"] = "1"
        def boom(_path):
            raise AssertionError("checker must not run when skipped")
        media._check_quality("image", self.png, boom)


class TestJobs(unittest.TestCase):
    # Exercises the job lifecycle against a stand-in generator, so no model is
    # loaded: jobs.run shells out to jobs.GENERATE, which the tests replace.
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (jobs.JOBS_DIR, jobs.GENERATE)
        jobs.JOBS_DIR = os.path.join(self.tmp, "jobs")

    def tearDown(self):
        jobs.JOBS_DIR, jobs.GENERATE = self._saved

    def _fake_generator(self, body):
        path = os.path.join(self.tmp, "fake_gen.py")
        with open(path, "w") as fh:
            fh.write(body)
        jobs.GENERATE = path
        return path

    def _record(self, **fields):
        rec = {"id": jobs.new_id(), "kind": "image", "status": "queued",
               "argv": [], "summary": "", "output": None, "error": None,
               "pid": None, "created": 1.0, "started": None, "finished": None}
        rec.update(fields)
        jobs._write(rec)
        return rec

    def test_valid_id_rejects_path_traversal(self):
        # Ids arrive from MCP arguments and are joined into a path.
        self.assertTrue(jobs.valid_id(jobs.new_id()))
        for bad in ("../../etc/passwd", "..", "", "abc", "1785376545-XYZ", None):
            self.assertFalse(jobs.valid_id(bad), bad)

    def test_get_rejects_bad_id(self):
        self.assertIsNone(jobs.get("../secret"))

    def test_run_writes_output_on_success(self):
        self._fake_generator("print('/tmp/out.png')\n")
        rec = self._record()
        jobs.run(rec["id"])
        done = jobs._read(rec["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["output"], "/tmp/out.png")
        self.assertIsNotNone(done["finished"])

    def test_run_records_failure(self):
        self._fake_generator("import sys; sys.exit('boom')\n")
        rec = self._record()
        jobs.run(rec["id"])
        failed = jobs._read(rec["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("boom", failed["error"])

    def test_run_fails_when_generator_prints_nothing(self):
        self._fake_generator("pass\n")
        rec = self._record()
        jobs.run(rec["id"])
        self.assertEqual(jobs._read(rec["id"])["status"], "failed")

    def test_run_skips_a_cancelled_job(self):
        self._fake_generator("print('/tmp/should-not-run.png')\n")
        rec = self._record(status="cancelled")
        jobs.run(rec["id"])
        self.assertEqual(jobs._read(rec["id"])["status"], "cancelled")

    def test_dead_worker_is_reaped(self):
        # A pid that cannot be running any more (reaped by the OS long ago).
        rec = self._record(status="running", pid=999999)
        self.assertEqual(jobs.get(rec["id"])["status"], "failed")
        self.assertIn("died", jobs._read(rec["id"])["error"])

    def test_cancel_marks_cancelled(self):
        rec = self._record(status="queued")
        self.assertEqual(jobs.cancel(rec["id"])["status"], "cancelled")

    def test_cancel_leaves_finished_jobs_alone(self):
        rec = self._record(status="done", output="/tmp/x.png")
        self.assertEqual(jobs.cancel(rec["id"])["status"], "done")

    def test_listing_is_newest_first(self):
        self._record(created=1.0, kind="image")
        self._record(created=5.0, kind="video")
        self.assertEqual([r["kind"] for r in jobs.listing()], ["video", "image"])

    def test_prune_keeps_active_jobs(self):
        self._record(status="done", created=1.0)
        self._record(status="failed", created=2.0)
        active = self._record(status="running", pid=os.getpid(), created=3.0)
        self.assertEqual(jobs.prune(), 2)
        self.assertEqual([r["id"] for r in jobs.listing()], [active["id"]])

    def test_describe_shows_last_error_line(self):
        rec = self._record(status="failed",
                           error="Traceback...\n  File x\nValueError: bad input")
        self.assertIn("ValueError: bad input", jobs.describe(rec))


class TestModelCatalog(unittest.TestCase):
    # The catalog replaced a hardcoded dict so per-model differences stop being
    # special cases. Capabilities came from probing each CLI's --help.
    def test_the_shipped_catalog_parses_and_has_the_default(self):
        models = media.load_models()
        self.assertIn(media.DEFAULT_MODEL, models)
        self.assertGreaterEqual(len(models), 8)
        for name, spec in models.items():
            self.assertTrue(spec["cli"].startswith("mflux-"), name)
            self.assertIsInstance(spec["caps"], set)

    def test_capabilities_are_not_uniform(self):
        # If every model had the same caps the mechanism would be pointless.
        # Verified against the installed CLIs: ideogram4 and boogu take no
        # init image, everything else does.
        models = media.load_models()
        with_init = {n for n, s in models.items() if "init-image" in s["caps"]}
        self.assertTrue(with_init)
        self.assertNotEqual(with_init, set(models))

    def test_comments_and_short_lines_are_skipped(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "m.conf")
        open(p, "w").write("# a comment\n\nbroken|line\n"
                           "ok | mflux-generate-x |  | 4 | lora,negative | note\n")
        models = media.load_models(p)
        self.assertEqual(list(models), ["ok"])
        self.assertEqual(models["ok"]["steps"], 4)
        self.assertEqual(models["ok"]["caps"], {"lora", "negative"})
        self.assertIsNone(models["ok"]["base_model"])

    def test_an_unsupported_flag_is_refused_by_name(self):
        # The alternative is mflux exiting with its own usage text after the
        # caller already waited, naming neither the model nor an alternative.
        spec = {"cli": "mflux-generate-x", "caps": {"negative"}, "steps": None}
        with self.assertRaises(ValueError) as ctx:
            media.build_command(spec, "cat", "/o.png", loras=["x.safetensors"],
                                model_name="mage-flow")
        msg = str(ctx.exception)
        self.assertIn("mage-flow", msg)
        self.assertIn("--lora", msg)
        self.assertIn("Models that do", msg)

    def test_a_default_adapts_but_an_explicit_request_is_validated(self):
        # Erroring on a flag the caller never named would make an unsupported
        # default look like their mistake.
        spec = {"cli": "mflux-generate-x", "caps": {"negative"}, "steps": 8}
        cmd = media.build_command(spec, "cat", "/o.png", model_name="m")
        self.assertNotIn("--quantize", cmd)
        self.assertNotIn("--steps", cmd)
        with self.assertRaises(ValueError):
            media.build_command(spec, "cat", "/o.png", quantize=4, model_name="m")
        with self.assertRaises(ValueError):
            media.build_command(spec, "cat", "/o.png", steps=3, model_name="m")

    def test_every_documented_capability_is_gated(self):
        # Five of the ten were ungated: a model whose CLI lacks --seed would
        # have had it forwarded anyway, which is the failure the gate exists
        # to prevent for the others.
        full = {"cli": "x", "steps": None,
                "caps": {"negative", "prompt-file", "init-image", "lora",
                         "lora-style", "quantize", "steps", "seed", "aspect",
                         "dimensions"}}
        for cap, kwargs in (
                ("quantize", {"quantize": 4}), ("steps", {"steps": 2}),
                ("seed", {"seed": 1}), ("aspect", {"aspect": "1:1"}),
                ("dimensions", {"width": 512})):
            lacking = dict(full, caps=full["caps"] - {cap})
            with self.assertRaises(ValueError, msg="%s ungated" % cap):
                media.build_command(lacking, "c", "/o.png", model_name="m", **kwargs)
            media.build_command(full, "c", "/o.png", model_name="m", **kwargs)

    def test_a_supported_flag_passes_through(self):
        spec = {"cli": "mflux-generate-x", "caps": {"negative"}, "steps": None}
        cmd = media.build_command(spec, "cat", "/o.png", negative="blurry")
        self.assertEqual(cmd[cmd.index("--negative-prompt") + 1], "blurry")


class TestIdeogramCaption(unittest.TestCase):
    # Ideogram 4 takes a JSON caption with placed elements. The rules are
    # mflux's own; validating here names a malformed caption before a render
    # starts instead of after it, as a schema warning.
    def _caption(self, elements):
        return json.dumps({"high_level_description": "poster",
                           "compositional_deconstruction": {"elements": elements}})

    def test_prose_is_left_alone(self):
        self.assertEqual(media.check_ideogram_caption("a cat on a beach"), [])
        self.assertEqual(media.check_ideogram_caption('"just a string"'), [])

    def test_a_well_formed_caption_passes(self):
        good = self._caption([
            {"type": "text", "bbox": [100, 200, 300, 800], "text": "HOLA",
             "color_palette": ["#FF0000"]},
            {"type": "obj", "bbox": [0, 0, 1000, 1000], "desc": "sky"}])
        self.assertEqual(media.check_ideogram_caption(good), [])

    def test_axis_order_is_caught(self):
        # [y_min, x_min, y_max, x_max] - Y first - is the trap nobody guesses.
        problems = media.check_ideogram_caption(
            self._caption([{"type": "text", "bbox": [900, 100, 200, 400], "text": "X"}]))
        self.assertTrue(any("Y first" in p for p in problems))

    def test_coordinate_space_is_caught(self):
        problems = media.check_ideogram_caption(
            self._caption([{"type": "obj", "bbox": [0, 0, 1200, 50]}]))
        self.assertTrue(any("0..1000" in p for p in problems))

    def test_floats_are_rejected(self):
        problems = media.check_ideogram_caption(
            self._caption([{"type": "obj", "bbox": [0.0, 0, 100, 50]}]))
        self.assertTrue(any("integers" in p for p in problems))

    def test_bad_length_is_caught(self):
        problems = media.check_ideogram_caption(
            self._caption([{"type": "obj", "bbox": [0, 0, 100]}]))
        self.assertTrue(any("y_min, x_min, y_max, x_max" in p for p in problems))

    def test_element_type_and_text_requirement(self):
        problems = media.check_ideogram_caption(
            self._caption([{"type": "thing"}, {"type": "text"}]))
        self.assertTrue(any("obj, text" in p for p in problems))
        self.assertTrue(any("needs a 'text' string" in p for p in problems))

    def test_style_palette_allows_sixteen_but_elements_only_five(self):
        # mflux's own limits differ by context; hardcoding 5 falsely rejected
        # a valid caption, and a false rejection blocks a working render.
        sixteen = ["#%06X" % i for i in range(16)]
        ok = json.dumps({"high_level_description": "x",
                         "style_description": {"color_palette": sixteen},
                         "compositional_deconstruction": {"elements": []}})
        self.assertEqual(media.check_ideogram_caption(ok), [])
        too_many = json.dumps({
            "high_level_description": "x",
            "compositional_deconstruction": {"elements": [
                {"type": "obj", "color_palette": sixteen[:6]}]}})
        self.assertTrue(any("at most 5" in p
                            for p in media.check_ideogram_caption(too_many)))

    def test_hex_colors_must_be_uppercase(self):
        # mflux's own rule: it accepts "#F2B134" and rejects "#f2b134". The
        # difference is invisible unless something names it, and a caption
        # that looked fine here would come back with warnings from mflux.
        lower = json.dumps({"high_level_description": "x",
                            "style_description": {"color_palette": ["#f2b134"]},
                            "compositional_deconstruction": {"elements": []}})
        problems = media.check_ideogram_caption(lower)
        self.assertTrue(any("UPPERCASE" in p for p in problems))
        upper = lower.replace("#f2b134", "#F2B134")
        self.assertEqual(media.check_ideogram_caption(upper), [])

    def test_a_realistic_caption_passes_end_to_end(self):
        # The example shipped in the docs, kept honest: if the schema tightens
        # and this stops validating, the documented prompt is wrong too.
        caption = json.dumps({
            "high_level_description": "A vintage poster of a chrome lighter.",
            "style_description": {
                "aesthetics": "mid-century poster, flat vector shapes",
                "lighting": "warm rim light, soft vignette",
                "medium": "screen print",
                "art_style": "retro Swiss design",
                "color_palette": ["#0D3B45", "#F2B134", "#E8E3D3"]},
            "compositional_deconstruction": {
                "background": "flat teal field with a faint radial glow",
                "elements": [
                    {"type": "text", "bbox": [80, 120, 240, 880], "text": "ZIPPO",
                     "desc": "condensed display type", "color_palette": ["#E8E3D3"]},
                    {"type": "obj", "bbox": [300, 330, 760, 670],
                     "desc": "chrome lighter, lid open, tall flame"}]}})
        self.assertEqual(media.check_ideogram_caption(caption), [])

    def test_the_documented_example_passes_its_own_validator(self):
        # The schema handed to a model through list_media_models must agree
        # with the one enforced here, or fxlla teaches a format it rejects.
        example = json.dumps(media.IDEOGRAM_PROMPT_FORMAT["example"])
        self.assertEqual(media.check_ideogram_caption(example), [])

    def test_the_documented_schema_states_the_traps(self):
        fmt = media.IDEOGRAM_PROMPT_FORMAT
        self.assertIn("Y FIRST", fmt["element"]["bbox"])
        self.assertIn("0..1000", fmt["element"]["bbox"])
        self.assertIn("UPPERCASE", fmt["colors"])

    def test_a_missing_composition_section_is_flagged(self):
        problems = media.check_ideogram_caption(
            json.dumps({"high_level_description": "just a description"}))
        self.assertTrue(any("compositional_deconstruction" in p for p in problems))

    def test_unknown_keys_and_palette_limits(self):
        problems = media.check_ideogram_caption(json.dumps({"nonsense": 1}))
        self.assertTrue(any("unknown top-level keys" in p for p in problems))
        problems = media.check_ideogram_caption(self._caption(
            [{"type": "obj", "color_palette": ["#FFF", "#000000", "#000000",
                                               "#000000", "#000000", "#000000"]}]))
        self.assertTrue(any("at most 5 colors" in p for p in problems))
        self.assertTrue(any("#RRGGBB" in p for p in problems))

    def test_generate_image_refuses_a_bad_caption(self):
        with self.assertRaises(ValueError) as ctx:
            media.generate_image(
                self._caption([{"type": "text", "bbox": [900, 0, 100, 10], "text": "x"}]),
                model="ideogram4")
        self.assertIn("JSON caption", str(ctx.exception))


class TestControlAndDepth(unittest.TestCase):
    # Two controlnet families that are NOT interchangeable: FLUX takes a
    # checkpoint plus a control image as separate repeatable flags, Z-Image
    # takes one combined spec. Both stack.
    def _img(self):
        p = os.path.join(tempfile.mkdtemp(), "ctl.png")
        open(p, "wb").write(media.PNG_MAGIC + b"0" * 64)
        return p

    def _flux(self):
        return {"cli": "x", "steps": None,
                "caps": {"controlnet", "controlnet-image", "controlnet-strength"}}

    def _zimage(self):
        return {"cli": "x", "steps": None,
                "caps": {"control-spec", "controlnet-strength"}}

    def test_flux_form_splits_image_and_checkpoint(self):
        p = self._img()
        cmd = media.build_command(self._flux(), "c", "/o.png",
                                  controls=["%s,InstantX/FLUX.1-dev-Controlnet-Canny" % p],
                                  model_name="controlnet")
        self.assertEqual(cmd[cmd.index("--controlnet-image-path") + 1], p)
        self.assertEqual(cmd[cmd.index("--controlnet-path") + 1],
                         "InstantX/FLUX.1-dev-Controlnet-Canny")

    def test_flux_form_stacks(self):
        a, b = self._img(), self._img()
        cmd = media.build_command(self._flux(), "c", "/o.png", controls=[a, b],
                                  model_name="controlnet")
        self.assertEqual(cmd.count("--controlnet-image-path"), 2)

    def test_zimage_form_passes_the_spec_through_and_stacks(self):
        cmd = media.build_command(self._zimage(), "c", "/o.png",
                                  controls=["pose:pose.png:0.8", "depth:d.png"],
                                  model_name="z-controlnet")
        self.assertEqual(cmd.count("--control"), 2)
        self.assertIn("pose:pose.png:0.8", cmd)
        # The combined spec must NOT be split into the FLUX flags.
        self.assertNotIn("--controlnet-image-path", cmd)

    def test_a_z_image_spec_survives_a_comma(self):
        # split_ref splits on commas and the z-image spec uses colons, so a
        # path containing a comma must NOT be torn apart: that branch never
        # runs for this family, and this pins it.
        cmd = media.build_command(self._zimage(), "c", "/o.png",
                                  controls=["pose:/a,b/p.png:0.8"],
                                  model_name="z-controlnet")
        self.assertEqual(cmd[cmd.index("--control") + 1], "pose:/a,b/p.png:0.8")

    def test_a_flux_path_containing_a_colon_still_works(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a:b.png")
        open(p, "wb").write(media.PNG_MAGIC)
        cmd = media.build_command(self._flux(), "c", "/o.png", controls=[p],
                                  model_name="controlnet")
        self.assertEqual(cmd[cmd.index("--controlnet-image-path") + 1], p)

    def test_the_wrong_form_says_which_one_this_model_takes(self):
        # Confusing the two families is the likely mistake; "file not found"
        # alone would send someone hunting for a path problem.
        with self.assertRaises(ValueError) as ctx:
            media.build_command(self._flux(), "c", "/o.png",
                                controls=["pose:p.png:0.8"], model_name="controlnet")
        self.assertIn("z-controlnet form", str(ctx.exception))
        self.assertIn("IMAGE[,CHECKPOINT]", str(ctx.exception))

    def test_a_missing_control_image_is_named(self):
        with self.assertRaises(ValueError) as ctx:
            media.build_command(self._flux(), "c", "/o.png",
                                controls=["/no/such.png"], model_name="controlnet")
        self.assertIn("/no/such.png", str(ctx.exception))

    def test_a_model_without_control_refuses(self):
        plain = {"cli": "x", "steps": None, "caps": {"negative"}}
        with self.assertRaises(ValueError) as ctx:
            media.build_command(plain, "c", "/o.png", controls=[self._img()],
                                model_name="z-image-turbo")
        self.assertIn("z-image-turbo", str(ctx.exception))

    def test_depth_map_can_be_supplied_and_saved(self):
        spec = {"cli": "x", "steps": None, "caps": {"depth-image", "save-depth"}}
        d = self._img()
        cmd = media.build_command(spec, "c", "/o.png", depth_image=d,
                                  save_depth=True, model_name="depth")
        self.assertEqual(cmd[cmd.index("--depth-image-path") + 1], d)
        self.assertIn("--save-depth-map", cmd)

    def test_prompt_file_replaces_the_prompt_flag(self):
        # mflux declares them mutually exclusive ("--prompt-file: not allowed
        # with argument --prompt"), verified against the real CLI, so sending
        # both made every --prompt-file render die in argparse.
        spec = {"cli": "x", "steps": None, "caps": {"prompt-file"}}
        f = os.path.join(tempfile.mkdtemp(), "p.txt")
        open(f, "w").write("a cat")
        cmd = media.build_command(spec, "ignored", "/o.png", prompt_file=f,
                                  model_name="m")
        self.assertIn("--prompt-file", cmd)
        self.assertNotIn("--prompt", cmd)
        self.assertEqual(cmd[cmd.index("--prompt-file") + 1], f)

    def test_the_depth_map_path_is_derivable(self):
        # mflux writes "<base>_depth_map<ext>"; the caller needs it to chain a
        # control step, so it cannot stay an internal naming convention.
        self.assertEqual(media.depth_map_path("/a/b/img.png"),
                         "/a/b/img_depth_map.png")
        self.assertEqual(media.depth_map_path("/a/b/img"),
                         "/a/b/img_depth_map.png")

    def test_guidance(self):
        spec = {"cli": "x", "steps": None, "caps": {"guidance"}}
        cmd = media.build_command(spec, "c", "/o.png", guidance=7.5, model_name="m")
        self.assertEqual(cmd[cmd.index("--guidance") + 1], "7.5")
        bare = {"cli": "x", "steps": None, "caps": set()}
        with self.assertRaises(ValueError):
            media.build_command(bare, "c", "/o.png", guidance=7.5, model_name="m")


class TestLoraDiscovery(unittest.TestCase):
    # Searching only the civitai download directory answered "none" to
    # someone holding ten: people train their own and keep them beside the
    # project that produced them.
    def test_configured_directories_are_searched(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "style.safetensors"), "w").close()
        saved = os.environ.get("FXLLA_LORA_DIRS")
        os.environ["FXLLA_LORA_DIRS"] = d
        try:
            paths = [p for p, _mb in media.find_loras()]
            self.assertIn(os.path.join(d, "style.safetensors"), paths)
        finally:
            if saved is None:
                del os.environ["FXLLA_LORA_DIRS"]
            else:
                os.environ["FXLLA_LORA_DIRS"] = saved

    def test_several_directories_and_nested_files(self):
        a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(a, "sub"))
        open(os.path.join(a, "sub", "deep.safetensors"), "w").close()
        open(os.path.join(b, "flat.ckpt"), "w").close()
        saved = os.environ.get("FXLLA_LORA_DIRS")
        os.environ["FXLLA_LORA_DIRS"] = "%s:%s" % (a, b)
        try:
            names = [os.path.basename(p) for p, _ in media.find_loras()]
            self.assertIn("deep.safetensors", names)
            self.assertIn("flat.ckpt", names)
        finally:
            if saved is None:
                del os.environ["FXLLA_LORA_DIRS"]
            else:
                os.environ["FXLLA_LORA_DIRS"] = saved

    def test_the_civitai_directory_is_always_searched(self):
        self.assertTrue(any(d.endswith("civitai") for d in media.lora_dirs()))

    def test_a_missing_directory_is_skipped_not_fatal(self):
        saved = os.environ.get("FXLLA_LORA_DIRS")
        os.environ["FXLLA_LORA_DIRS"] = "/no/such/dir"
        try:
            media.find_loras()  # must not raise
        finally:
            if saved is None:
                del os.environ["FXLLA_LORA_DIRS"]
            else:
                os.environ["FXLLA_LORA_DIRS"] = saved


class TestLoRA(unittest.TestCase):
    # `fxlla pull civitai:<id>` could download LoRAs from the day it shipped
    # and nothing could apply one.
    def _spec(self):
        return {"cli": "mflux-generate-x", "steps": None,
                "caps": {"lora", "lora-style", "negative", "init-image"}}

    def _lora(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "style.safetensors")
        open(p, "w").close()
        return p

    def test_a_path_with_scale_comma_form(self):
        # Comma, not spaces: a multi-value argparse flag swallowed the prompt,
        # and a JSON array of plain strings could not express the pair at all.
        p = self._lora()
        cmd = media.build_command(self._spec(), "c", "/o.png", loras=["%s,0.8" % p])
        i = cmd.index("--lora")
        self.assertEqual(cmd[i + 1:i + 3], [p, "0.8"])

    def test_a_tilde_path_reaches_the_backend_expanded(self):
        # Validating the expanded path while passing the literal "~" told the
        # caller the file was fine and handed mflux something it cannot
        # resolve: nothing here runs through a shell.
        import tempfile
        real = self._lora()
        home = os.path.expanduser("~")
        if not real.startswith(home):
            self.skipTest("temp dir is not under HOME")
        cmd = media.build_command(self._spec(), "c", "/o.png",
                                  loras=["~" + real[len(home):]])
        self.assertEqual(cmd[cmd.index("--lora") + 1], real)
        self.assertNotIn("~", cmd[cmd.index("--lora") + 1])

    def test_several_loras_repeat_the_flag(self):
        a, b = self._lora(), self._lora()
        cmd = media.build_command(self._spec(), "c", "/o.png", loras=[a, b])
        self.assertEqual(cmd.count("--lora"), 2)

    def test_a_huggingface_repo_id_is_accepted(self):
        # mflux takes org/name too, so only a local-looking path is checked.
        cmd = media.build_command(self._spec(), "c", "/o.png", loras=["org/name"])
        self.assertIn("org/name", cmd)

    def test_a_missing_local_lora_is_named(self):
        with self.assertRaises(ValueError) as ctx:
            media.build_command(self._spec(), "c", "/o.png",
                                loras=["/no/such.safetensors"])
        self.assertIn("/no/such.safetensors", str(ctx.exception))

    def test_built_in_styles(self):
        cmd = media.build_command(self._spec(), "c", "/o.png",
                                  lora_style="storyboard")
        self.assertEqual(cmd[cmd.index("--lora-style") + 1], "storyboard")
        self.assertIn("storyboard", media.LORA_STYLES)


class TestPromptControls(unittest.TestCase):
    def _spec(self):
        return {"cli": "mflux-generate-x", "steps": None,
                "caps": {"negative", "prompt-file", "init-image"}}

    def test_negative_prompt(self):
        cmd = media.build_command(self._spec(), "c", "/o.png", negative="text, watermark")
        self.assertEqual(cmd[cmd.index("--negative-prompt") + 1], "text, watermark")

    def test_prompt_file_must_exist(self):
        with self.assertRaises(ValueError) as ctx:
            media.build_command(self._spec(), "c", "/o.png", prompt_file="/no/p.txt")
        self.assertIn("/no/p.txt", str(ctx.exception))

    def test_init_image_maps_to_image_path(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "in.png")
        open(p, "wb").write(media.PNG_MAGIC)
        cmd = media.build_command(self._spec(), "c", "/o.png", init_image=p)
        self.assertEqual(cmd[cmd.index("--image-path") + 1], p)


class TestJobWait(unittest.TestCase):
    # An agent with no way to await a render polls instead: one issued 47
    # status calls waiting on a single video.
    def test_returns_as_soon_as_the_job_leaves_the_active_states(self):
        import time
        calls = {"n": 0}
        saved = jobs.get

        def _fake(_jid):
            calls["n"] += 1
            return {"status": "running"} if calls["n"] < 3 else {"status": "done"}
        jobs.get = _fake
        try:
            begin = time.monotonic()
            rec = jobs.wait("id", timeout_s=10, poll_s=0.01)
        finally:
            jobs.get = saved
        self.assertEqual(rec["status"], "done")
        self.assertLess(time.monotonic() - begin, 1.0)

    def test_a_timeout_returns_the_record_rather_than_raising(self):
        saved = jobs.get
        jobs.get = lambda _jid: {"status": "running"}
        try:
            rec = jobs.wait("id", timeout_s=0.05, poll_s=0.01)
        finally:
            jobs.get = saved
        self.assertEqual(rec["status"], "running")

    def test_an_unknown_job_returns_none_immediately(self):
        saved = jobs.get
        jobs.get = lambda _jid: None
        try:
            self.assertIsNone(jobs.wait("id", timeout_s=10, poll_s=0.01))
        finally:
            jobs.get = saved


class TestImageFacts(unittest.TestCase):
    def test_dimensions_and_size_from_the_header(self):
        import struct, tempfile
        p = os.path.join(tempfile.mkdtemp(), "i.png")
        with open(p, "wb") as fh:
            fh.write(media.PNG_MAGIC + b"\x00\x00\x00\x0dIHDR"
                     + struct.pack(">II", 512, 384) + b"0" * 40)
        f = quality.image_facts(p)
        self.assertEqual((f["width"], f["height"]), (512, 384))
        self.assertGreater(f["bytes"], 0)

    def test_a_missing_file_yields_nothing(self):
        self.assertEqual(quality.image_facts("/no/such.png"), {})


class TestVideoImageAnchors(unittest.TestCase):
    # Image-to-video. Asked for "a video transitioning between these two
    # images", a model instead described them in the prompt and produced an
    # unrelated clip: without anchors the stills are never used at all.
    def _img(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "ref.png")
        open(p, "wb").write(media.PNG_MAGIC + b"0" * 64)
        return p

    def test_a_bare_path_anchors_the_opening_frame(self):
        p = self._img()
        cmd = media.build_video_command("x", "/o.mp4", images=[p])
        self.assertEqual(cmd[cmd.index("--image") + 1], p)

    def test_two_anchors_repeat_the_flag(self):
        a, b = self._img(), self._img()
        cmd = media.build_video_command("x", "/o.mp4",
                                        images=["%s,0,1.0" % a, "%s,96,1.0" % b])
        self.assertEqual(cmd.count("--image"), 2)
        first = cmd.index("--image")
        self.assertEqual(cmd[first + 1:first + 4], [a, "0", "1.0"])

    def test_a_missing_reference_is_named(self):
        # Otherwise it surfaces as a generic backend failure minutes later.
        with self.assertRaises(ValueError) as ctx:
            media.build_video_command("x", "/o.mp4", images=["/no/such.png"])
        self.assertIn("/no/such.png", str(ctx.exception))

    def test_no_images_leaves_the_flag_out(self):
        self.assertNotIn("--image", media.build_video_command("x", "/o.mp4"))

    def test_the_flag_is_the_one_the_backend_accepts(self):
        # A model invented --images (plural); neither generate.py nor ltx has
        # it, so every call would have died in argparse.
        p = self._img()
        cmd = media.build_video_command("x", "/o.mp4", images=[p])
        self.assertNotIn("--images", cmd)


class TestResolveOutput(unittest.TestCase):
    # Passing a directory ("put it in ~/Downloads") used to reach the backend
    # as a filename and fail deep inside it. A real model hit exactly that and
    # abandoned the tool for ~30 shell commands.
    def test_none_lands_in_the_default_dir(self):
        p = media.resolve_output(None, "image", "png")
        self.assertTrue(p.startswith(media.OUT_DIR))
        self.assertTrue(p.endswith(".png"))

    def test_a_file_path_is_used_verbatim(self):
        self.assertEqual(media.resolve_output("/tmp/x.png", "image", "png"),
                         "/tmp/x.png")

    def test_an_existing_directory_gets_a_generated_name_inside(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = media.resolve_output(d, "image", "png")
        self.assertEqual(os.path.dirname(p), d)
        self.assertTrue(p.endswith(".png"))
        self.assertNotEqual(p, d)

    def test_a_trailing_separator_means_a_directory(self):
        import tempfile
        d = os.path.join(tempfile.mkdtemp(), "new-dir") + os.sep
        p = media.resolve_output(d, "video", "mp4")
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(os.path.dirname(p) + os.sep, d)

    def test_every_generator_kind_uses_it(self):
        for kind, ext in (("image", "png"), ("video", "mp4"), ("voice", "wav"),
                          ("edit", "png"), ("upscale", "png")):
            self.assertTrue(media.resolve_output(None, kind, ext).endswith("." + ext))


class TestAspectConflict(unittest.TestCase):
    # Measured: a request for 512x512 WITH aspect 1:1 produced 1024x1024,
    # because aspect won and the dimensions were dropped without a word.
    def test_aspect_with_dimensions_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            media.build_command(media.MODELS["z-image-turbo"], "a cat", "/o.png",
                                width=512, height=512, aspect="1:1")
        self.assertIn("512", str(ctx.exception))
        self.assertIn("1:1", str(ctx.exception))

    def test_either_alone_still_works(self):
        only_dims = media.build_command(media.MODELS["z-image-turbo"], "c", "/o.png",
                                        width=512, height=512)
        self.assertEqual(only_dims[only_dims.index("--width") + 1], "512")
        only_aspect = media.build_command(media.MODELS["z-image-turbo"], "c", "/o.png",
                                          aspect="16:9")
        self.assertEqual(only_aspect[only_aspect.index("--aspect") + 1], "16:9")
        self.assertNotIn("--width", only_aspect)


class TestMfluxBinDir(unittest.TestCase):
    # The image knob: one directory for the whole mflux family, since image
    # alone is eight per-model CLIs and a single-binary knob cannot cover it.
    def test_unset_resolves_from_path(self):
        saved = media.MFLUX_BIN_DIR
        media.MFLUX_BIN_DIR = ""
        try:
            self.assertEqual(media.mflux_cli("mflux-generate"), "mflux-generate")
        finally:
            media.MFLUX_BIN_DIR = saved

    def test_set_resolves_inside_the_directory(self):
        import tempfile
        d = tempfile.mkdtemp()
        open(os.path.join(d, "mflux-generate"), "w").close()
        saved = media.MFLUX_BIN_DIR
        media.MFLUX_BIN_DIR = d
        try:
            self.assertEqual(media.mflux_cli("mflux-generate"),
                             os.path.join(d, "mflux-generate"))
        finally:
            media.MFLUX_BIN_DIR = saved

    def test_set_with_missing_binary_errors_instead_of_falling_back(self):
        # Silently falling back to PATH would run a different install than
        # the one the user pointed at - the quiet failure the knob exists to
        # prevent.
        import tempfile
        d = tempfile.mkdtemp()
        saved = media.MFLUX_BIN_DIR
        media.MFLUX_BIN_DIR = d
        try:
            with self.assertRaises(ValueError) as ctx:
                media.mflux_cli("mflux-generate-boogu")
            self.assertIn(d, str(ctx.exception))
            self.assertIn("mflux-generate-boogu", str(ctx.exception))
        finally:
            media.MFLUX_BIN_DIR = saved

    def test_image_edit_and_upscale_all_honor_the_directory(self):
        import tempfile
        d = tempfile.mkdtemp()
        for name in ("mflux-generate-z-image-turbo", "mflux-generate-qwen-edit",
                     "mflux-upscale-seedvr2"):
            open(os.path.join(d, name), "w").close()
        saved = media.MFLUX_BIN_DIR
        media.MFLUX_BIN_DIR = d
        try:
            img = media.build_command(media.MODELS["z-image-turbo"], "a cat", "/o.png")
            self.assertEqual(img[0], os.path.join(d, "mflux-generate-z-image-turbo"))
            edit = media.build_edit_command("p", "/i.png", "/o.png")
            self.assertEqual(edit[0], os.path.join(d, "mflux-generate-qwen-edit"))
            up = media.build_upscale_command("/i.png", "/o.png")
            self.assertEqual(up[0], os.path.join(d, "mflux-upscale-seedvr2"))
        finally:
            media.MFLUX_BIN_DIR = saved

    def test_specific_edit_knob_still_wins(self):
        saved_dir, saved_edit = media.MFLUX_BIN_DIR, media.EDIT_BIN
        media.MFLUX_BIN_DIR = "/nonexistent"
        media.EDIT_BIN = "/my/qwen-edit"
        try:
            cmd = media.build_edit_command("p", "/i.png", "/o.png")
            self.assertEqual(cmd[0], "/my/qwen-edit")
        finally:
            media.MFLUX_BIN_DIR, media.EDIT_BIN = saved_dir, saved_edit


class TestVoicePythonResolution(unittest.TestCase):
    # One resolution, three layers: the env override always wins, the uv tool
    # venv (installed by `fxlla setup --media`) is the default when present,
    # bare python3 keeps hand-rolled PATH venvs working.
    def test_env_override_wins(self):
        os.environ["FXLLA_VOICE_PYTHON"] = "/my/venv/bin/python"
        try:
            self.assertEqual(media.resolved_voice_python(), "/my/venv/bin/python")
        finally:
            del os.environ["FXLLA_VOICE_PYTHON"]

    def test_uv_tool_interpreter_when_present(self):
        saved_run, saved_isfile = media.subprocess.run, media.os.path.isfile

        class _Out:
            stdout = "/tools\n"
        media.subprocess.run = lambda *a, **k: _Out()
        media.os.path.isfile = lambda p: p == "/tools/mlx-audio/bin/python"
        try:
            self.assertEqual(media.resolved_voice_python(),
                             "/tools/mlx-audio/bin/python")
        finally:
            media.subprocess.run, media.os.path.isfile = saved_run, saved_isfile

    def test_falls_back_to_python3(self):
        saved = media.subprocess.run

        def _boom(*_a, **_k):
            raise OSError("no uv")
        media.subprocess.run = _boom
        try:
            self.assertEqual(media.resolved_voice_python(), "python3")
        finally:
            media.subprocess.run = saved


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
        import array, math, wave
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ok.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                # A real tone. The previous fixture was a constant sample value,
                # which is a DC level carrying no signal, and the content checks
                # correctly call that silence.
                tone = array.array("h", (int(9000 * math.sin(2 * math.pi * 180 * t / 24000))
                                         for t in range(4096)))
                w.writeframes(tone.tobytes())
            media.validate_wav_output(path)  # must not raise


if __name__ == "__main__":
    unittest.main()
