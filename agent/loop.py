"""fxlla do - one intent, one plan, one action, one look, one retry.

Everything else in fxlla answers a question it was asked precisely: which
model, which flags, which prompt. This asks for an outcome instead and works
out the rest, which is the one thing the project had been leaving to whatever
was driving it. That was the right call while the alternative was a client
loading an MCP server and orchestrating by hand. It stops being the right call
once the decision needs facts only fxlla holds - which models exist, what each
one actually accepts, how long each takes, what the render came out looking
like. A caller cannot plan well against a menu it has to guess at.

The shape is deliberately small, and each step is a different KIND of thing so
that no step is trusted with a job it cannot do:

    plan    a local model turns the intent into one concrete call, chosen
            from the real catalog. Anything it invents is refused here,
            before a render spends four minutes proving it.
    act     the existing generator runs. No new render path.
    look    the vision model ENUMERATES what is in the result. It is never
            asked whether the result is correct: a model asked "is this a red
            car?" agrees, and an agreement is worth nothing.
    check   plain code compares the enumeration against what the planner
            committed to in advance. No model judges another model's output;
            one states expectations, another reports sightings, and arithmetic
            decides whether they line up.
    retry   at most once, told what was expected and what was reported.

The check reports what the DESCRIPTION did or did not mention, which is a fact
about the description and not a verdict on the image. A miss is a reason to
look again, not proof of failure, and the output says so - the alternative is
a loop that confidently discards good work because a describer was terse.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "media"))

import generate  # noqa: E402  (path set above)
import weights  # noqa: E402

GATEWAY_HOST = os.environ.get("FXLLA_HOST", "127.0.0.1")
GATEWAY_PORT = os.environ.get("FXLLA_PORT", "8080")
PLANNER = os.environ.get("FXLLA_AGENT_MODEL") or os.environ.get(
    "FXLLA_DEFAULT_MODEL", "qwen3-coder")

# Two budgets rather than one. Steps bound how many times it is willing to be
# wrong; seconds bound the wall clock, because a single render on a slow model
# can outlast a reasonable step count on its own.
MAX_STEPS = int(os.environ.get("FXLLA_AGENT_MAX_STEPS", "2"))
MAX_SECONDS = int(os.environ.get("FXLLA_AGENT_MAX_SECONDS", "900"))

# Asked without naming what we hope to find. Naming it invites agreement, and
# the whole value of this step is that it was not told the answer.
LOOK = ("List what is actually visible in this image: subjects, setting, "
        "colours, and any text you can read, quoting text exactly. Describe "
        "only what is present. Do not guess at intent or judge quality.")

# What the planner may set. Everything outside this is refused rather than
# forwarded: the catalog's caps already say which flags a model accepts, and a
# planner that invents one should learn that here and not in the backend.
FIELDS = ("model", "prompt", "seed", "steps", "width", "height", "aspect",
          "negative", "guidance", "preset")


class PlanError(Exception):
    """The planner returned something the catalog cannot honour."""


def _downloaded(alias):
    """Whether this model's weights are actually on this machine.

    The catalog lists what fxlla knows how to run, not what it can run right
    now, and offering the difference sets the planner up to fail: the refusal
    would arrive from deep inside the backend, after the gateway had already
    unloaded every resident model to make room. Two of the sixteen are absent
    on the machine this was written on, and neither is distinguishable from
    the rest by anything the planner can see.
    """
    try:
        missing, _size = weights.missing_for("image", alias)
        return not missing
    except Exception:
        # Never let a probe decide the model is unusable: a changed cache
        # layout would silently empty the menu.
        return True


def menu(only_downloaded=True):
    """The real capabilities, as the planner will be shown them.

    Built from the catalog rather than written out, so a model added or a cap
    corrected shows up here without anyone remembering to update a prompt.
    Timings come from what was actually measured on this machine, because
    "which model" is mostly a question about how long the user will wait.
    """
    try:
        timings = generate.observed_timings()
    except Exception:
        timings = {}
    rows = []
    for alias in sorted(generate.MODELS):
        if only_downloaded and not _downloaded(alias):
            continue
        spec = generate.MODELS[alias]
        seen = timings.get(alias) or {}
        rows.append({
            "model": alias,
            "accepts": sorted(spec.get("caps") or ()),
            "default_steps": spec.get("steps"),
            "measured_seconds": seen.get("median_s") if isinstance(seen, dict) else None,
            "note": (spec.get("note") or "")[:160],
        })
    return rows


def _chat(messages, model, timeout_s, max_tokens=900):
    """One completion from the local gateway."""
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    url = "http://%s:%s/v1/chat/completions" % (GATEWAY_HOST, GATEWAY_PORT)
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            answer = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError("the planner refused this: %s"
                           % exc.read().decode("utf-8", "replace")[:400])
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "no gateway at %s:%s (%s). Start one with `fxlla serve`."
            % (GATEWAY_HOST, GATEWAY_PORT, exc.reason))
    choices = answer.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "") if choices else ""
    if not text.strip():
        raise RuntimeError("the planner returned nothing")
    return text.strip()


def _extract_json(text):
    """The first JSON object in a reply.

    Local models wrap JSON in prose or a fence more often than not, and losing
    a correct plan to a stray "Here you go:" would be a self-inflicted retry.

    Braces are counted only OUTSIDE strings. Counting them everywhere looks
    fine until a prompt contains one, and prompts here contain them often -
    ideogram4 takes a JSON caption, so a brace inside the "prompt" value is
    the normal case rather than the exotic one. A depth that closed early
    turned a perfectly valid plan into "no JSON object in the planner's reply".
    """
    depth, start, in_string, escaped = 0, None, False, False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            if depth:
                in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except ValueError:
                    start = None
    raise PlanError("no JSON object in the planner's reply: %s" % text[:200])


PROMPT = """You plan ONE local image generation and nothing else.

Reply with a single JSON object, no prose:
{"model": "...", "prompt": "...", "expect": ["...", "..."], "why": "..."}

Rules:
- "model" MUST be one from the list below.
- Only include an optional key if that model's "accepts" list allows it:
  %s
- "prompt" is the text the image model receives. Write it for an image model.
- "expect" is 2 to 5 SINGLE distinctive words that should be visible in the
  result - concrete nouns and colours, never adjectives about quality. They
  are checked against an independent description of the render, so choose
  words a describer would actually use.
- "why" is one short sentence on why this model.

Available models:
%s"""


def _last_line(text):
    """The exception out of a traceback, not a byte slice of one.

    Backend failures arrive as a whole Python traceback in stderr. Slicing the
    tail of it cuts mid-word and leads with frame noise; the last non-empty
    line is the message that actually says what went wrong.
    """
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith(("File \"", "^", "~")):
            return line[:300]
    return (lines[-1] if lines else "")[:300]


def plan(intent, feedback=None, exclude=(), timeout_s=180):
    """One concrete call, validated against what the catalog can honour."""
    offered = [row for row in menu() if row["model"] not in exclude]
    if not offered and exclude:
        raise PlanError(
            "every usable model failed on this request: %s" % ", ".join(sorted(exclude)))
    if not offered:
        raise PlanError(
            "no image model on this machine has its weights downloaded. Pull "
            "one first: fxlla media weights, then fxlla pull media:<alias>")
    optional = ", ".join(f for f in FIELDS if f not in ("model", "prompt"))
    messages = [
        {"role": "system", "content": PROMPT % (optional, json.dumps(offered, indent=1))},
        {"role": "user", "content": intent},
    ]
    if feedback:
        # The retry is told what happened, not what to do about it. Handing it
        # a fix would make this a template with extra steps. Two things can
        # have happened, and they are not the same failure: the plan was
        # refused before rendering, or it rendered and the description came
        # back short. The refusal already names the models that would have
        # worked, so it is passed through verbatim rather than summarised.
        messages.append({"role": "assistant", "content": json.dumps(feedback["plan"])})
        if feedback.get("refused"):
            messages.append({"role": "user", "content":
                             "That plan failed: %s\nThat model has been removed "
                             "from the list above. Plan one more attempt at the "
                             "original request with one that is still listed."
                             % feedback["refused"]})
        else:
            messages.append({"role": "user", "content":
                             "That render was made. An independent description of it "
                             "did not mention: %s.\nThe description was:\n%s\n\n"
                             "Plan one more attempt at the original request."
                             % (", ".join(feedback["missing"]), feedback["described"])})
    return _validate(_extract_json(_chat(messages, PLANNER, timeout_s)), exclude)


def _validate(chosen, exclude=()):
    """Refuse what the generator cannot be asked, and only that.

    Deliberately does NOT re-check the model's capabilities. build_command
    already refuses every unsupported flag by name, for every field including
    seed and steps, and it does so before the render starts - so a second cap
    check here would be a duplicate authority that drifts from the catalog the
    moment one is corrected. That drift is the exact bug this project spent a
    week removing from the other direction. What is left is what the generator
    genuinely cannot tell us: an alias that does not exist, an empty prompt, a
    plan that commits to nothing checkable, and an invented key that would
    reach generate_image as a TypeError instead of a message.
    """
    if not isinstance(chosen, dict):
        raise PlanError("the plan is not an object")
    model = chosen.get("model")
    if model not in generate.MODELS:
        raise PlanError("unknown model %r; the catalog has: %s"
                        % (model, ", ".join(sorted(generate.MODELS))))
    if model in exclude:
        # Withholding a model from the menu is not the same as refusing it.
        # The whole point of removing a failed model was that obeying should
        # not be optional, and a planner that names it anyway - which is
        # exactly what the one here did when merely told in prose - would
        # otherwise sail straight through into another doomed render.
        raise PlanError("%s already failed on this request and was withheld "
                        "from the list; it cannot be chosen again" % model)
    if not (chosen.get("prompt") or "").strip():
        raise PlanError("the plan has no prompt")
    expect = [w for w in (chosen.get("expect") or []) if isinstance(w, str) and w.strip()]
    if not expect:
        raise PlanError("the plan commits to nothing visible; 'expect' is empty")
    out = {"model": model, "prompt": chosen["prompt"].strip(),
           "expect": [w.strip() for w in expect][:5],
           "why": (chosen.get("why") or "").strip()}
    for field in FIELDS:
        if field in ("model", "prompt") or field not in chosen:
            continue
        if chosen[field] not in (None, ""):
            out[field] = chosen[field]
    return out


def look(path, timeout_s):
    """What the vision model reports seeing. Never what it thinks of it."""
    return generate.describe_image(path, question=LOOK, timeout_s=timeout_s)


def check(expect, described):
    """Which committed words the description did not mention.

    Substring, case-folded, and nothing cleverer on purpose. This is not
    comprehension and does not pretend to be: it answers "did the describer say
    this word", which is checkable, instead of "is this in the picture", which
    is not. Everything it returns is phrased as a miss in the description.
    """
    low = described.lower()
    return [word for word in expect if word.lower() not in low]


def run(intent, max_steps=MAX_STEPS, max_seconds=MAX_SECONDS, out=sys.stderr):
    """The loop. Returns the record of what it did, whether or not it settled."""
    started = time.time()
    steps, feedback, last = [], None, None
    # A model that failed is removed from the next menu rather than merely
    # mentioned in the feedback. Told in prose that schnell had just died, the
    # planner chose schnell again with the same reasoning - so the constraint
    # is applied where obeying it is not optional.
    failed = set()

    def left():
        return max_seconds - (time.time() - started)

    for attempt in range(1, max_steps + 1):
        if left() <= 0:
            break
        print("[do] planning (%d/%d)" % (attempt, max_steps), file=out, flush=True)
        try:
            chosen = plan(intent, feedback, exclude=failed,
                          timeout_s=max(30, min(180, left())))
        except PlanError as exc:
            # Only the FIRST plan is allowed to end the run. After that an
            # artifact may already exist, and letting a bad reply escape here
            # threw it away: the render was logged, then main() printed the
            # planner's error and exited 1, indistinguishable from a run that
            # produced nothing. A later planning failure is recorded like any
            # other and the report still hands over what was made.
            if not steps:
                raise
            print("[do] planning failed: %s" % exc, file=out, flush=True)
            steps.append({"attempt": attempt, "plan": None, "output": None,
                          "described": None, "missing": [], "refused": str(exc)})
            break
        print("[do] %s: %s" % (chosen["model"], chosen["why"] or chosen["prompt"][:70]),
              file=out, flush=True)
        if left() <= 0:
            break
        try:
            path = generate.generate_image(**{k: v for k, v in chosen.items()
                                              if k not in ("expect", "why")})
        except (ValueError, RuntimeError) as exc:
            # Two shapes, one response. ValueError is the generator refusing a
            # flag before spending the render, naming both it and the models
            # that would have taken it. RuntimeError is the backend failing
            # partway, which happens for reasons no amount of planning can
            # anticipate - the first live run of this loop died on one, from a
            # model whose weights were fully cached. Either way the run should
            # continue knowing what happened rather than end: a second model
            # is usually available and usually works.
            reason = _last_line(exc)
            failed.add(chosen["model"])
            print("[do] %s failed: %s" % (chosen["model"], reason), file=out, flush=True)
            record = {"attempt": attempt, "plan": chosen, "output": None,
                      "described": None, "missing": [], "refused": reason}
            steps.append(record)
            last = record
            feedback = record
            continue
        print("[do] rendered %s" % path, file=out, flush=True)
        described = look(path, timeout_s=max(30, min(600, left())))
        missing = check(chosen["expect"], described)
        record = {"attempt": attempt, "plan": chosen, "output": path,
                  "described": described, "missing": missing}
        steps.append(record)
        last = record
        if not missing:
            return _result(intent, steps, settled=True, started=started)
        print("[do] the description did not mention: %s" % ", ".join(missing),
              file=out, flush=True)
        feedback = record
    return _result(intent, steps, settled=False, started=started, last=last)


def _result(intent, steps, settled, started, last=None):
    # The last attempt is not always the last artifact: a plan refused after a
    # render succeeded leaves a file worth returning, and reporting None there
    # would throw away work the user can still use.
    rendered = [s for s in steps if s.get("output")]
    return {"intent": intent, "settled": settled, "attempts": len(steps),
            "seconds": round(time.time() - started, 1),
            "output": (rendered[-1]["output"] if rendered else None),
            "steps": steps, "unsettled": None if settled else last}


def report(result, out=sys.stdout):
    """What it did and why, in the order a person needs it."""
    rendered = [s for s in result["steps"] if s.get("output")]
    if not rendered:
        print("nothing was rendered.", file=out)
        for step in result["steps"]:
            if step.get("refused"):
                # A step whose planning failed has no plan to name.
                chosen = (step.get("plan") or {}).get("model")
                print("  attempt %d %s: %s"
                      % (step["attempt"],
                         "planned %s and it failed" % chosen if chosen
                         else "could not be planned",
                         step["refused"]), file=out)
        if not result["steps"]:
            print("  the budget ran out before the first attempt finished",
                  file=out)
        return
    final = rendered[-1]
    print(file=out)
    print(final["output"], file=out)
    print(file=out)
    print("model    %s" % final["plan"]["model"], file=out)
    print("prompt   %s" % final["plan"]["prompt"], file=out)
    if final["plan"].get("why"):
        print("why      %s" % final["plan"]["why"], file=out)
    print("took     %ss over %d attempt(s)" % (result["seconds"], result["attempts"]),
          file=out)
    print(file=out)
    print("looked at it and saw:", file=out)
    for line in final["described"].splitlines():
        print("  %s" % line, file=out)
    if final["missing"]:
        print(file=out)
        print("Not settled. The description never mentioned: %s."
              % ", ".join(final["missing"]), file=out)
        print("That is a gap in the description, not proof the image is wrong "
              "- look at it yourself before rerunning.", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fxlla do",
        description="State an outcome; fxlla picks the model, renders, looks "
                    "at the result and retries once if the description does "
                    "not mention what the plan committed to.")
    parser.add_argument("intent", help="what you want, in plain words")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS,
                        help="attempts before it gives up (default %d)" % MAX_STEPS)
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS,
                        help="wall-clock budget (default %d)" % MAX_SECONDS)
    parser.add_argument("--json", action="store_true",
                        help="print the whole record instead of a summary")
    args = parser.parse_args(argv)
    try:
        result = run(args.intent, args.max_steps, args.max_seconds)
    except (PlanError, RuntimeError, ValueError) as exc:
        print("fxlla do: %s" % exc, file=sys.stderr)
        return 1
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        report(result)
    return 0 if result["settled"] else 2


if __name__ == "__main__":
    sys.exit(main())
