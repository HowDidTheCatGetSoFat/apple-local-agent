# fxlla eval

Measure chat models as fxlla serves them, so choosing a model rests on data
instead of the catalog's prose. The unit under measurement is the deployment -
weights + quantization + engine + server - because that is what you actually
run. Standard library only, no network at eval time, and quality is scored by
mechanical checks alone: executing generated code against asserts, comparing
structured tool calls, validating output shape. No model ever judges another
model's prose here; a judge model would make every score depend on the judge.

```sh
fxlla eval                      # every cached model with role dev/agentic/max
fxlla eval qwen3-coder tiny     # exactly these (named models skip the RAM guard)
fxlla eval --quick              # 1 task per dimension: a pipeline check, not a ranking
fxlla eval --dim tools,code     # a subset of dimensions
fxlla eval --json               # machine-readable output
fxlla eval --list               # show the plan and fingerprint, run nothing
fxlla eval --keep-failed        # keep failed code tasks' scratch dirs for inspection
```

Each model runs alone on a cold, dedicated server (`FXLLA_EVAL_PORT`, default
8097), so load time is honestly cold and models never share the GPU. A running
`fxlla on` server refuses the eval; a running gateway gets its resident models
freed through the same unload endpoint the media path uses, and reloads them on
demand afterward.

## What is measured

- **code (10 tasks)**: write-from-spec and fix-the-bug Python, executed in a
  sandbox against hidden asserts. Extraction strips think-blocks, then
  concatenates in reply order every fenced block that compiles AND binds a
  name (imports, defs, classes, assignments), so later iterations of a
  function shadow earlier ones while fenced example output, bare demo calls
  and quoted fragments fall away. Each part of that rule exists because a
  real model's reply style broke a simpler rule and silently lowered a
  correct score; the fallbacks (all compiling fences, then the last fence,
  then the whole reply) keep genuine syntax errors visible. Extraction
  changes bump the harness version - it is at v3 for exactly this reason.
- **tools (8 tasks)**: structured `tool_calls` with the right function and
  exact arguments, plus abstention (a question needing no tool), reading a tool
  result from history, and stopping after a permission error. A correct call
  that only appears in the text channel fails the headline and fills a separate
  "recoverable" column: opencode and Claude Code never see the text channel, so
  counting it would inflate a score no agent stack can collect - but the remedy
  (a serving-layer fix) differs from a model that cannot call at all, and the
  report says which one you are looking at.
- **instructions (8 tasks)**: exact output, JSON shape with types and arity,
  line rules, forbidden words, word caps. All checks are string, regex, count,
  or parse mechanics.
- **candor (8 tasks)**: whether a model will decline what cannot be done
  rather than comply with it. Four traps and four controls: a fabricated
  stdlib symbol it should call fake, a real one it should call real, a
  self-contradicting specification, a request to process data never given.
  The pairing is the design - a model answering FAKE to everything scores
  perfectly on the traps alone. For the three exact-match pairs it also
  separates format failure from lost candor, because both halves are equally
  strict about stray prose. The impossible/possible pair does NOT balance that
  way: the trap is an exact match while the control runs the code, and code
  extraction deliberately discards surrounding text. A merely chatty model can
  fail that trap alone, so read it with the other three rather than on its own. It exists because refusing correctly is a capability
  and nothing else here asked for it, which matters when comparing a model
  whose refusal direction was surgically removed against the one it came from.
- **context (4 tasks)**: serials planted in ~8k and ~16k tokens of generated
  filler at controlled depths; pass is exact containment. These double as the
  long-prompt TTFT and prefill measurements.

Speed: cold start to ready (engine-aware readiness: llama-server answers HTTP
while weights still load, so gguf polls `/health`), the first request timed
separately from the TTFT median, decode tok/s as the median over every
streamed response with 64+ tokens, prefill tok/s and TTFT at 8k/16k from the
context tasks, and peak server RSS (process RSS on unified memory - an
approximation). Tokens spent and wall time are first-class columns: a model
that scores well while burning four times the tokens pays its real cost in
the table. Wall time includes the sandbox checking of code tasks (recorded
per task as `check_s` in the run JSON); tokens spent is the purely
model-side cost. With `--repeats`, every headline number is frozen at the
first pass - repeat passes only feed the flipped-task list.

The engines split the cold cost differently, and the table would lie if it
pretended otherwise: llama-server loads weights before its `/health` flips, so
its `load_s` is the real load and `first_s` adds warmup; mlx_lm.server starts
instantly and loads the weights on the FIRST request, so its `load_s` is
process startup and the 16 GB land inside `first_s` (measured: qwen3-coder
shows load_s 0.75 and first_s 7.3). Compare cold costs as load_s + first_s,
never load_s alone across engines.

## Reading the numbers

Every run prints a **fingerprint** (sha256 prefix over the RENDERED task set:
exact prompts, generated filler, tool schemas, check specs) and a **harness
version** (bumped when extraction, checking, probing, or sandbox semantics
change). Two runs are comparable exactly when both match - a string
comparison, not a judgement call.

The task counts are small on purpose (a sweep must stay cheap enough to run
after every pull), and the report states the consequence: one task is 10-25
points depending on the dimension, so a 1-2 task gap is noise. Speed gaps
inside two models' printed min..max spreads are noise. Scores are
per-machine: results carry the hardware identity, and numbers from different
machines rank nothing. `--repeats N` reruns the quality tasks and lists the
task ids that flipped - that list is the model's own measured noise floor.

Results go to stdout and to `$XDG_STATE_HOME/fxlla/evals/` (per-run JSON plus
a `history.jsonl` line). Nothing under the repository tree is ever written by
the harness, and the harness never reads past results: recorded numbers can
never re-enter the measurement.

## The sandbox

Model-generated code runs under `python3 -I` in a scratch directory with an
env allowlist (the caller's tokens and config never reach it), rlimits (CPU,
file size, descriptors, process count), a Python-level socket tripwire, a
pass verdict that requires the check's own completion sentinel (exit status
alone is forgeable by `os._exit(0)`), a wall clock that kills the whole
process group, and - when `/usr/bin/sandbox-exec` exists - a macOS seatbelt
denying network and out-of-directory writes on top.

The layers are not equivalent, and saying so matters: the seatbelt enforces
at the OS level what the Python tripwire only trips on the common path (the
raw `_socket` module stays importable, and out-of-directory writes have no
Python-level counterpart at all). For code that does what the tasks ask, the
paths agree; for code probing the sandbox itself, the seatbelt can flip a
verdict from pass to fail, never the reverse. The Linux CI exercises the
weaker stdlib path, which is therefore the floor. This is an **accident
guard, not a security boundary**: it contains runaway loops, fork storms,
giant files, and env leakage from code a cooperative model wrote for tiny
tasks. Evaluating weights you do not trust at all belongs in a virtual
machine.

## The task set

`tasks.json` is authored from scratch for this harness: no benchmark dumps
(copyright, and training contamination), and no task references this
repository or has its answer anywhere in it, so editing repo docs can never
move a score - the retrieval eval (`fxlla kb eval`) paid for that lesson
first. The file is append-only: never edit an existing task id; add a new id,
which mints a new fingerprint and visibly ends comparability. A task that
every model passes or every model fails discriminates nothing and should be
replaced. The repository is public, so future models may train on these tasks;
scores rank local models against each other on one fingerprint and are not
absolute ability measures. `--tasks FILE` swaps in a private set, which is the
only real defense.

## Out of scope, deliberately

Helpfulness and style (not mechanically checkable), refusal behavior,
multi-turn agent loops (fxlla has no agent loop), perplexity (needs logits,
not a chat API), and cross-machine leaderboards.

## Token budgets

Every task declares a `max_tokens`, and the harness raises it to at least
`ANSWER_FLOOR` (2048, override with `FXLLA_EVAL_ANSWER_FLOOR`). A reasoning
model spends tokens thinking before it emits any, so a budget sized for the
answer alone is gone by the time the answer starts - the model is scored on an
empty string and the number reads as incapacity. Measured on a Qwen3.5 pair:
every dimension budgeted at 256 or 512 collapsed, `code` at 2048 did not, and
what the run compared was which model happened to think less. The floor raises
and never lowers, because a task asking for more room knows something it does
not. It is applied when tasks are rendered, which is the form that gets
fingerprinted, so changing it moves the hash and runs either side of the change
do not look comparable.
