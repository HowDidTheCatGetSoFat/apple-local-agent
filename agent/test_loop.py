"""Tests for `fxlla do`.

Every model call is stubbed. The loop's job is deciding what to do with what
the planner, the generator and the eyes come back with, and that decision is
what these pin - none of it needs a GPU, a gateway or a render to be wrong.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media"))

import generate  # noqa: E402
import loop  # noqa: E402


class TestPlanExtraction(unittest.TestCase):
    def test_json_is_found_inside_prose(self):
        # Local models wrap their answer more often than not, and losing a
        # correct plan to "Here you go:" would be a self-inflicted retry.
        found = loop._extract_json('Sure! Here is the plan:\n```json\n'
                                   '{"model": "a", "prompt": "b"}\n```\nHope that helps.')
        self.assertEqual(found["model"], "a")

    def test_nested_braces_do_not_end_the_object_early(self):
        found = loop._extract_json('{"a": {"b": 1}, "c": 2}')
        self.assertEqual(found["c"], 2)

    def test_an_unbalanced_brace_inside_a_string_does_not_close_the_object(self):
        # ideogram4 takes a JSON caption, so a brace inside "prompt" is the
        # normal case here. It must be UNBALANCED to be a real test: a
        # matched pair inside a string leaves naive counting at the same
        # answer by luck, and a version of this with "{...}" in the prompt
        # passed against an implementation that ignored strings entirely.
        found = loop._extract_json(
            '{"model": "ideogram4", "prompt": "a cat } wearing a hat", '
            '"expect": ["cat"]}')
        self.assertEqual(found["model"], "ideogram4")
        self.assertEqual(found["expect"], ["cat"])

    def test_an_escaped_quote_does_not_end_the_string(self):
        found = loop._extract_json('{"prompt": "she said \\"} \\" and left", "model": "m"}')
        self.assertEqual(found["model"], "m")

    def test_an_unbalanced_brace_in_prose_before_the_object_is_skipped(self):
        found = loop._extract_json('use } carefully. {"model": "m", "prompt": "p"}')
        self.assertEqual(found["model"], "m")

    def test_a_reply_with_no_object_is_a_plan_error(self):
        with self.assertRaises(loop.PlanError):
            loop._extract_json("I cannot do that.")

    def test_a_broken_object_does_not_swallow_a_later_good_one(self):
        # A first candidate that fails to parse must not abort the search:
        # a model that emits a malformed sketch then the real plan is common.
        found = loop._extract_json('{not json} then {"model": "z", "prompt": "p"}')
        self.assertEqual(found["model"], "z")


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.real = generate.MODELS
        generate.MODELS = {
            "fast": {"caps": {"seed", "steps"}, "steps": 8, "note": ""},
            "fancy": {"caps": {"seed", "negative", "guidance"}, "steps": 20, "note": ""},
        }
        self.addCleanup(setattr, generate, "MODELS", self.real)

    def _plan(self, **over):
        base = {"model": "fast", "prompt": "a cat", "expect": ["cat"]}
        base.update(over)
        return base

    def test_an_unknown_model_is_refused_by_name(self):
        with self.assertRaises(loop.PlanError) as ctx:
            loop._validate(self._plan(model="imaginary"))
        self.assertIn("imaginary", str(ctx.exception))
        self.assertIn("fast", str(ctx.exception))

    def test_a_plan_with_no_prompt_is_refused(self):
        with self.assertRaises(loop.PlanError):
            loop._validate(self._plan(prompt="   "))

    def test_a_plan_committing_to_nothing_is_refused(self):
        # Without expectations there is nothing for the look to be checked
        # against, so the loop would report success on any file at all.
        with self.assertRaises(loop.PlanError):
            loop._validate(self._plan(expect=[]))

    def test_capabilities_are_not_re_checked_here(self):
        # The generator refuses unsupported flags by name, before rendering.
        # Duplicating that check would create a second authority that drifts
        # from the catalog the moment one of them is corrected.
        out = loop._validate(self._plan(guidance=3.5))
        self.assertEqual(out["guidance"], 3.5)

    def test_an_invented_key_never_reaches_the_generator(self):
        # generate_image is called with **plan; an unknown key would surface
        # as a TypeError rather than a message anyone can act on.
        out = loop._validate(self._plan(nonsense="x", temperature=2))
        self.assertNotIn("nonsense", out)
        self.assertNotIn("temperature", out)

    def test_a_withheld_model_cannot_be_chosen_anyway(self):
        # Removing a failed model from the menu is only a constraint if naming
        # it regardless is refused. Filtering the prompt alone left the planner
        # free to pick it - which the real one did - and it sailed into
        # another doomed render.
        with self.assertRaises(loop.PlanError) as ctx:
            loop._validate(self._plan(model="fast"), exclude={"fast"})
        self.assertIn("already failed", str(ctx.exception))

    def test_excluding_one_model_does_not_refuse_the_others(self):
        out = loop._validate(self._plan(model="fancy"), exclude={"fast"})
        self.assertEqual(out["model"], "fancy")

    def test_expectations_are_capped(self):
        out = loop._validate(self._plan(expect=["a", "b", "c", "d", "e", "f", "g"]))
        self.assertEqual(len(out["expect"]), 5)


class TestImagesInTheIntent(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.png = os.path.join(self.dir, "shot.png")
        open(self.png, "wb").close()

    def test_a_named_file_that_exists_is_found(self):
        self.assertEqual(loop.images_in("make the wall green in %s" % self.png),
                         [self.png])

    def test_a_file_that_does_not_exist_is_not_offered(self):
        self.assertEqual(loop.images_in("edit %s/absent.png" % self.dir), [])

    def test_a_path_with_spaces_survives_when_quoted(self):
        spaced = os.path.join(self.dir, "two words.png")
        open(spaced, "wb").close()
        self.assertEqual(loop.images_in('edit "%s" please' % spaced), [spaced])

    def test_trailing_punctuation_does_not_break_the_path(self):
        self.assertEqual(loop.images_in("look at %s, then fix it" % self.png),
                         [self.png])

    def test_the_same_file_twice_is_listed_once(self):
        self.assertEqual(loop.images_in("%s and %s" % (self.png, self.png)),
                         [self.png])

    def test_punctuation_wrapped_around_a_path_does_not_hide_it(self):
        # People write paths inside parentheses and markdown brackets at least
        # as often as they write them bare. Missing those is silent: the
        # planner is shown an empty list and generates a fresh image instead
        # of editing the file the request plainly named.
        for phrasing in ("the photo (%s) needs a hat",
                         "edit [%s] please",
                         "make %s! brighter",
                         "edit ![alt](%s) to add a hat",
                         "look at <%s>",
                         "about %s: make it green"):
            with self.subTest(phrasing=phrasing):
                self.assertEqual(loop.images_in(phrasing % self.png), [self.png])

    def test_the_order_is_the_order_they_appear(self):
        # Quoted runs were collected first, so a quoted path jumped ahead of
        # an earlier unquoted one and the docstring's promise was false.
        second = os.path.join(self.dir, "two words.png")
        open(second, "wb").close()
        self.assertEqual(loop.images_in('%s then "%s"' % (self.png, second)),
                         [self.png, second])

    def test_a_prompt_with_no_paths_offers_nothing(self):
        self.assertEqual(loop.images_in("a red bicycle against a blue wall"), [])

    def test_a_non_image_file_is_not_offered(self):
        other = os.path.join(self.dir, "notes.txt")
        open(other, "wb").close()
        self.assertEqual(loop.images_in("read %s" % other), [])


class TestEditPlans(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.real = generate.MODELS
        generate.MODELS = {"fast": {"caps": {"seed"}, "steps": 8, "note": ""}}
        self.addCleanup(setattr, generate, "MODELS", self.real)
        self.dir = tempfile.mkdtemp()
        self.png = os.path.join(self.dir, "shot.png")
        open(self.png, "wb").close()

    def _edit(self, **over):
        base = {"action": "edit", "image": self.png, "prompt": "make it green",
                "expect": ["green"]}
        base.update(over)
        return base

    def test_an_edit_naming_a_found_image_is_accepted(self):
        out = loop._validate(self._edit(), editable=[self.png])
        self.assertEqual(out["action"], "edit")
        self.assertEqual(out["image"], self.png)
        self.assertNotIn("model", out)

    def test_an_invented_path_is_refused(self):
        # The planner is never trusted to transcribe a path. A hallucinated or
        # mistyped one must be impossible, not merely unlikely.
        with self.assertRaises(loop.PlanError) as ctx:
            loop._validate(self._edit(image="/tmp/not-in-the-request.png"),
                           editable=[self.png])
        self.assertIn("not one of the images", str(ctx.exception))

    def test_an_edit_with_no_editable_images_is_refused(self):
        with self.assertRaises(loop.PlanError):
            loop._validate(self._edit(), editable=[])

    def test_the_same_file_written_differently_still_matches(self):
        # A planner echoing the path with a redundant ./ or a ~ must not be
        # rejected for a difference that resolves to the same file.
        odd = os.path.join(self.dir, ".", "shot.png")
        out = loop._validate(self._edit(image=odd), editable=[self.png])
        self.assertEqual(out["image"], self.png)

    def test_a_different_case_still_names_the_same_file(self):
        # This machine's volume is case-insensitive, so a planner echoing
        # FOO.PNG for the foo.png it was shown named the same file and was
        # refused for it. The comparison asks the filesystem, not the strings.
        shouted = os.path.join(self.dir, "SHOT.PNG")
        if not os.path.isfile(shouted):
            self.skipTest("case-sensitive filesystem")
        out = loop._validate(self._edit(image=shouted), editable=[self.png])
        self.assertEqual(out["image"], self.png)

    def test_a_symlink_to_an_offered_image_resolves_to_it(self):
        link = os.path.join(self.dir, "link.png")
        os.symlink(self.png, link)
        out = loop._validate(self._edit(image=link), editable=[self.png])
        self.assertEqual(out["image"], self.png)

    def test_an_edit_carries_no_generation_only_fields(self):
        # steps/guidance/aspect mean nothing to qwen-edit and would reach it
        # as a TypeError rather than a message.
        out = loop._validate(self._edit(steps=40, guidance=3.0, aspect="16:9"),
                             editable=[self.png])
        for absent in ("steps", "guidance", "aspect"):
            self.assertNotIn(absent, out)

    def test_an_edit_may_carry_a_seed(self):
        out = loop._validate(self._edit(seed=7), editable=[self.png])
        self.assertEqual(out["seed"], 7)

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(loop.PlanError) as ctx:
            loop._validate(self._edit(action="upscale"), editable=[self.png])
        self.assertIn("unknown action", str(ctx.exception))

    def test_a_plan_with_no_action_still_means_generate(self):
        out = loop._validate({"model": "fast", "prompt": "a cat",
                              "expect": ["cat"]})
        self.assertEqual(out["action"], "generate")

    def test_an_edit_is_dispatched_to_the_edit_generator(self):
        seen = {}
        saved = (generate.generate_edit, generate.generate_image)
        generate.generate_edit = lambda **k: seen.update(k) or "/tmp/edited.png"
        generate.generate_image = lambda **k: self.fail("edit went to generate")
        self.addCleanup(lambda: (setattr(generate, "generate_edit", saved[0]),
                                 setattr(generate, "generate_image", saved[1])))
        plan = loop._validate(self._edit(), editable=[self.png])
        self.assertEqual(loop._act(plan), "/tmp/edited.png")
        self.assertEqual(seen["image"], self.png)
        self.assertEqual(seen["prompt"], "make it green")
        self.assertNotIn("action", seen)

    def test_a_generate_is_dispatched_to_the_image_generator(self):
        seen = {}
        saved = (generate.generate_edit, generate.generate_image)
        generate.generate_image = lambda **k: seen.update(k) or "/tmp/made.png"
        generate.generate_edit = lambda **k: self.fail("generate went to edit")
        self.addCleanup(lambda: (setattr(generate, "generate_edit", saved[0]),
                                 setattr(generate, "generate_image", saved[1])))
        plan = loop._validate({"model": "fast", "prompt": "a cat",
                               "expect": ["cat"]})
        self.assertEqual(loop._act(plan), "/tmp/made.png")
        self.assertEqual(seen["model"], "fast")
        self.assertNotIn("action", seen)


class TestDrift(unittest.TestCase):
    """What an edit changed besides what it was asked to change.

    The check only ever asks whether what was PROMISED turned up, so an edit
    that also altered something nobody mentioned passed clean.
    """

    def test_the_real_case_that_motivated_this(self):
        # Measured on the first live edit: the wall went yellow as asked, and
        # the ground went from concrete to wooden, which nothing flagged.
        before = ("The image shows a red bicycle placed against a blue wall. "
                  "The ground is a light grey concrete surface.")
        after = ("The image shows a red bicycle placed against a bright yellow "
                 "wall. The bicycle is positioned on a light gray wooden surface.")
        appeared, vanished = loop.drifted(before, after)
        self.assertIn("wooden", appeared)
        self.assertIn("concrete", vanished)

    def test_the_requested_change_shows_up_too(self):
        # Not filtered out: the asked-for change is a change, and separating
        # it would mean deciding what the user meant.
        appeared, vanished = loop.drifted("a blue wall", "a yellow wall")
        self.assertEqual((appeared, vanished), (["yellow"], ["blue"]))

    def test_identical_descriptions_drift_in_neither_direction(self):
        text = "A red bicycle against a blue wall on grey concrete."
        self.assertEqual(loop.drifted(text, text), ([], []))

    def test_filler_and_short_words_are_ignored(self):
        # "the image shows" moving around says nothing about the image.
        appeared, vanished = loop.drifted(
            "The image shows a bicycle.", "This photo appears to show a bicycle.")
        self.assertEqual((appeared, vanished), ([], []))

    def test_case_and_punctuation_do_not_count_as_drift(self):
        self.assertEqual(loop.drifted("A red Bicycle.", "a RED bicycle"), ([], []))

    def test_no_before_means_no_drift_reported(self):
        # A generate has nothing to compare against, and inventing a baseline
        # would be worse than saying nothing.
        self.assertEqual(loop.drifted(None, "a red bicycle"), ([], []))

    def test_a_generate_never_reads_an_input(self):
        looked = []
        self._stub_for_drift(looked, action="generate")
        loop.run("a red bicycle", max_steps=1, out=self.quiet)
        self.assertEqual(len(looked), 1, "only the result should be read")

    def test_an_edit_reads_the_input_as_well(self):
        looked = []
        self._stub_for_drift(looked, action="edit")
        result = loop.run("make it yellow", max_steps=1, out=self.quiet)
        self.assertEqual(len(looked), 2, "input and result")
        self.assertIn("concrete", result["steps"][0]["vanished"])
        self.assertIn("wooden", result["steps"][0]["appeared"])

    def test_a_failed_read_of_the_input_does_not_lose_the_render(self):
        # The extra look is commentary. It must never cost a render that
        # already succeeded.
        looked = []
        self._stub_for_drift(looked, action="edit", raise_on_input=True)
        result = loop.run("make it yellow", max_steps=1, out=self.quiet)
        self.assertTrue(result["settled"])
        self.assertEqual(result["output"], "/tmp/edited.png")

    def test_the_render_survives_a_failure_nobody_thought_of(self):
        # The test above raises RuntimeError, which an `except (RuntimeError,
        # ValueError)` catches - so it would pass against a handler that only
        # covers the two shapes that came to mind, and prove nothing about the
        # promise. A truncated HTTP reply is neither: IncompleteRead escaped
        # run() AND main() and killed the process with the image already on
        # disk. The promise is about every failure or it is not a promise.
        import http.client
        looked = []
        self._stub_for_drift(looked, action="edit",
                             raise_on_input=http.client.IncompleteRead(b"partial"))
        result = loop.run("make it yellow", max_steps=1, out=self.quiet)
        self.assertTrue(result["settled"])
        self.assertEqual(result["output"], "/tmp/edited.png")
        self.assertEqual(result["steps"][0]["before"], None)

    def test_no_time_left_means_the_input_is_not_read(self):
        # Every look floors its timeout at 30 s, so reading the input with
        # seconds left does not spend seconds - it spends thirty past the
        # budget, and the verdict look that follows floors at 30 as well.
        # Commentary does not get to be what overruns the caller's limit.
        looked = []
        self._stub_for_drift(looked, action="edit")
        result = loop.run("make it yellow", max_steps=1, max_seconds=20,
                          out=self.quiet)
        self.assertEqual(looked, ["/tmp/edited.png"], "only the result")
        self.assertEqual(result["steps"][0]["before"], None)

    def _stub_for_drift(self, looked, action, raise_on_input=False):
        import tempfile
        directory = tempfile.mkdtemp()
        png = os.path.join(directory, "shot.png")
        open(png, "wb").close()
        self.quiet = open(os.devnull, "w")
        self.addCleanup(self.quiet.close)
        real = generate.MODELS
        generate.MODELS = {"fast": {"caps": {"seed"}, "steps": 8, "note": ""}}
        self.addCleanup(setattr, generate, "MODELS", real)
        plan = ({"action": "edit", "image": png, "prompt": "yellow",
                 "expect": ["yellow"]} if action == "edit" else
                {"model": "fast", "prompt": "a bike", "expect": ["yellow"]})

        def fake_look(path, timeout_s):
            looked.append(path)
            if path == png:
                if isinstance(raise_on_input, BaseException):
                    raise raise_on_input
                if raise_on_input:
                    raise RuntimeError("the vision model returned nothing")
                return "a bicycle against a blue wall on grey concrete"
            return "a bicycle against a yellow wall on a wooden surface"

        saved = (loop.plan, generate.generate_image, generate.generate_edit, loop.look)
        loop.plan = lambda *a, **k: loop._validate(plan, editable=[png])
        generate.generate_image = lambda **kw: "/tmp/made.png"
        generate.generate_edit = lambda **kw: "/tmp/edited.png"
        loop.look = fake_look
        self.addCleanup(lambda: (setattr(loop, "plan", saved[0]),
                                 setattr(generate, "generate_image", saved[1]),
                                 setattr(generate, "generate_edit", saved[2]),
                                 setattr(loop, "look", saved[3])))


class TestCheck(unittest.TestCase):
    def test_a_mentioned_word_is_not_missing(self):
        self.assertEqual(loop.check(["cat"], "A CAT sitting on a mat."), [])

    def test_case_does_not_decide_it(self):
        self.assertEqual(loop.check(["Cat", "MAT"], "a cat on a mat"), [])

    def test_what_was_never_mentioned_comes_back(self):
        self.assertEqual(loop.check(["cat", "hat"], "a cat on a mat"), ["hat"])

    def test_the_look_question_never_names_what_is_sought(self):
        # A vision model asked "is this a red car?" says yes. The whole value
        # of this step is that it was not told the answer.
        self.assertNotIn("expect", loop.LOOK.lower())
        for leading in ("is this", "does this", "correct", "verify", "confirm"):
            self.assertNotIn(leading, loop.LOOK.lower())


class TestLoop(unittest.TestCase):
    def setUp(self):
        self.real = generate.MODELS
        generate.MODELS = {"fast": {"caps": {"seed"}, "steps": 8, "note": ""}}
        self.addCleanup(setattr, generate, "MODELS", self.real)
        self.planned, self.rendered, self.excluded = [], [], []
        self.quiet = open(os.devnull, "w")
        self.addCleanup(self.quiet.close)

    def _stub(self, plans, describe, render=None):
        """Drive the loop with canned plans, renders and descriptions."""
        queue = list(plans)

        def fake_plan(intent, feedback=None, exclude=(), timeout_s=180, editable=()):
            self.planned.append(feedback)
            self.excluded.append(set(exclude))
            return loop._validate(queue.pop(0))

        def fake_render(**kwargs):
            # The call number is passed in rather than left for each stub to
            # count: a stub that appended as well saw every render twice and
            # its "fail the first one" never fired.
            self.rendered.append(kwargs)
            if render:
                return render(len(self.rendered), **kwargs)
            return "/tmp/out-%d.png" % len(self.rendered)

        saved = (loop.plan, generate.generate_image, loop.look)
        loop.plan = fake_plan
        generate.generate_image = fake_render
        loop.look = lambda path, timeout_s: describe(path)
        self.addCleanup(lambda: (setattr(loop, "plan", saved[0]),
                                 setattr(generate, "generate_image", saved[1]),
                                 setattr(loop, "look", saved[2])))

    def test_a_render_whose_description_mentions_everything_settles_at_once(self):
        self._stub([{"model": "fast", "prompt": "a red cat", "expect": ["cat", "red"]}],
                   lambda p: "a red cat on a mat")
        result = loop.run("a red cat", out=self.quiet)
        self.assertTrue(result["settled"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(self.rendered), 1)

    def test_a_short_description_buys_exactly_one_more_attempt(self):
        self._stub([{"model": "fast", "prompt": "a red cat", "expect": ["cat", "hat"]},
                    {"model": "fast", "prompt": "a cat in a hat", "expect": ["cat", "hat"]}],
                   lambda p: ("a cat on a mat" if p.endswith("1.png")
                              else "a cat wearing a hat"))
        result = loop.run("a cat in a hat", out=self.quiet)
        self.assertTrue(result["settled"])
        self.assertEqual(result["attempts"], 2)

    def test_the_retry_is_told_what_was_missing(self):
        self._stub([{"model": "fast", "prompt": "a", "expect": ["cat", "hat"]},
                    {"model": "fast", "prompt": "b", "expect": ["cat"]}],
                   lambda p: "a cat")
        loop.run("x", out=self.quiet)
        self.assertIsNone(self.planned[0])
        self.assertEqual(self.planned[1]["missing"], ["hat"])

    def test_it_stops_at_the_step_budget_rather_than_forever(self):
        self._stub([{"model": "fast", "prompt": "a", "expect": ["unicorn"]}] * 5,
                   lambda p: "an empty room")
        result = loop.run("x", max_steps=2, out=self.quiet)
        self.assertFalse(result["settled"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(self.rendered), 2)

    def test_a_refused_plan_costs_an_attempt_and_feeds_the_reason_back(self):
        def refuse(call, **kwargs):
            if call == 1:
                raise ValueError("model 'fast' does not support --guidance. "
                                 "Models that do: fancy")
            return "/tmp/ok.png"

        self._stub([{"model": "fast", "prompt": "a", "expect": ["cat"]},
                    {"model": "fast", "prompt": "b", "expect": ["cat"]}],
                   lambda p: "a cat", render=refuse)
        result = loop.run("x", out=self.quiet)
        self.assertTrue(result["settled"])
        self.assertIn("does not support", self.planned[1]["refused"])
        self.assertIsNone(result["steps"][0]["output"])

    def test_a_model_that_failed_is_removed_from_the_next_menu(self):
        # Observed on the first live run: told in prose that schnell had just
        # died, the planner chose schnell again with the same reasoning. The
        # constraint has to be applied where obeying it is not optional.
        def refuse(call, **kwargs):
            if call == 1:
                raise ValueError("model 'fast' exploded")
            return "/tmp/ok.png"

        self._stub([{"model": "fast", "prompt": "a", "expect": ["cat"]},
                    {"model": "fast", "prompt": "b", "expect": ["cat"]}],
                   lambda p: "a cat", render=refuse)
        loop.run("x", out=self.quiet)
        self.assertEqual(self.excluded[0], set())
        self.assertEqual(self.excluded[1], {"fast"})

    def test_the_failure_reported_is_the_exception_not_traceback_noise(self):
        def refuse(call, **kwargs):
            raise RuntimeError(
                '  File "/x/y.py", line 9, in load\n'
                "    weights = load(component)\n"
                "              ^^^^^^^^^^^^^^^\n"
                "ValueError: no download_url for component: vae")

        self._stub([{"model": "fast", "prompt": "a", "expect": ["cat"]}],
                   lambda p: "a cat", render=refuse)
        result = loop.run("x", max_steps=1, out=self.quiet)
        self.assertEqual(result["steps"][0]["refused"],
                         "ValueError: no download_url for component: vae")

    def test_running_out_of_usable_models_says_so(self):
        # Asserted non-empty first: an empty menu raises its own PlanError, and
        # without this the test would pass whether or not exclusion did anything.
        self.assertTrue(loop.menu(), "the menu must offer something to exclude")
        with self.assertRaises(loop.PlanError) as ctx:
            loop.plan("x", exclude={"fast"})
        self.assertIn("every usable model failed", str(ctx.exception))

    def test_an_earlier_render_is_still_returned_when_a_later_plan_is_refused(self):
        # Throwing away a finished image because the NEXT plan was rejected
        # would discard work the user can still use.
        def refuse_second(call, **kwargs):
            if call == 2:
                raise ValueError("model 'fast' does not support --preset. "
                                 "Models that do: (none)")
            return "/tmp/first.png"

        self._stub([{"model": "fast", "prompt": "a", "expect": ["hat"]},
                    {"model": "fast", "prompt": "b", "expect": ["hat"]}],
                   lambda p: "a cat", render=refuse_second)
        result = loop.run("x", max_steps=2, out=self.quiet)
        self.assertFalse(result["settled"])
        self.assertEqual(result["output"], "/tmp/first.png")

    def test_the_wall_clock_budget_stops_it_before_a_second_render(self):
        # Steps alone do not bound this: one slow render can outlast any
        # reasonable step count on its own.
        self._stub([{"model": "fast", "prompt": "a", "expect": ["unicorn"]}] * 3,
                   lambda p: "an empty room")
        result = loop.run("x", max_steps=5, max_seconds=0, out=self.quiet)
        self.assertFalse(result["settled"])
        self.assertEqual(len(self.rendered), 0)

    def test_a_later_planning_failure_does_not_discard_the_render(self):
        # The worst shape this had: attempt 1 renders, attempt 2's planner
        # names something unusable, the PlanError escapes run(), and main()
        # prints the planner's error and exits 1 - identical to a run that
        # made nothing, with the finished PNG never mentioned.
        queue = [{"model": "fast", "prompt": "a", "expect": ["hat"]}]

        def fake_plan(intent, feedback=None, exclude=(), timeout_s=180, editable=()):
            if not queue:
                raise loop.PlanError("unknown model 'imaginary'")
            return loop._validate(queue.pop(0))

        saved = (loop.plan, generate.generate_image, loop.look)
        loop.plan = fake_plan
        generate.generate_image = lambda **k: "/tmp/kept.png"
        loop.look = lambda path, timeout_s: "a cat"
        self.addCleanup(lambda: (setattr(loop, "plan", saved[0]),
                                 setattr(generate, "generate_image", saved[1]),
                                 setattr(loop, "look", saved[2])))
        result = loop.run("x", max_steps=2, out=self.quiet)
        self.assertFalse(result["settled"])
        self.assertEqual(result["output"], "/tmp/kept.png")

    def test_the_very_first_planning_failure_still_ends_the_run(self):
        # Nothing was made, so there is nothing to preserve and the planner's
        # error is the whole story. Swallowing it would report an empty
        # success instead.
        def fake_plan(intent, feedback=None, exclude=(), timeout_s=180, editable=()):
            raise loop.PlanError("unknown model 'imaginary'")

        saved = loop.plan
        loop.plan = fake_plan
        self.addCleanup(setattr, loop, "plan", saved)
        with self.assertRaises(loop.PlanError):
            loop.run("x", max_steps=2, out=self.quiet)

    def test_a_report_naming_no_plan_does_not_crash(self):
        import io
        result = {"intent": "x", "settled": False, "attempts": 1, "seconds": 1.0,
                  "output": None, "unsettled": None,
                  "steps": [{"attempt": 1, "plan": None, "output": None,
                             "described": None, "missing": [],
                             "refused": "unknown model 'imaginary'"}]}
        buffer = io.StringIO()
        loop.report(result, out=buffer)
        self.assertIn("could not be planned", buffer.getvalue())

    def test_the_last_line_skips_traceback_frames_and_carets(self):
        # A traceback truncated mid-frame ends on a caret line; taking the
        # last non-blank line would report "^^^^^^" as the failure.
        self.assertEqual(
            loop._last_line('  File "/x/y.py", line 9, in load\n'
                            "    weights = load(component)\n"
                            "              ^^^^^^^^^^^^^^^"),
            "weights = load(component)")

    def test_the_images_in_the_intent_reach_the_planner(self):
        # The whole edit feature hangs off this one line in run(). Without a
        # test, a regression that silently disabled it would look like the
        # planner simply preferring to generate.
        import tempfile
        directory = tempfile.mkdtemp()
        png = os.path.join(directory, "shot.png")
        open(png, "wb").close()
        offered = []

        def fake_plan(intent, feedback=None, exclude=(), timeout_s=180, editable=()):
            offered.append(list(editable))
            return loop._validate({"model": "fast", "prompt": "p",
                                   "expect": ["cat"]})

        saved = (loop.plan, generate.generate_image, loop.look)
        loop.plan = fake_plan
        generate.generate_image = lambda **k: "/tmp/out.png"
        loop.look = lambda path, timeout_s: "a cat"
        self.addCleanup(lambda: (setattr(loop, "plan", saved[0]),
                                 setattr(generate, "generate_image", saved[1]),
                                 setattr(loop, "look", saved[2])))
        loop.run("edit %s" % png, max_steps=1, out=self.quiet)
        self.assertEqual(offered[0], [png])

    def test_a_report_of_an_edit_names_the_file_not_a_model(self):
        # An edit plan carries no "model" key, so an inverted branch here
        # would be a KeyError on every real edit.
        import io
        result = {"intent": "x", "settled": True, "attempts": 1, "seconds": 1.0,
                  "output": "/tmp/edited.png", "unsettled": None,
                  "steps": [{"attempt": 1, "output": "/tmp/edited.png",
                             "described": "a yellow wall", "missing": [],
                             "plan": {"action": "edit", "image": "/tmp/in.png",
                                      "prompt": "make it yellow",
                                      "expect": ["yellow"], "why": ""}}]}
        buffer = io.StringIO()
        loop.report(result, out=buffer)
        self.assertIn("edited   /tmp/in.png", buffer.getvalue())
        self.assertNotIn("model  ", buffer.getvalue())

    def test_a_failed_edit_is_not_told_a_model_was_withheld(self):
        # Nothing is withheld on an edit failure - there is one edit model -
        # so the generate-only phrasing described something that had not
        # happened and pointed at a list the plan had not chosen from.
        sent = []
        saved = loop._chat
        loop._chat = lambda messages, model, timeout_s, **k: (
            sent.append(messages) or '{"model": "fast", "prompt": "p", "expect": ["c"]}')
        self.addCleanup(setattr, loop, "_chat", saved)
        loop.plan("x", feedback={"plan": {"action": "edit", "image": "/tmp/a.png"},
                                 "refused": "qwen-edit exploded"})
        text = sent[0][-1]["content"]
        self.assertNotIn("removed from the list", text)
        self.assertIn("generating a new image instead is allowed", text)

    def test_a_failed_generate_is_still_told_the_model_was_withheld(self):
        sent = []
        saved = loop._chat
        loop._chat = lambda messages, model, timeout_s, **k: (
            sent.append(messages) or '{"model": "fast", "prompt": "p", "expect": ["c"]}')
        self.addCleanup(setattr, loop, "_chat", saved)
        loop.plan("x", feedback={"plan": {"action": "generate", "model": "fancy"},
                                 "refused": "boom"})
        self.assertIn("removed from the list", sent[0][-1]["content"])

    def test_a_report_of_a_run_that_rendered_nothing_does_not_crash(self):
        import io
        self._stub([{"model": "fast", "prompt": "a", "expect": ["cat"]}],
                   lambda p: "a cat",
                   render=lambda call, **k: (_ for _ in ()).throw(
                       ValueError("model 'fast' does not support --steps. "
                                  "Models that do: fancy")))
        result = loop.run("x", max_steps=1, out=self.quiet)
        buffer = io.StringIO()
        loop.report(result, out=buffer)
        self.assertIn("nothing was rendered", buffer.getvalue())
        self.assertIn("does not support", buffer.getvalue())


class TestMenu(unittest.TestCase):
    def test_the_menu_is_built_from_the_catalog_not_written_out(self):
        # A model added or a cap corrected has to show up here without anyone
        # remembering to edit a prompt.
        real = generate.MODELS
        generate.MODELS = {"only": {"caps": {"seed", "steps"}, "steps": 4,
                                    "note": "the note"}}
        self.addCleanup(setattr, generate, "MODELS", real)
        rows = loop.menu()
        self.assertEqual([r["model"] for r in rows], ["only"])
        self.assertEqual(rows[0]["accepts"], ["seed", "steps"])
        self.assertEqual(rows[0]["default_steps"], 4)

    def test_a_model_with_no_measured_timing_still_appears(self):
        real = generate.MODELS
        generate.MODELS = {"fresh": {"caps": set(), "steps": None, "note": ""}}
        self.addCleanup(setattr, generate, "MODELS", real)
        self.assertIsNone(loop.menu()[0]["measured_seconds"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
