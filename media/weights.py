#!/usr/bin/env python3
"""Consent for the weights a render downloads on its own.

The toolchains fetch their weights from Hugging Face on first use - tens of
gigabytes, mid-render, with nothing asked. That is the one transfer nobody
initiates on purpose, and it is reachable three ways (the CLI, a background job,
and an MCP tool call) which all converge on generate.py, so the check lives here
rather than in the shell wrapper.

Refusing is the point. There is no prompt: whoever is driving should present the
size and let the user decide, then pass --yes or pre-fetch with
`fxlla pull media:<alias>`. FXLLA_ASSUME_YES=1 authorizes without the flag.

Standard library only.
"""
import os

CATALOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "media.conf")

# Which catalog alias each generator needs. Image models are named after their
# catalog entry, so those resolve directly from the requested model.
FIXED_ALIAS = {"video": "ltx", "voice": "chatterbox",
               "edit": "qwen-edit", "upscale": "seedvr2"}


def authorized():
    return os.environ.get("FXLLA_ASSUME_YES", "") not in ("", "0", "false")


def _row(alias):
    """(repos, size) for a catalog alias, or None when it is not listed."""
    try:
        with open(CATALOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[0] == alias:
                    return [r.strip() for r in parts[1].split(",") if r.strip()], parts[2]
    except OSError:
        return None
    return None


def _cache_home():
    return os.environ.get("FXLLA_MEDIA_HF_HOME") or os.path.expanduser(
        "~/.cache/huggingface")


def _cached(repo):
    """Mirrors the shell check: a repo directory holding real weight-sized bytes.

    A directory alone is not enough - an interrupted or listing-only fetch leaves
    a few kilobytes of metadata behind.
    """
    directory = os.path.join(_cache_home(), "hub", "models--" + repo.replace("/", "--"))
    if not os.path.isdir(directory):
        return False
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                if os.path.getsize(os.path.join(root, name)) > 1024 * 1024:
                    return True
            except OSError:
                continue
    return False


def missing_for(kind, model=None, extras=()):
    """Repos this generator needs that are not cached, plus the catalog size.

    `extras` are aliases a specific invocation adds on top of the model's own:
    a weight requirement can come from an OPTION rather than from the model, and
    --pid-decode is the first - it pulls a decoder checkpoint and a gated caption
    encoder for any of ten models. Resolving only the model's alias would let the
    gate pass on weights that are already cached and then fetch 8 GB mid-render,
    which is exactly the failure this module exists to prevent.
    """
    aliases = [a for a in [FIXED_ALIAS.get(kind) or model] + list(extras) if a]
    if not aliases:
        return [], None
    missing, sizes, seen = [], [], set()
    for alias in aliases:
        row = _row(alias)
        if row is None:
            continue  # not in the catalog: no opinion rather than a wrong one
        repos, size = row
        gap = [r for r in repos if r not in seen and not _cached(r)]
        seen.update(repos)
        if gap:
            missing += gap
            sizes.append("%s for %s" % (size or "an unknown amount", alias))
    return missing, " plus ".join(sizes) or None


def require(kind, model=None, extras=()):
    """Raise unless the weights are present or the transfer was authorized."""
    if authorized():
        return
    missing, size = missing_for(kind, model, extras)
    if not missing:
        return
    aliases = [a for a in [FIXED_ALIAS.get(kind) or model] + list(extras) if a]
    raise RuntimeError(
        "this render would first download about %s of weights (%s), and nothing "
        "here asked a human. Nothing was downloaded. Present the size to the "
        "user, then either pre-fetch with %s or pass --yes."
        % (size or "an unknown amount", ", ".join(missing),
           " and ".join("'fxlla pull media:%s --yes'" % a for a in aliases)))
