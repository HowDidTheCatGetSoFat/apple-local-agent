import contextlib
import importlib
import io
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
                          "edit_image", "upscale_image", "media_job_status",
                          "list_media_jobs", "cancel_media_job"})

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
