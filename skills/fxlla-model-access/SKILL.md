---
name: fxlla-model-access
description: Check model availability and get consent before downloading weights with fxlla. Use before starting a local model or a media backend so a large download never happens by surprise; prefer cached models and confirm size and time first.
---

# Check availability and get consent before downloading

fxlla never surprises the user with a large download. Whether a model is cached
or must be fetched is checkable, and pulling weights is an explicit action. Own
the offer-and-consent flow.

## The flow

1. Check first. `fxlla avail <alias>` returns JSON: `cached`, `known`,
   `engine`, `size_mb` (real size when cached, else null), and `catalog_size`
   (the download estimate). `fxlla ls --json` lists what is already cached.
   `catalog_size` is a hand-written estimate, not a measurement, and it can
   run UNDER: a vision repo ships one or more multimodal projectors alongside
   the weights and the pull takes every one of them, which on one 22GB row is
   another 1.6GB. Quote it as an estimate rather than a figure, and say "about"
   - the number you are relaying to a user is the one they consent against.
2. If it is cached, use it. Starting is fast.
3. If it is not cached, do not pull silently. Tell the user the size and the
   rough time at the current bandwidth cap, and offer any cheaper cached
   alternative that would do.
4. Only after the user agrees, download: `fxlla pull <alias> --yes` (or
   `fxlla on <alias> --pull --yes`). Downloads are bandwidth-capped and resumable.

`--yes` is required, not decoration. You have no terminal, so a transfer over the
threshold refuses without it. Treat that refusal as the flow working: relay the
size, get agreement, then re-run with `--yes`. Do not set `FXLLA_ASSUME_YES` to
get around the question. The same applies to a render whose weights are missing:
it refuses and names the `fxlla pull media:<alias>` command to fetch them.

## Thresholds

- Small models (well under a few GB) can be pulled with a brief heads-up.
- For anything large, always confirm first. Prefer a cached model when it meets
  the need.

## Notes

- The same rule covers media weights and the embedding model; if a media backend
  is not ready, `fxlla doctor` shows what is missing.
- Gated repositories need `HF_TOKEN` set in the user's config; do not ask for or
  handle secrets yourself.
