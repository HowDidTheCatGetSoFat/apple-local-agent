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
```

Each model runs alone on a cold, dedicated server (`FXLLA_EVAL_PORT`, default
8097), so load time is honestly cold and models never share the GPU. A running
`fxlla on` server refuses the eval; a running gateway gets its resident models
freed through the same unload endpoint the media path uses, and reloads them on
demand afterward.

## What is measured

- **code (10 tasks)**: write-from-spec and fix-the-bug Python, executed in a
  sandbox against hidden asserts. Extraction takes the LAST fenced block of the
  reply, after stripping think-blocks.
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
the table.

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
file size, descriptors, process count), a Python-level socket tripwire, a wall
clock that kills the whole process group, and - when `/usr/bin/sandbox-exec`
exists - a macOS seatbelt denying network and out-of-directory writes on top.
Verdicts are identical with and without the seatbelt, which is what makes it
safe to ship. This is an **accident guard, not a security boundary**: it
contains runaway loops, fork storms, giant files, and env leakage from code a
cooperative model wrote for tiny tasks. Evaluating weights you do not trust at
all belongs in a virtual machine.

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
