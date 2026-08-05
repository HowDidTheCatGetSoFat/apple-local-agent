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


def cache_roots():
    """Every Hugging Face cache root to search, in order.

    FXLLA_MEDIA_HF_HOME may name several, separated by ':' the way PATH is,
    because weights outgrow a disk: one volume here holds 1.0 TB of them at 98%
    full, so the next model has to land elsewhere without the ones already
    fetched turning into "missing". Each entry is the directory that CONTAINS
    `hub/`, not `hub/` itself. lib/core.sh splits it the same way.
    """
    raw = os.environ.get("FXLLA_MEDIA_HF_HOME") or ""
    roots = [p for p in raw.split(":") if p]
    return roots or [os.path.expanduser("~/.cache/huggingface")]


def write_root():
    """Where a new download goes: the first root named."""
    return cache_roots()[0]


def _has_weights(directory):
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


def _weight_bytes(directory):
    """Bytes of real weight files under a repo directory, 0 when absent.

    Symlinks are skipped rather than followed: a Hugging Face cache keeps the
    bytes in blobs/ and fills snapshots/ with links back to them, so following
    both would count every file twice.
    """
    if not os.path.isdir(directory):
        return 0
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > 1024 * 1024:
                total += size
    return total


def repo_root(repo):
    """The cache root holding this repo, or None.

    A render needs this, not merely "it is cached somewhere": HF_HOME takes ONE
    path, so the toolchain has to be pointed at the root that actually has the
    weights or it will decide they are missing and fetch them again.
    """
    leaf = "hub", "models--" + repo.replace("/", "--")
    for root in cache_roots():
        if _has_weights(os.path.join(root, *leaf)):
            return root
    return None


def _cache_home():
    """Back-compat single answer: the write root."""
    return write_root()


def _cached(repo):
    """Mirrors the shell check: a repo directory holding real weight-sized bytes.

    A directory alone is not enough - an interrupted or listing-only fetch leaves
    a few kilobytes of metadata behind.
    """
    return repo_root(repo) is not None


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


def _repos_for(kind, model=None, extras=()):
    """Every catalog repo this invocation needs, in order, deduplicated."""
    aliases = [a for a in [FIXED_ALIAS.get(kind) or model] + list(extras) if a]
    repos, seen = [], set()
    for alias in aliases:
        row = _row(alias)
        if row is None:
            continue  # not in the catalog: no opinion rather than a wrong one
        for repo in row[0]:
            if repo not in seen:
                seen.add(repo)
                repos.append(repo)
    return repos


def hf_home_for(kind, model=None, extras=(), repos=()):
    """(root, unreachable) - the single HF_HOME to hand this render.

    HF_HOME takes ONE path. With several caches configured, pointing a render
    at the wrong one does not fail loudly: the toolchain decides the weights
    are missing and downloads them again. So pick the root that actually holds
    what this invocation needs.

    When the needed repos are split across roots there is no right answer, only
    a least-wrong one. The tie-break is BYTES, not how many repos each root
    holds: counting repos let a root with two small extras (14 GB of decoder
    and caption encoder) outrank the one holding the 33 GB base model, and
    re-fetching the base model is precisely the cost this exists to avoid.
    Ranking by bytes optimises the thing actually at stake.

    `repos` carries ids that are not catalog aliases - a --lora given as a
    Hugging Face repo id is a real dependency of the render and lives in some
    root, but no catalog row mentions it.

    Whatever the chosen root cannot serve is returned, so the caller can say so
    instead of letting a surprise download explain it.
    """
    roots = cache_roots()
    needed = _repos_for(kind, model, extras) + [r for r in repos if r]
    seen, ordered = set(), []
    for repo in needed:
        if repo not in seen:
            seen.add(repo)
            ordered.append(repo)
    if len(roots) == 1 or not ordered:
        return roots[0], []

    best, best_bytes, best_hits = roots[0], -1, []
    for root in roots:
        total, hits = 0, []
        for repo in ordered:
            size = _weight_bytes(os.path.join(
                root, "hub", "models--" + repo.replace("/", "--")))
            if size:
                total += size
                hits.append(repo)
        if total > best_bytes:
            best, best_bytes, best_hits = root, total, hits
    # Only count a repo as lost if it exists somewhere else. One that is
    # nowhere is simply not downloaded yet, which is a different message.
    unreachable = [r for r in ordered
                   if r not in best_hits and repo_root(r) is not None]
    return best, unreachable


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
