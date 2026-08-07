# Local benchmarks

Numbers measured on this machine, by `fxlla eval`. They rank models against
each other **here**; a number from another Mac ranks nothing against them.

- Host: M5 Max, 128 GB unified memory
- Harness: v5, tasks fingerprint `2dc531994d76`
- Run with `FXLLA_EVAL_ANSWER_FLOOR=8192` (see "Why the floor" below)
- Date: 2026-08-07

Comparable runs need the same fingerprint **and** the same harness version.
The fingerprint above is not the default one: raising the answer floor changes
the rendered task set, so it changes the fingerprint. That is expected.

## Gemma 4: 26B-A4B against the 12B

| model | engine | tok/s (median) | tok/s range | prefill tok/s | TTFT ms | cold s | RSS GB | tokens | wall s |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gemma-4-26b | mlx | 119.5 | 97.3 - 134.6 | 2657 | 192 | 3.99 | 13.7 | 48,998 | 455.8 |
| gemma-4-26b-qat | gguf | 106.9 | 61.2 - 122.0 | 1921 | 102 | 4.44 | 29.4 | 65,818 | 680.1 |
| gemma-4-12b-qat | gguf | 53.1 | 34.7 - 56.1 | 1008 | 340 | 7.34 | 23.3 | 42,012 | 864.6 |

Cold is `load_s + first_s`, never `load_s` alone: llama-server loads weights
before answering `/health`, mlx_lm.server loads them on the first request, so
the split between those two columns means different things per engine.

| model | instructions | tools | candor | code | context |
| --- | --- | --- | --- | --- | --- |
| gemma-4-26b | 8/8 | 8/8 | 6/8 | 9/10 | 4/4 |
| gemma-4-26b-qat | 8/8 | 8/8 | 6/8 | 9/10 | 2/4 |
| gemma-4-12b-qat | 8/8 | 8/8 | 7/8 | 10/10 | 3/4 |

## What these say

**The quality columns do not separate these models, and the reason is not that
they are equal.** Nine of the ten failures across all three runs are
`[hit max_tokens]` - the model was still thinking when the budget ended. That
holds at a floor of 8192, four times the default. Exactly one failure in the
whole sweep is a capability failure: `code-rle` on gemma-4-26b, a real
SyntaxError. Read the quality table as "the harness cannot tell these apart",
not as "these are the same model".

That also disposes of the one gap that looks real: gemma-4-26b-qat scoring 2/4
on context. Both of its context failures are `[hit max_tokens]`. It is not
worse at long context; it spent the budget reasoning.

**The two 26B builds are indistinguishable on decode speed.** 119.5 against
106.9 looks like a gap until you read the spreads: 97.3-134.6 against
61.2-122.0, overlapping across most of their range. The harness's own rule -
a gap inside two models' min..max spreads is noise - applies.

**The 26B-A4B is about twice the 12B, and that gap is real.** 34.7-56.1 does
not overlap the MLX build's range at all. A mixture of experts with ~3.9B
active beats a 12B dense, which is the arithmetic working out the way the
architecture says it should.

**Where the two 26B builds do differ:**

- Prefill: 2657 against 1921 tok/s, MLX ahead by 38%. This is what long
  prompts and agent loops pay.
- Memory: 13.7 GB against 29.4 GB of peak RSS. (RSS on unified memory is an
  approximation and the engines account differently, so treat it as a size
  class, not a measurement.)
- Tokens spent: 65,818 against 48,998 for identical scores - 34% more, and
  680 s of wall against 456 s. Both builds reason; the QAT reasons more.
- TTFT: 102 ms against 192 ms, QAT ahead.
- The QAT download carries a 1.19 GB projector, so it can read images. The MLX
  build is text-only.

On this machine, `gemma-4-26b` is the better default: same scores, best
prefill, half the resident memory, a third fewer tokens, and it finishes the
same work in two thirds of the time. Take `gemma-4-26b-qat` when the job needs
eyes or the lowest TTFT.

## Why the floor

The default `FXLLA_EVAL_ANSWER_FLOOR` is 2048. At that budget every failure
these models produce is a truncation, so the sweep measures the budget rather
than the model. 8192 is what these runs used, and it is still not enough - see
the `[hit max_tokens]` count above. Any comparison of reasoning models at the
default floor is measuring `max_tokens`.

## Why v4 numbers are not here

The v4 harness reported 403.1 / 267.9 / 170.2 tok/s for these same three
models. Those numbers were wrong, by different factors each (4.1x, 2.4x, 3.2x),
which means v4 did not even preserve the ranking - it put the MLX build first
on decode where the servers' own clocks put the QAT first.

The cause: the decode clock started at the first **visible** token while the
token count came from the server's `completion_tokens`, which counts the
thinking too. Every token a reasoning model generated before it started
answering inflated the rate. No two servers name that field the same way -
llama.cpp streams `reasoning_content`, mlx_lm streams `reasoning` - and
`gateway/metrics.py` read neither.

How the fix was checked, and where those numbers live: not in the sweep above.
A one-off probe fed one long generation per model through the fixed
`StreamMetrics` and compared it against each server's own clock -
llama.cpp's `timings.predicted_per_second` for the gguf builds, and
`completion_tokens / wall` on a warm, already-loaded model for MLX, which has
no internal counter. That probe read 116.5 and 104.3 tok/s where the servers
said 113.8 and 98.3. Those four figures are from that check, on a single
essay prompt; the table above is a median over the whole task set, which is a
different workload and lands elsewhere in the same range.

The same bug had a second, independent home: `_probe()` in `bin/fxlla`, the
active sampler behind `fxlla stats` on a standalone server, which writes to
the same `stats.jsonl` the menu bar charts. Its symptom was the opposite of an
inflated rate. It counted only visible deltas over a 48-token budget, and a
reasoning model spends all 48 thinking - so it emitted no visible content and
the probe reported a flat `ttft=0 tps=0`. Measured on gemma-4-26b-qat before
and after: `0` became 124.5-124.8 tok/s across three runs.

Speed numbers from v4 and earlier are not comparable with these.

## Measurement conditions

Each model ran alone on a cold, dedicated server. The gateway was up during
both sweeps, which the harness warns about, so it was checked rather than
assumed: `gateway.log` gained no `loading` line during either run, meaning
nothing else put a model on the GPU while the numbers were being taken.

## A reference point from elsewhere

[TurboFieldfare](https://github.com/drumih/turbo-fieldfare) runs this same
model by streaming experts from SSD, and publishes 31-35 tok/s for itself and
76-82 tok/s for mlx-lm on a 24 GB M5 Pro. Different machine, so those numbers
rank nothing against the table above - but the shape is worth recording: it
buys roughly a 7x memory reduction at roughly 2.4x the decode cost. That is an
excellent trade on an 8 GB Mac and the wrong one here, where the memory it
saves is memory this machine has spare.
