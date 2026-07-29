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
2. If it is cached, use it. Starting is fast.
3. If it is not cached, do not pull silently. Tell the user the size and the
   rough time at the current bandwidth cap, and offer any cheaper cached
   alternative that would do.
4. Only after the user agrees, download: `fxlla pull <alias>` (or
   `fxlla on <alias> --pull` to pull and start). Downloads are bandwidth-capped
   and resumable.

## Thresholds

- Small models (well under a few GB) can be pulled with a brief heads-up.
- For anything large, always confirm first. Prefer a cached model when it meets
  the need.

## Notes

- The same rule covers media weights and the embedding model; if a media backend
  is not ready, `fxlla doctor` shows what is missing.
- Gated repositories need `HF_TOKEN` set in the user's config; do not ask for or
  handle secrets yourself.
