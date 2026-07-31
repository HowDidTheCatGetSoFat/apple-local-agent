# Journal

Engineering log of decisions and findings. Newest entry on top.

## 2026-07-31: A render that takes minutes, behind a transport that waits seconds

Read the same editor session again after the day's fixes landed and found the
tools failing a second way, one the earlier reading could not have shown
because it only appears once the tools work well enough to be used for real
renders.

**The server went deaf.** A poster render was submitted synchronously. The MCP
read loop was `for line in sys.stdin` with the tool call handled inline, so
while mflux ran, the server read nothing: every later call queued behind it and
came back to the model as `MCP error -32001: Request timed out` - including the
`media_job_status` and `list_media_jobs` calls asking about that very render.
The model, unable to tell a timeout from a failure, submitted the same render
three more times. Four renders competing for the same GPU, none of them
visible.

Two changes, and the ordering matters. Handling each tool call on its own
thread stops the server going deaf - but alone it fixes nothing the caller can
perceive, because a four-minute render still outlasts the client's timeout. So
a render is now submitted as a background job and awaited for a bounded window
(`FXLLA_MCP_WAIT_S`, default 45): finished in time, the path comes back as
before; not finished, the job id does, with a sentence saying it is still
running and not to submit it again. `media_job_status` caps its own wait at the
same window for the same reason - the model asked to block 120 seconds and got
a timeout, which told it nothing at all.

The 45 is measured, not chosen: in that session a 45 s call returned its result
and calls past roughly 69 s were reported timed out. It is a knob because the
number belongs to whatever client is calling, not to us.

**Where the bound comes from is the general lesson.** A blocking interface is
only correct while the work fits inside someone else's patience, and that
someone is not in our repo. When the work can exceed it, the honest move is to
return a handle and a status, because the alternative is not a slow answer but
an ambiguous one - and an agent resolves ambiguity by retrying.

**Then the fixed version ran, and the third instance of the same mistake showed
up.** The poster prompt said "guardalo en ~/Downloads". The model picked
ideogram4, corrected 1080 to 1088 on its own from `dim_step`, submitted the
job, followed it without resubmitting - everything the day's work was for - and
wrote the file to the media directory anyway, because no MCP generator exposes
`output`. The CLI has taken `--output` on all five since the beginning. Only
the schemas were missing it, so the request was accepted and dropped, and the
path in the answer read like a result rather than a default.

Three times now, on the same surface: the capability existed and the caller had
no way to reach it. It is worth naming what makes this recur - the CLI is where
the feature gets built and the MCP schema is a separate list that has to be
edited by hand, so every new option is one edit away from being invisible. The
test that catches it does not check any single flag; it walks every generator
and asserts the argv carries what the schema advertises.

Two smaller findings from the same reading, both the same shape as yesterday's:

- `list_loras` reports a path and a name; the model passed the name and got
  "LoRA not found: Krea2-realism-V1.safetensors", having been shown that exact
  string a moment earlier. A bare filename now resolves against the search
  directories. A name with a directory part in it still does not - a path the
  caller spelled out is theirs to get right, and guessing would hide a typo.
- A 1080-wide poster died on `width must be a multiple of 16` after the
  weights were resident. Ideogram 4 is the only model that enforces this
  (mflux's other latent creators divide by 8 and silently accept anything, so a
  blanket check would refuse sizes that work), so the grid is declared per
  model, validated before anything spawns, and reported as `dim_step` from
  `list_media_models` - the failure now costs a millisecond, and is avoidable
  before it costs even that.

## 2026-07-31: The media surface, rebuilt from a transcript

Released v0.2.0, then read a real editor session and found the media tools had
been broken in ways no test could have caught, because every one of them was
about the gap between what the tools could do and what a caller could discover
or express. The whole day's work traces back to that reading.

**The first bug explains the rest.** Four MCP media calls died with
"FXLLA_STORE is not set", so the model abandoned the tools and drove the CLI
through about thirty shell commands, where the remaining defects surfaced. The
cause: the wiring copied only EXPORTED variables, and config.env is read with
`: "${VAR:=default}"`, which assigns without exporting - so only `fxlla media
wire-opencode` (which exports on its way through cmd_media) ever produced a
working registration, and `wire-opencode --all` wrote an empty environment.

Then, in order of how they were found: `--output` refused a directory (passing
one is the obvious thing to try); `--aspect` with `--width/--height` made
aspect win silently, so a request for 512x512 produced a 1024x1024 file; and
the model reported 49 frames at 24 fps as "about 10 seconds" (it is 2.04) and
declared an unmet 4-8 second requirement satisfied. That last one is why video
now returns its MEASURED duration and takes `--seconds`: a caller reading its
own request back can be wrong and sound certain.

**The catalog was the root cause of everything after.** Image models were a
flat `{cli, base_model, steps}` dict, so every per-model difference had to be
a special case and nothing could be built on top. Replacing it with a
declarative catalog whose capabilities are PROBED from each CLI's `--help`
unlocked, in one step: 8 models became 16, LoRA support, negative prompts,
prompt files, init images, guidance, controlnet with native stacking, and
depth. An unsupported option is now refused by name with the models that would
work, instead of being handed to a backend that rejects it minutes later.

Deliberately NOT abstracted: the two controlnet families take genuinely
different flags (FLUX a checkpoint plus a control image, Z-Image one combined
`type:path[:strength]` spec). The caps decide which form a model speaks, and
passing the wrong one names the form that model takes. An abstraction over the
pair would have lost information for the sake of looking tidy.

**Adversarial review caught what self-review could not**, twice. Round one: the
controlnet weight rows listed only the adapter (4 GB and 7 GB) while mflux
loads a base model too (58 GB and 33 GB), so with the adapter cached the
consent gate passed silently and the render pulled tens of gigabytes - exactly
the transfer that gate exists to stop, walked past by the person who wrote it.
Also `--prompt-file` never worked at all: mflux declares it mutually exclusive
with `--prompt` and the builder always emitted both. And the caption validator
hardcoded a 5-color palette limit where mflux allows 16 for the style palette,
a FALSE REJECTION, which is worse than a missed one because it blocks a render
that would have worked.

**Ideogram 4 takes a JSON caption**, and the trap is not guessable: bbox is
`[y_min, x_min, y_max, x_max]` - Y first - integers in a 0..1000 space, hex
uppercase. Reading mflux's own validator gave the rules; a test holds a
realistic caption so the documented prompt cannot rot. Writing that example
surfaced one more: the validator accepted lowercase hex that mflux rejects.

**Then the point of it all.** Asked for a prompt to test whether a model could
build the caption, the answer was no - `list_media_models` said "takes a JSON
caption with bboxes" and stopped, so the model would have failed for lack of
information rather than ability. The schema is now served through the same
call, with a worked example, and a test asserts the schema fxlla TEACHES
validates against the schema fxlla ENFORCES. The media skill was rewritten
from a tool list into a decision guide for the same reason - it still told the
model to POLL a job, the behaviour behind 47 status calls in one session.

**LoRA discovery took four attempts, each wrong for a different reason.**
Searching only the civitai download folder answered "none" to someone holding
ten - people train their own and keep them beside the project that produced
them. Adding the Hugging Face cache found three more, matched by filename.
Then filenames proved to lie in both directions: `Krea2-realism-V1` is an
adapter and says nothing, `Krea-2-Turbo` is a 26 GB base model a "krea" filter
would match. Metadata was no better - `modelspec.*` appears on base models,
so keying off it reported FLUX.1-schnell and three stabilityai models as
LoRAs. What settles it is structural: an adapter has `lora_A`/`lora_B`,
`lokr_`, `loha_` or `oft_` tensors, in the safetensors header, so identifying
a 2 GB file costs a few kilobytes. Final count: 10 found became 68.

Knowing a file is a LoRA still does not say which model to apply it to, and
most declare nothing. The hidden dimension does: it is a property of the model
the adapter was fitted to, so it survives renaming (1536 krea2, 4608
ideogram4, 3840 z-image, 4096 ltx2). FLUX and Qwen both sit at 3072 and are
split by Qwen's joint-attention projections. The table was measured from
adapters whose base was known independently and **recovers all 28 declared
labels in the collection it was built from** - without that check there would
be no reason to trust it on the 39 that declare nothing. Inferred bases carry
a `~` prefix everywhere they surface, because a guess presented as fact sends
someone hunting for why a correctly paired adapter did nothing.

**Two process notes.** A local model with write access to this repository
edited `media_mcp.py` mid-session, adding an `images` parameter that forwarded
a `--images` flag neither generate.py nor ltx-2-mlx has, then looped nineteen
times on the same failing edit. A blanket `git add -A` swept it into a pushed
commit. The feature was real and wanted, so it was finished rather than
reverted - but the lesson is not to blanket-stage while another agent writes
to the same tree. Separately, CI caught that `app/build.sh` had never bundled
`config/media.conf`, so every installed app shipped without a weight catalog:
the fix is a glob over `config/*.conf` rather than a list that drifts.

## 2026-07-31: Chat-model evals, the last roadmap item

`fxlla eval` closes the roadmap: quality and speed per chat model, measured on
this machine, so a model swap is a measurement instead of a guess. The design
went through a three-lens panel (measurement validity, minimal surface, the
user's decision) with an adversarial synthesis before a line was written; the
panel's blind-spot list - things all three designs missed - produced the
port-free pre-check, the engine-aware readiness, the provenance fields, and
the KV-headroom RAM guard, each of which mattered in practice.

**What it is.** 30 authored tasks (10 code, 8 tools, 8 instructions, 4
long-context), every check mechanical: code is executed against asserts in a
sandbox, tool calls compared structurally, output validated by shape, string,
count or containment. No LLM-as-judge, deliberately: a judge model would make
every score depend on the judge. Speed comes from streamed probes plus the
quality requests themselves, through the gateway's own StreamMetrics. Each
model runs alone on a cold dedicated server on FXLLA_EVAL_PORT; anything
already listening there is refused, not adopted - the borrowed-server lesson
from the embedding work, replayed on a new port. Results carry the tasks
fingerprint, a harness version, server build, weights identity and machine
identity, and land only under the state dir, which the harness never reads.

**The sweep** (M5 Max 128 GB, AC power, fingerprint f6ae21eaee7d, harness
v3, all mlx; tiny and redteam-4bit run by name):

| model | load_s | first_s | ttft | tok/s | rss_gb | code | tools | instr | ctx | tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| tiny | 1.7 | 0.6 | 104 | 587.5 | 0.5 | 1/10 | 3/8 | 3/8 | 3/4 | 5709 |
| coder-1.5b | 1.2 | 0.5 | 127 | 399.9 | 1.1 | 4/10 | 2/8 | 3/8 | 4/4 | 3526 |
| coder-3b | 0.8 | 1.1 | 189 | 224.8 | 1.9 | 4/10 | 0/8 | 6/8 | 4/4 | 5271 |
| qwen3-coder | 0.7 | 7.2 | 284 | 131.2 | 16.3 | 8/10 | 8/8 | 6/8 | 4/4 | 8203 |
| redteam-4bit | 0.9 | 17.5 | 353 | 93.5 | 42.1 | 10/10 | 8/8 | 7/8 | 4/4 | 7878 |

The gate the task set had to pass: tiny near zero on code (1/10) while the
big coders are not. It discriminates - and it already changed an answer. The
80B-A3B (redteam-4bit) beats the daily driver on code AND instructions with
structured tool calls intact, at 93.5 vs 131.2 tok/s and 42 vs 16 GB
resident: a real trade the catalog prose never surfaced. The smaller Qwen2.5
models emit correct tool calls into the TEXT channel, which opencode never
sees, and coder-1.5b leaks `<|im_end|>` into its content - serving-layer
facts the "as deployed" charter exists to surface, where the remedy is a
template/engine fix, not a smarter model.

**What the harness itself got wrong first, all caught by measurement:**

1. **load_s was a lie for mlx.** A 16 GB model reported load_s 0.75, because
   mlx_lm.server answers HTTP instantly and loads weights on the FIRST
   request - first_s is where the load went (7.3s for 16 GB, 17.5s for the
   42 GB model). llama-server is the opposite: /health flips only after the
   load. The table shows both columns and the footer says to compare cold
   costs as the sum, never load_s alone across engines.
2. **Code extraction took three rounds of measurement, one per reply style.**
   Plain last-fence: qwen3-coder closes replies with fenced example OUTPUT,
   so the demo got extracted as code and a correct solution scored as a
   SyntaxError. Last fence that compiles (v2): redteam-4bit closes with a
   fenced USAGE example that compiles fine, so v2 extracted
   `print(topo_sort(...))` without the definition and scored SIX correct
   solutions as NameError - 4/10 for a model whose true score is 10/10. Plain
   concatenation of compiling fences: the same model QUOTES compiling but
   non-self-contained fragments while explaining a bug (`if a[mid] < x:`
   alone), which crash the import. The rule that survives all three measured
   styles is v3: concatenate, in reply order, the compiling fences that BIND
   names (imports, defs, classes, assignments) - later definitions shadow
   earlier ones by Python's own semantics, and output text, bare demo calls
   and quoted fragments fall away. Every miss was a wrong LOW score, which is
   the quiet kind: nothing looks broken, a strong model just looks mediocre.
3. **The fork-orphan test was impossible to write as behavior.** RLIMIT_NPROC
   counts the user's TOTAL processes, so under the sandbox a fork can never
   succeed on a workstation and no orphan can exist to observe. The killpg
   backstop is asserted as mechanism instead, the same pattern as the rag
   flock test, with the why written down.

Mutation pass: 22 named mutants, 22 killed - after one round where the suite
itself was red and every mutant "died" spuriously, which is a reminder that a
mutation pass on a broken baseline proves nothing. Runtime: the default
3-model sweep is about 3 minutes; a 30B alone is ~95 seconds plus load.

**The pre-push adversarial review confirmed 31 findings** (5 more were raised
and refuted), and the worst were exactly the kind self-review misses. The
claim that sandbox verdicts are identical with and without the macOS seatbelt
was FALSIFIED with a five-line probe: an out-of-directory write fails under
the seatbelt and passes without it, because file writes have no Python-level
counterpart at all - the docs now state the honest contract (the seatbelt is
strictly stricter; the Linux CI floor is the stdlib path) and a test pins the
divergence direction instead of denying it. --repeats was polluting every
headline number it existed to protect (speed medians, tokens, wall absorbed
N passes while the counts stayed at pass 1); a teardown failure destroyed the
fully built record of a model that had just spent minutes of GPU time; the
flat 600s request timeout would have killed qwen3-235b's first-request load
using the same math the code's own readiness scaling calls too small; exit
status alone was forgeable by os._exit(0), so a pass now requires the check's
completion sentinel; and harness errors were being charged to the model in
the pass counts, which now exclude them from the denominator and flag the
row. Two review-round test gaps mattered as much as the code bugs: nothing
pinned the full rendered task count (a mutant scoring 4 of 30 tasks survived
green CI), and eval_model's measurement path had zero test coverage, so
probe-0 exclusion and the producer/consumer record keys were unpinned - both
now have tests, and the mutant list grew from 18 to 22.

Out of scope, stated in evals/README.md: helpfulness and style, refusals,
agent loops, perplexity, and cross-machine comparison - results name the
machine precisely so nobody mistakes a 16 GB M1's numbers for these.

## 2026-07-31: Choosable embedding model, and two bugs it exposed

The previous entry closed MLX embeddings and named the real gap: the embedding
model was effectively hardcoded, pinned to nomic-embed-text v1.5 from February
2024. This closes it. Four more `embed` aliases (bge-small 384-dim, bge-large and
qwen3-embedding 1024-dim, embeddinggemma 768-dim), selected with
`FXLLA_EMBED_MODEL`, plus `fxlla kb reindex` to move an existing base across and
`fxlla kb eval` to tell whether the move was worth it.

Building it turned up two defects that only exist once more than one model is
possible.

**The borrowed server was never asked what it held.** Reuse adopted anything
answering `/health`. Demonstrated with zero embedding models installed at all,
where `kb add` and `kb search` both still succeeded against a server on the port:
proof that nothing in the path consults the model. With one possible model that
is harmless. With five it is silent corruption, because the width guard is the
only thing standing between two models and it cannot separate nomic from
embeddinggemma (both 768) or qwen3-embedding from bge-large (both 1024).
llama.cpp answers `/props` with `model_path`, so the fix is to ask rather than to
keep a marker file that could disagree with reality. A server that will not
identify itself is still allowed - pointing `FXLLA_EMBED_PORT` at your own is a
legitimate setup, and unverifiable is not the same as wrong.

**`--pooling mean` was hardcoded.** Reading the GGUF headers directly, over HTTP
range requests so nothing had to be downloaded:

| model | arch | dim | context | declared pooling |
|---|---|---|---|---|
| nomic-embed-text-v1.5 | nomic-bert | 768 | 2048 | MEAN |
| embeddinggemma-300M | gemma-embedding | 768 | 2048 | MEAN |
| qwen3-embedding-0.6B | qwen3 | 1024 | 32768 | LAST |
| bge-small-en-v1.5 | bert | 384 | 512 | CLS |
| bge-large-en-v1.5 | bert | 1024 | 512 | CLS |

Three of five want something other than mean, and every one of them declares it.
Forcing mean was correct only for the model that happened to be the default, and
would have quietly degraded the rest: a badly pooled vector is still a vector, so
nothing downstream would ever report it. Removing the flag lets llama.cpp honour
the metadata. Verified before removing it, because "llama.cpp probably reads the
metadata" is exactly the kind of assumption that has been wrong here before:
omitting the flag reproduces `--pooling mean` to five decimals on nomic, while
`--pooling cls` visibly differs, which also proves the test could have detected a
difference. The README told users to pass `--pooling mean` when running a server
by hand; that was wrong for the same reason and is fixed.

**The eval.** 19 questions over this repository's own documentation - real prose,
no invented fixtures, no network - phrased as a person would ask them rather than
as keyword lookups. On corpus fingerprint `0c32ee03a384`:

| | recall@1 | recall@5 | MRR | median query |
|---|---|---|---|---|
| embed (nomic, 768) | 68% | 100% | 0.809 | 8 ms |
| embed-small (bge-small, 384) | 74% | 84% | 0.789 | 5 ms |

A 37 MB model beats the 100 MB default on recall@1, loses 16 points of recall@5,
lands 0.02 behind on MRR, and answers in about half the time. That is a real
trade to weigh, and the catalog notes could never have settled it.

The caveat printed with those numbers cost something to learn. The first version
of this entry quoted 67% / 89% / 0.731 for nomic. Then writing the entry edited
the corpus - `docs/JOURNAL.md` and `CHANGELOG.md` were in it - and the same
command returned 56% / 78% / 0.644. A benchmark whose score moves when you record
the score is not a benchmark. So those two append-only logs, the files where
results get written down, are out of the corpus, and the output carries a sha256
prefix over the corpus bytes: two runs are comparable exactly when the
fingerprints match, which is a string comparison instead of a judgement call. The
remaining seven files still shift when edited, and the fingerprint is what makes
that visible rather than silent.

Two honest weaknesses, both worth stating before anyone leans on these numbers.
At 7 files and 80 chunks recall@5 saturates: nomic scores 100%, so that column
cannot show an improvement. And 19 queries means one query is worth 5.3 points,
so a gap of one or two queries is noise - editing the README alone moved nomic
from 74% to 68% on recall@1 and flipped which model led. The harness is sound and
catches a real difference; it is not fine-grained enough to rank two close models,
and calling a 6-point recall@1 gap a winner would be reading the sample, not the
models. Widening it is the natural next step if model choice starts mattering
more than it does today.

Two smaller things worth recording. `.entry`, which `fxlla pull` already writes
to record the file it fetched, decides which weights to load, so re-pulling a
different quant into the same directory cannot silently change the answer the way
a glob would. And `reindex` re-embeds the chunk text already in the store instead
of re-reading sources that may have moved, in a single transaction, because a
half-re-embedded base holds two widths at once and `_kb_dim` reads whichever row
comes first - a store that lies about itself. Mutation testing on the batch: 7
mutants, 7 killed, but only after the first run found two survivors. One was a
broken mutant of mine that disabled nothing; the other was a genuinely weak test,
where the traversal payload pointed at a file that did not exist, so the fallback
fired for the wrong reason and the assertion held with or without the guard.

## 2026-07-30: MLX embeddings measured against llama.cpp, and declined

The backlog carried "optional MLX embeddings" as RAG polish. MLX is fxlla's default
engine for chat because it is the fast path on Apple Silicon, so the obvious
question is why it would not also be the path for embeddings. Measured rather than
argued, same model both engines (bge-small-en-v1.5, 384 dims, real chunks from this
repo through `chunk_text`):

| | MLX | llama.cpp |
|---|---|---|
| load the model | 7.63s | 0.35s |
| one query | 0.888s | 0.048s |
| batch of 16 | 83.8/s | 89.8/s |
| batch of 64 | 129.6/s | 104.1/s |
| batch of 256 | 167.9/s | 92.9/s |

So MLX genuinely wins on bulk throughput, about 1.8x at a batch of 256, and loses
badly on startup (22x) and single-query latency (18x). That settles it against MLX
here, because the shape of the work is the opposite of what MLX is good at: `fxlla
kb search` starts a process, embeds one query, and exits. Adopting MLX would make
the common path go from 0.23s to over 7.6s in order to make bulk indexing 1.8x
faster - and indexing this whole repo already takes 2.7s, so the prize is about a
second, once.

The only way to collect that 1.8x is to keep an MLX process alive across queries to
amortise the 7.63s load, which is the persistent embedding daemon already declined
on measurement earlier the same day.

Worth recording that an earlier version of this note claimed MLX offered "nothing"
and that its models were all available in GGUF anyway. The first half was wrong -
1.8x is not nothing - and the conclusion survived for a different reason than the
one first given. The second half checked out: EmbeddingGemma and BGE-M3 are both
published as GGUF, by more publishers than MLX has, since llama.cpp supports
embeddings natively and ggml-org keeps up.

Maturity also argues against it: `mlx-embeddings` is at 0.1.0, and
`mlx-community/embeddinggemma-300m-4bit` - the strongest small embedding model of
2025 - raises `TypeError: Model.__call__() got an unexpected keyword argument
'input_ids'` even though its architecture (`gemma3_text`) is listed as supported.

What the item was really reaching for is model choice, and that is a separate and
real gap: `_embed_model()` globs `<store>/models/embed/*.gguf` and takes the first
match, and the catalog holds exactly one `embed` alias, pinned to nomic-embed-text
v1.5 from February 2024. Choosing a newer model today means deleting files by hand.
That is fixable without a second engine.

## 2026-07-30: Media weight catalog and pre-fetch

Closed the media half of the reproducibility gap. `config/media.conf` maps each
media alias to the Hugging Face repositories it actually needs, `fxlla media
weights` shows sizes and cached state, `fxlla pull media:<alias>` pre-fetches, and
`fxlla doctor` reports the ready count.

- The repo ids were not guessed. A subagent traced mflux's `[project.scripts]` to
  each model's `ModelConfig.model_name` (every mflux model is self-contained: VAE,
  transformer, and text encoder all come from subdirectories of one repo), and
  reading ltx-2-mlx showed its *inference* pipelines default
  `gemma_model_id = mlx-community/gemma-3-12b-it-4bit` as the text encoder. So the
  video alias pulls two repos; shipping only the LTX repo would have left a fresh
  machine unable to render. Sizes come from the HF API, not from guesses.
- Media weights go to the Hugging Face *cache*, not `FXLLA_STORE`: the toolchains
  resolve them by repo id through huggingface_hub, so cache layout is what
  matters. That forces the transfer through the HF CLI, which has no rate limit -
  documented plainly, because the bandwidth cap is otherwise a core promise. aria2
  cannot produce a valid cache layout (blobs, snapshots, symlinks), so capping and
  correctness were mutually exclusive here and correctness won.
- `HF_HOME` is set only when `FXLLA_MEDIA_HF_HOME` is set, mirroring what
  `media/generate.py` already does, so a pull and a render always agree on the
  location. Deliberately did not default it to `<store>/huggingface`: that would
  have stranded weights already sitting in `~/.cache/huggingface` and triggered
  tens of gigabytes of silent re-downloading.
- Full-repo downloads over-fetch badly for repos that ship many variants (the
  SeedVR2 repo is 60 GB but a render needs ~7 GB), so the catalog carries an
  `include` column of globs for those cases, and pre-fetching stays optional
  since a toolchain fetches only what it needs on first use.
- Cache detection looks for a file over 1 MB under the repo directory rather than
  trusting the directory to exist: interrupted or listing-only fetches leave a
  4 KB metadata-only directory that would otherwise read as cached. It uses
  `find -print -quit` instead of piping into `head`, because a pipe would SIGPIPE
  `find` under `pipefail` - the same trap this repo hit with `grep -q` before.
- My own test hit that exact trap: assertions written as `run ... | grep -q`
  reported false negatives. Fixed by capturing output first and matching with a
  here-string, and noted in the test so it does not come back.
- Verified against real caches: detection agrees with `du` (including a
  metadata-only repo correctly reported missing), and a real 973 MB pull into an
  empty scratch cache produced a valid layout that the listing then reported as
  cached. `boogu` shows as missing on this machine, which matches its empty cache
  directory - the listing is telling the truth.

## 2026-07-30: Bundled CLI and install-on-PATH from the app

The app resolved `fxlla` from `~/.local/bin`, `/usr/local/bin`, or
`/opt/homebrew/bin` and bundled nothing, so a `.dmg`-only install had a front end
with no CLI behind it - and no way for a UI action to link a CLI that did not
exist. Fixed both halves: `app/build.sh` copies the CLI tree into
`fxlla.app/Contents/Resources/cli`, and the panel gained an **Install the fxlla
command** action.

- The maintainer's call (2026-07-30) was that putting the CLI on PATH is a
  user-triggered UI action, the VS Code / Docker Desktop pattern, not a silent
  installer step. It writes outside the app bundle, so the user should ask for it.
- It links into `~/.local/bin`, which is user-writable, so there is no admin
  prompt at all (the app already has `osascriptAdmin` for the GPU limit; this
  does not need it).
- `bin/fxlla` already resolved symlinks before computing `REPO_ROOT`, so a link
  into the bundle Just Works: the bundled CLI uses the bundle's own lib/, config/,
  and python modules. Verified by running it through a scratch symlink.
- Resolution order puts an installed CLI ahead of the bundled one, so a git
  checkout stays in charge during development.
- Secret hazard, handled deliberately: `config/` is copied file by file
  (`models.conf` and `config.env.example` only) because `config/config.env` is
  git-ignored and holds the user's Apple app-specific password and HF/Civitai
  tokens. Copying `config/` wholesale would have shipped them inside a
  distributable `.dmg`. build.sh also hard-fails if that file ever appears in the
  bundle, and a CI step re-checks it.
- Self-review caught a destructive bug before it shipped: the first version
  replaced whatever sat at `~/.local/bin/fxlla`. On this machine that link points
  at the git checkout, so clicking Install would have silently repointed it at the
  app bundle and broken live editing. It now reports and leaves alone anything it
  did not create - a real file, or a symlink pointing elsewhere.
- Dropped an "is it on PATH?" check I had written: a GUI app inherits a minimal
  PATH that does not reflect the user's shell, so it would have been wrong more
  often than right. The UI states where it linked instead.
- Verified: strict-concurrency Swift 6 build clean, the bundle contains the CLI
  tree, an audit for the 5 real secret values from the local config.env found
  none in the bundle, and the bundled CLI runs both directly and via symlink.
- Review round (CodeRabbit, 5 findings, 4 taken): the important one was that a
  bundle running from a mounted `.dmg` or from Gatekeeper's App Translocation sits
  at a temporary path, so linking into it would leave a dangling `fxlla` on PATH
  once the image is ejected. The install now refuses from those locations and asks
  the user to move the app to Applications first. Also added: the CI check runs the
  bundled CLI *through a symlink* as well (the direct call would not catch a
  regression in symlink-based REPO_ROOT resolution), a bash re-exec guard on both
  app entrypoints, and plainer wording instead of an arrow in the UI note. The
  fifth finding claimed the es/pt strings violate the English-only rule; declined,
  because AGENTS.md explicitly allows app localization. Clarified that section to
  name L.swift as the resource layer so the rule cannot be read as a conflict.

## 2026-07-30: Background media jobs

Video renders run for minutes, which is far too long for an MCP tool call to
block on. `--async` on any generator now returns a job id immediately and the
caller polls (`fxlla media jobs|job|cancel`, or `media_job_status` /
`list_media_jobs` / `cancel_media_job` over MCP).

- No daemon and no dependencies (`media/jobs.py`): submitting writes a JSON
  record under `<media out>/jobs` and spawns a detached worker with
  `start_new_session=True`. The worker owns the record for the rest of the job.
- Jobs are serialized with an exclusive `flock`: renders share unified memory
  with the gateway's resident models (which `free_gpu` already unloads), so two
  concurrent renders would thrash or OOM the machine. A job waiting on the lock
  reports `queued`, which is a normal state, not a failure.
- The worker re-invokes `generate.py` with the original argv minus `--async`
  (captured from `sys.argv`), so a background job runs exactly the same code path
  as the direct call - no duplicated flag plumbing, nothing to drift.
- `start_new_session` also makes the worker a process-group leader, so `cancel`
  can `killpg` the worker *and* the generator it spawned. Killing only the worker
  would have left the real renderer running.
- Stale jobs: a worker killed with -9 or lost to a reboot would sit in `running`
  forever, so reads reap any active job whose pid is gone and mark it failed.
  Records are written atomically (tmp + os.replace) so a concurrent `jobs` listing
  never sees a half-written file.
- `--async` cannot use `dest="async"` (Python keyword); job ids from MCP are
  regex-validated before any path join.
- Self-review additions: `jobs --prune` (the jobs dir grew unboundedly, one JSON
  plus one log per job forever) and `describe` showing the *last* line of a
  captured traceback rather than the first 60 characters of it.
- Verified end to end with real renders: two submissions serialized correctly
  (one running, one queued), both produced real PNGs (~190 KB), cancelling the
  queued one killed its worker without disturbing the running job, and the MCP
  path returned a job id instantly and then polled it to completion.

Also recorded a maintainer decision for the still-open CLI-on-PATH item: it
belongs in the app UI as an explicit user action ("Install the fxlla command"),
the VS Code / Docker Desktop pattern, not as a silent installer step.

## 2026-07-29: Code graph Phase B (multi-language via tree-sitter)

Extended the code graph beyond Python. `graph/tsextract.py` parses JavaScript,
TypeScript/TSX, Go, Rust, Java, C/C++, and Ruby with tree-sitter and returns the
same `(defs, refs)` shape as the ast visitor, so both feed the same KuzuDB
Def/Ref/CALLS model and queries resolve by name across languages.

- Config-driven walker: a small per-language table maps def node types to a kind,
  marks class-like scopes (so nested functions become methods), and names the
  call node types plus the field holding the callee. A generic tree walk builds
  qualname from the scope stack and caller from the enclosing def, mirroring the
  ast visitor. Grammars come from `tree-sitter-language-pack` (precompiled).
- codegraph.py dispatches by extension: `.py` keeps the stdlib `ast` path (best
  qualname fidelity, no dependency), everything else goes through tsextract. The
  `import tree_sitter_language_pack` is deferred inside `extract()`, so codegraph
  and its unit tests still import under a plain system python; the tree-sitter
  tests self-skip there and run for real in CI under uv.
- `_graph_python` gained `--with tree-sitter --with tree-sitter-language-pack`
  next to the pinned `kuzu==0.11.3`; CI runs the extension test step with all
  three.
- Verified end to end: indexed a mixed JS/Go/Python/Rust directory; `def helper`
  spanned all four files and `callers helper` resolved across languages. New
  tests cover pure tsextract (JS, Go) and the cross-language graph (JS + Python).
- Scope: a curated language set with a per-language config; adding a language is
  a new config entry. tree-sitter is error-tolerant (no parse exceptions), so a
  malformed file yields partial results rather than being skipped like a Python
  SyntaxError.
- Self-review caught two robustness bugs (CodeRabbit was usage-limited, no paid
  account, so I reviewed adversarially instead): (1) two defs sharing
  file::line::qualname (e.g. a same-line redefinition) produced a duplicate
  Def.id and a Kuzu primary-key violation that aborted the whole `index` run -
  fixed by deduping defs by id per file; (2) `_gather` walked dependency/build
  dirs now that JS/Go/etc. are indexed - added `_SKIP_DIRS` (node_modules,
  vendor, target, dist, build, .venv, ...) pruned in-place during os.walk. Both
  have regression tests.

## 2026-07-29: RAG MCP through the vector index

Follow-up to the RAG vector index: the MCP `rag_search` still ran the brute-force
scan because the server spawned system python3. Fixed with the same trick used
for the code graph MCP: `fxlla kb mcp` now launches `rag_mcp.py` under the kb
python (`uv run --with sqlite-vec` when `FXLLA_KB_INDEX` is on), so the server's
`sys.executable` re-invocation of `core.py` inherits sqlite-vec. `rag_mcp.py` is
unchanged. `wire-opencode` registers the resolved interpreter as the command and
forwards `FXLLA_STORE`/`FXLLA_EMBED_PORT`/`FXLLA_KB_INDEX`, so re-running it after
toggling the index refreshes the registration. A CI shell test
(`tests/test_wire.sh`) pins the command/env selection for both states; the MCP
path was verified end to end against the live embedder. `core.py` still falls
back to the scan when the extension will not load, so nothing breaks with the
index off.

## 2026-07-29: Code graph on KuzuDB (Phase A)

Second step of the RAG/KuzuDB priority. Swapped the code graph's flat SQLite
store for an embedded KuzuDB graph, keeping the `ast` extraction (`_Visitor`),
the CLI, and the MCP tools identical.

- Model: `Def` and `Ref` node tables mirror the old `defs`/`refs` rows, plus a
  derived `CALLS` relationship from the definition that encloses a call to every
  definition sharing the called name (name-approximate, since Python calls are
  not statically resolved). CALLS is rebuilt in full after each `index` with a
  single MERGE join, so the per-file incremental replace of Def/Ref is untouched
  and edges never duplicate.
- `impact` is now one Cypher variable-length path
  (`MATCH p=(a:Def)-[:CALLS*1..N]->(t:Def) ... min(length(p))`), replacing the
  hand-rolled Python BFS. `unused` uses a `NOT EXISTS { }` subquery. `refs` and
  `callers` stay name-based over `Ref` nodes, so they still resolve calls to
  symbols with no project definition (verified against `serialize_float32`).
- KuzuDB is not stdlib, so `import kuzu` is deferred inside `_conn()`: the module
  still imports under system python for the `_Visitor`/MCP unit tests (CI runs
  there), while `fxlla graph` runs the backend under `uv run --with kuzu`
  (`FXLLA_GRAPH_PYTHON` overrides). The MCP server runs under the same
  interpreter so its `sys.executable` re-invocation of codegraph.py inherits
  kuzu. No stdlib fallback: KuzuDB is the sole engine now, matching the plan.
- Variable-length path bounds cannot be parameterized, so the depth (an int from
  argparse, clamped 1..50) is formatted into the query string.
- Verified end to end: indexed the repo (290 defs, 1418 refs), def/callers/
  impact/refs/unused/stats/ls/rm, the JSON-RPC MCP path, and re-index idempotency.
- Dependency risk (raised in review): KuzuDB upstream was archived after the
  October 2025 Apple acquisition; 0.11.3 is the final release with no further
  maintenance. Decision: pin `kuzu==0.11.3` in every `uv run --with` invocation
  (CLI, CI, wire-opencode) for reproducibility, keep `FXLLA_GRAPH_PYTHON` as the
  escape hatch, and re-evaluate a maintained fork/successor if the pin ever fails
  to install. It ships pre-bundled algo/fts/json/vector extensions, which also
  helps Phase B.
- CI now runs `graph.test_graph` (and `rag.test_rag`) a second time under
  `uv run --with kuzu==0.11.3 --with sqlite-vec`, so the extension-backed tests
  that self-skip under system python actually execute in CI instead of skipping.
- Known scaling limit (raised in review): `_rebuild_calls` joins on
  `qualname`/`file`/`name`, none primary-key-indexed in Kuzu, and rebuilds all
  CALLS edges on every index. Fine at the tested scale; profile before running
  it over a large monorepo and narrow the join surface if it becomes a
  bottleneck (tracked in docs/roadmap-remaining.md).

## 2026-07-29: RAG vector index (sqlite-vec), opt-in

First step of the RAG/KuzuDB priority. `fxlla kb search` scored every chunk with
a python cosine loop (O(n)); fine for small bases, wasteful past a few thousand
chunks.

- `FXLLA_KB_INDEX=1` opts into a `sqlite-vec` KNN index. A per-kb `vec0` virtual
  table (`vec_<kb>`, cosine distance) is rebuilt on demand from the `chunks`
  table whenever its row count drifts, so `cmd_add` stays untouched and the
  index never re-embeds anything - it only repacks stored vectors.
- The chunks table remains the single source of truth. The index is derived
  state; deleting or corrupting it only forces a rebuild on the next search.
- sqlite-vec is not in the system python and macOS system python often forbids
  `load_extension`. So with the index on, `bin/fxlla kb` runs `rag/core.py`
  under `uv run --with sqlite-vec --no-project python` (uv is a hard dep, same
  pattern as `pull --downloader hf`). `FXLLA_KB_PYTHON` overrides the interpreter.
- Fully graceful: `_load_vec` returns None when the index is off or the extension
  will not load, and search falls back to the original brute-force scan. Scores
  are identical (cosine distance = 1 - cosine similarity), verified end to end
  against the live nomic embedder.
- Gotcha found in testing: dropping a `vec0` table needs the extension loaded on
  that connection, so `cmd_rm` loads it best-effort and guards the DROP.
- The RAG MCP server still uses the scan (it spawns system python3); routing it
  through the index is left in the roadmap.

## 2026-07-28: Media and gateway memory coordination

Closed the one robustness gap from docs/roadmap-remaining.md (Tier 1). Media
generation and the gateway's resident LLMs share unified memory; a heavy render
next to a large model could exceed the wired limit and OOM the machine.

- Gateway gained `POST /admin/unload` (Manager.unload_all): terminates every
  resident backend and clears the registry, keeps serving, reloads on demand.
- The coordination lives in `media/generate.py` (free_gpu), not the bash CLI, so
  MCP tool calls get it too - they go through generate.py, not `fxlla media`.
  Each generator calls it before the heavy subprocess.
- Best-effort: no gateway (connection refused) means nothing to free, and the
  request failure is swallowed. Opt out with `--keep-models` /
  `FXLLA_MEDIA_KEEP_MODELS` for a small job that fits alongside the model.
- generate.py reaches the gateway via FXLLA_HOST/FXLLA_PORT, added to the media
  env that cmd_media exports. Verified end to end against a live gateway:
  /admin/unload returns the freed aliases and /health then shows none resident.

## 2026-07-28: Local text to speech (voice)

Added `fxlla media voice` and the MCP `generate_speech` tool, completing the
media trio (image, video, speech). Reversed the earlier "not wireable" verdict.

- Earlier investigation concluded there was no callable text->speech path,
  because the best engine (Chatterbox via mlx-audio) was only importable inside
  one private virtualenv. The unlock: mlx-audio can live in the same
  environment that hosts ltx-2-mlx. So voice follows the video pattern: a
  configurable interpreter path (`FXLLA_VOICE_PYTHON`) rather than a CLI.
- `media/voice_backend.py` runs under that interpreter (imports mlx_audio,
  loads Chatterbox, writes a 24 kHz mono WAV). fxlla's own python never imports
  mlx_audio; `generate.py` shells out to it. The backend is not exercised in CI
  (needs mlx-audio); the command-building and WAV validation are.
- Chatterbox ships no `conds.safetensors`, so a reference voice wav is
  mandatory; it sets the timbre. Discovered the `generate` signature by
  introspection (`text`, `lang_code`, `ref_audio`, `speed`, `exaggeration`,
  `cfg_weight`).
- Validated end to end through `fxlla media voice`: a real ~3 s 24 kHz WAV,
  peak ~73 percent full scale and ~69 percent voiced windows (a real utterance,
  not silence). Verified the audio signal, not just the exit code.
- `cmd_media` now exports the media env once (a single name list) so the direct
  call, the MCP server, and the opencode registration forward the same set,
  instead of repeating a growing var list three times.

## 2026-07-28: Signing and notarization hardening

Shared `app/sign-lib.sh` (identity default, `require_identity`, `verify_signed`)
used by `build.sh` and `package-dmg.sh`. New `package-dmg.sh --check` validates
the signing environment without building; the notarize path now validates the
staple and runs a Gatekeeper assessment. Both notarytool credential routes are
documented (app-specific password and App Store Connect API key); credentials
live only in the keychain.

Bug caught while testing: `codesign -d ... | grep -q '...runtime'` reported a
false negative on a correctly signed app. Under `set -o pipefail`, `grep -q`
exits on first match and SIGPIPEs `codesign`, so the pipeline returns non-zero
despite the match. Fixed by capturing the output to a variable and matching
with `case`. The same shape was latent in `require_identity`. General rule for
these scripts: do not pipe a long-running producer into `grep -q` under
pipefail; capture first.

Notarization needs the maintainer's Apple credentials (an active Developer
Program membership plus either an app-specific password or an App Store Connect
API key), stored once as a notarytool keychain profile.

Verified end to end: with the profile in place, `app/package-dmg.sh --notarize`
built, signed, submitted, and got `Accepted` from the notary service, then
stapled and validated. The resulting `.dmg` passes `stapler validate` and a
`spctl` Gatekeeper assessment offline (the ticket is embedded). Whole run ~34 s.
Credentials live only in the keychain and in the git-ignored `config/config.env`.

## 2026-07-28: Media generation (image and video)

Phase 4 delivered on the CLI and MCP. `fxlla media image|video` plus an MCP
server (`generate_image`, `generate_video`).

- Images: mflux-cv (community build, `mflux-cv 0.18.29`), one CLI per model
  family. The wrapper maps a friendly name (z-image-turbo default, boogu,
  flux2-klein, qwen, krea2, schnell, dev) to the right binary. The earlier VAE
  error that parked this work was a stale vanilla-mflux build, not a wrapper
  bug; with mflux-cv it resolves cleanly. Validated with a real 512x512 render.
- Video: ltx-2-mlx (LTX-2.3). Contract gotcha: `generate` requires exactly one
  stage flag (`--distilled` is the fast default) and a mandatory `--frame-rate`
  (trained at 24). The binary usually lives in a project venv, so the path is
  configurable via `FXLLA_VIDEO_BIN`. Validated with a real 25-frame 512x320
  clip in 27s; the MP4 even carries an AAC audio track.
- Both validate the produced file (PNG magic / MP4 ftyp box plus a size floor),
  because these tools can exit 0 while writing nothing useful ("verify the
  pixels, not the exit code").
- Module named `generate.py` (not `media.py`): a file sharing its parent
  directory's name shadows it as a namespace package on import, the same
  collision that forced the `<domain>_mcp.py` naming.
- Voice/audio (a separate local toolchain) is out of scope here and tracked as
  a follow-up.

## 2026-07-28: Config precedence, completions, and model availability

Three CLI items batched into one review-sized change (all touch command
dispatch and model enumeration, so they share a review).

Model availability (#11), the tool side of the availability-and-consent design:
`fxlla ls --json` and `fxlla avail <alias>` expose `{cached, known, engine,
repo, size}` (catalog size for models not yet pulled). `fxlla on` keeps its
fail-fast when a model is not cached and gains an opt-in `--pull`. The agent or
skill still owns the offer-and-consent flow; the CLI just gives it a
machine-readable signal. A partial download (a model dir without the `.source`
completion marker) is deliberately reported as not cached.

- Config precedence (#15): `config.env` used plain assignments, so sourcing it
  clobbered any `FXLLA_*` value exported in the shell, reversing the documented
  order (environment > config.env > defaults). Fix in `lib/core.sh`: snapshot
  the exported config vars (`export -p` filtered to `FXLLA_*`/`HF_TOKEN`, which
  also sidesteps re-applying readonly system vars), source the file, then
  re-apply the snapshot so the environment wins. A `tests/test_config.sh`
  harness drives `fxlla config` across the three tiers and is run in CI.
- Shell completions (#10): `fxlla completions <bash|zsh>` prints a script backed
  by a hidden `fxlla __complete <what>` helper, keeping candidate lists in the
  CLI (single source of truth). Gotcha: `mapfile` is bash 4+, and macOS ships
  bash 3.2, so the bash script uses the classic word-splitting `COMPREPLY=(...)`
  form (safe here since candidates never contain spaces). Verified against the
  real `bash 3.2.57`. Tested in CI (`tests/test_completions.sh`).

## 2026-07-28: Passive gateway metrics

Closed the passive side of the stats work flagged in the earlier entry. The
gateway now measures real proxied traffic instead of relying on a synthetic
probe. Design:

- A separate `gateway/metrics.py` holds the pure logic (SSE token counting,
  first-token timing, usage parsing, sample append) so it is unit-tested
  without sockets. The server feeds streamed chunks to a `StreamMetrics` and
  appends one sample per completed completion request.
- Token count approximates one token per streamed delta, matching the existing
  probe. When a server emits a trailing `usage` chunk
  (`stream_options.include_usage`), its exact `completion_tokens` takes over.
  Non-streamed responses read `usage.completion_tokens` directly.
- Recording is best-effort and fully wrapped: a metrics failure logs and is
  swallowed so it can never affect the proxied response.
- Samples reuse the CLI probe's schema (`ts, model, engine, ram_mb, ttft_ms,
  tps`) plus a `source` marker, so the menu bar app renders them unchanged.
  `serve` pins `FXLLA_STATS_FILE` to the CLI's path so both writers agree.
- `fxlla stats` now reads these passive samples when the gateway is up (and no
  single-model server is), rather than probing.

## 2026-07-28: Metrics sourcing and fxlla stats

Shipped `fxlla stats` (RAM from server RSS, TTFT and tok/s from a small probe,
appended to a rolling stats.jsonl time-series). Validated on M5 Max:
qwen3-coder (30B-A3B 4bit) about 115 tok/s and 90 ms warm TTFT, 16.7 GB RAM.

Finding on passive metrics: mlx_lm.server does not log per-request tok/s (only
prompt-processing progress and the HTTP line), so passive decode-rate metrics
for MLX cannot come from log parsing. They will come from the multi-model
gateway, which sees the real token stream when it proxies. llama-server exposes
/metrics (Prometheus) and can be read passively. So the passive side of the
stats work is partly a dependency of the gateway.

Also added `fxlla doctor` (environment diagnostics: deps, PATH, store, GPU
memory, server health).

## 2026-07-28: Model availability and consent

Decided the responsibility split for downloading a model that is not cached.
The tool exposes availability in machine-readable form and makes downloads
explicit (no silent large pulls; `on` fails fast or opts in with `--pull`).
The skill or agent owns the offer and consent flow, since only it has the
conversation context to ask. The system prompt sets the thresholds. See
ROADMAP "Design principle: model availability and consent". Tracked as an
issue for the machine-readable status and the offer flow.

## 2026-07-28: Project start

Goal: run the best open-weights models locally on a MacBook Pro M5 Max
(128 GB, 40 GPU cores) for development and red team work, usable from opencode
without losing the Claude models in Claude Code.

### Decisions

- MLX as the primary engine (more efficient than llama.cpp on Metal), and
  GGUF via llama.cpp as the second engine only when a model has no MLX build
  or a specific imatrix quant is wanted. GGUF is not loadable by MLX except in
  limited cases; they are separate ecosystems.
- opencode as the client for local models; Claude Code left untouched (pointing
  it at a local base URL would replace Claude, not add to it).
- Models cached on an external disk (`/Volumes/1TB-WD750-1`, APFS, 931 GB
  free), not the internal SSD.
- Bandwidth-capped downloads via `aria2c --max-overall-download-limit`
  (LM Studio, ollama, and `hf` do not do this natively). 25 Mbps is about
  3.1 MB/s.

### Technical findings

- The file list for a repository comes from the HF tree API
  (`/api/models/<repo>/tree/main?recursive=true`), paginated through the `Link`
  header. An `aria2c` input file is built with `dir=` and `out=` per file.
- macOS ships bash 3.2: an empty array under `set -u` fails
  (`${arr[@]+"${arr[@]}"}`), and awk with direct interpolation is fragile.
  Scripts re-exec under bash when started by another shell such as zsh.
- macOS caps GPU RAM at about 75 percent (98 GB of 128). It is raised with
  `sysctl iogpu.wired_limit_mb`. Recommended 122880 (leaves 8 GB for the OS).
  Reverts on reboot.
- `mlx_lm.server` reports the model id as `default_model` (not the path); the
  opencode `local` provider uses that stable id.
- Signing available: `Developer ID Application` present in the keychain, plus
  Xcode 16 and Swift 6.3, so notarization is viable.

### End-to-end validation (model `tiny`, Qwen2.5-Coder-0.5B-4bit)

- `pull`: 265 MB in 1:44, about 20 Mbit effective (cap working).
- `on`: serves on `:8080`, chat/completions responds, `usage` returns tokens.
- `status` shows the idle timer; `off` cleans the process and state.

### Vision captured (see ROADMAP)

Signed menu bar app with live metrics; per-project RAG and a code graph
(KuzuDB) exposed as MCP servers; image and video skills (mflux) through tool
calling. Position: be the best local provider of models and tools, and leave
orchestration (skills, workflows) to opencode and Claude Code.

### Open

Phase order to prioritize; vector store (sqlite-vec vs LanceDB); confirm
KuzuDB for the graph.
