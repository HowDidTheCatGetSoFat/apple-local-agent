#!/usr/bin/env python3
"""fxlla multi-model gateway.

One OpenAI-compatible endpoint that fronts many models. It aggregates the
downloaded chat models in /v1/models (embedding models in the same store are
excluded - they cannot chat) and, on each request, routes to the backend for
the requested model, loading it on demand and evicting the least-recently-used
backend when a load would exceed the RAM budget.

Standard library only. Backends are launched through `fxlla _backend <alias>
<port>` so the launch logic stays in the CLI (the single source of truth).

Config via environment:
  FXLLA_HOST, FXLLA_PORT        gateway bind address (default 127.0.0.1:8080)
  FXLLA_STORE                   model store (models under <store>/models)
  FXLLA_BACKEND_PORT_BASE       first internal backend port (default 8100)
  FXLLA_GATEWAY_BUDGET_MB       resident RAM budget (default: ~GPU reservable)
  FXLLA_BIN                     path to the fxlla CLI (default: fxlla on PATH)
  FXLLA_CTX                     CEILING on the context window served to a gguf
                                model (default 32768). Each one gets the window
                                it was trained for, read from its own header,
                                capped by this - so a 7B trained to 128k and a
                                27B trained to 262k stop sharing one number.
                                Raise it to let the big ones use their full
                                window; the cost is RAM, since llama-server
                                allocates the KV cache up front. Reported per
                                model as "context" in /v1/models, from the same
                                reader that starts the backend.
  FXLLA_STATS_FILE              passive metrics time-series (default: the CLI's
                                stats.jsonl under the state dir)
  FXLLA_VISION_ROUTING          0 to stop reading images for models that cannot
                                (default on). Off, an image sent to a text model
                                fails in the backend as it used to. An image is
                                forwarded untouched only to a model the catalog
                                gives role 'vision' AND that has a projector on
                                disk; a model carrying an inherited, undeclared
                                vision tower gets a description like any other.
  FXLLA_VISION_MODEL            which model reads them (default: the first
                                catalog alias with role 'vision' that has a
                                multimodal projector on disk). Naming one here
                                is itself a declaration, so it need only have
                                the projector.
  FXLLA_VISION_MAX_IMAGES       images one request may carry (default 4); each
                                is read separately, so a batch holds the
                                connection for as long as all of them take
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ggufmeta  # noqa: E402  (local module, added to sys.path above)
import metrics  # noqa: E402

# Poll interval while waiting for a backend to answer. Small enough that a fast
# model load is not rounded up to the next whole second.
READY_POLL_INTERVAL = 0.05
HOST = os.environ.get("FXLLA_HOST", "127.0.0.1")
PORT = int(os.environ.get("FXLLA_PORT", "8080"))
STORE = os.environ.get("FXLLA_STORE", "")
MODELS_DIR = os.path.join(STORE, "models")
PORT_BASE = int(os.environ.get("FXLLA_BACKEND_PORT_BASE", "8100"))
FXLLA_BIN = os.environ.get("FXLLA_BIN", "fxlla")


def log(msg):
    sys.stderr.write("[gateway] %s\n" % msg)
    sys.stderr.flush()


def _is_loopback(addr):
    """True for IPv4/IPv6 loopback, including IPv4-mapped IPv6."""
    return addr == "::1" or addr.startswith("127.") or addr.startswith("::ffff:127.")


def dir_size_mb(path):
    try:
        out = subprocess.check_output(["du", "-sk", path], stderr=subprocess.DEVNULL)
        return int(out.split()[0]) // 1024
    except Exception:
        return 0


def default_budget_mb():
    env = os.environ.get("FXLLA_GATEWAY_BUDGET_MB")
    if env:
        return int(env)
    try:
        total_mb = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])) // (1024 * 1024)
    except Exception:
        total_mb = 65536
    try:
        cur = int(subprocess.check_output(["sysctl", "-n", "iogpu.wired_limit_mb"]))
    except Exception:
        cur = 0
    eff = cur if cur > 0 else total_mb * 3 // 4
    return max(eff - 4096, 4096)  # leave a margin for the gateway itself


BUDGET_MB = default_budget_mb()


CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "models.conf")


def _role_aliases(role):
    """Catalog aliases with this role, or None when the catalog cannot be read.

    None and the empty set are different answers - "nothing declares this
    role" versus "there was no way to find out" - and collapsing them made a
    catalog that existed but could not be opened look like a catalog that
    declared nothing, which denies every model instead of failing open."""
    out = set()
    try:
        with open(CATALOG, encoding="utf-8") as fh:
            for line in fh:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5 and not parts[0].startswith("#") and parts[3] == role:
                    out.add(parts[0])
    except OSError:
        return None
    return out


# Sending an image to a text model used to be a crash: mlx_lm.server raises
# "Only 'text' content type is supported" on any non-text part, and a GGUF
# backend without a projector drops it silently. Rather than make every client
# discover which of its models can see, the gateway reads the image with one
# that can and hands the chosen model a description. One request in, one answer
# out, two models used - the capability lives behind the endpoint instead of in
# whatever is calling it.
VISION_ROUTING = os.environ.get("FXLLA_VISION_ROUTING", "1") not in ("0", "false", "")


def _has_projector(alias):
    """True when a multimodal projector sits next to this model's weights.

    This answers whether llama-server CAN be handed an image: bin/fxlla passes
    --mmproj when it finds one, and without it the image goes nowhere."""
    import glob
    return bool(glob.glob(os.path.join(MODELS_DIR, alias, "mmproj*.gguf")))


def _can_see(alias):
    """True when this model is trusted to read an image itself.

    Two separate questions had been collapsed into one. The projector on disk
    says an image CAN reach the model; the catalog role says its vision was
    meant to be used. Those came apart the first time a model shipped a vision
    tower it had inherited and never tuned - a Qwen3.5 derivative whose own
    author writes that text-only training did not evaluate image understanding.
    Reading the file's existence as a statement about quality was inferring a
    claim nobody had made, so both now have to agree, and the safe answer wins
    by default: an undeclared model gets a description from one chosen for the
    job rather than being trusted with its own untested eyes. Declaring it is a
    deliberate act, and role 'vision' in the catalog is where it is made."""
    declared = _role_aliases("vision")
    if declared is None:
        # Unreadable, not merely absent: a moved catalog and an unreadable one
        # are the same situation from here. There are no declarations to read
        # AND no reader to be found, so demanding a declaration would turn a
        # working vision model into a 502. Fail open to the projector, which
        # is the only evidence left.
        return _has_projector(alias)
    return alias in declared and _has_projector(alias)


def _vision_alias():
    """The model that will do the reading, or None."""
    preferred = os.environ.get("FXLLA_VISION_MODEL")
    if preferred:
        # Validated, not trusted: an override naming a text model would send
        # it an image and surface as a vision failure blaming the wrong thing.
        # Only the projector is required - naming a model here IS the
        # declaration, and it should not have to be in the catalog to be used.
        if not _has_projector(preferred):
            raise RuntimeError(
                "FXLLA_VISION_MODEL is set to %r, which has no multimodal "
                "projector on disk and cannot read an image" % preferred)
        return preferred
    for alias in sorted(_role_aliases("vision") or ()):
        if _has_projector(alias):
            return alias
    return None


def _image_parts(body):
    """Every (message, index) holding an image, in order."""
    found = []
    messages = body.get("messages")
    if not isinstance(messages, list):
        return found
    for message in messages:
        # Every level is checked: this runs on EVERY request, including ones
        # with no image, so anything raising here turns a request that used to
        # be forwarded into a 502 blamed on vision.
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for i, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "image_url":
                found.append((message, i))
    return found


def _asked(body):
    """The text the user sent alongside the image, for relevance.

    Passed to the reader as CONTEXT, never as a claim to check: asked whether
    an expected string was present, a vision model confirmed it and missed a
    whole block of invented text; asked to enumerate, it reported the invention
    at once. So the question it receives always says report, never verify."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    # Latest turn first: an older question in the same conversation would
    # steer the reader at the wrong thing.
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()[:400]
        if not isinstance(content, list):
            continue
        texts = [p["text"] for p in content
                 if isinstance(p, dict) and p.get("type") == "text"
                 and isinstance(p.get("text"), str)]
        if texts:
            return " ".join(texts).strip()[:400]
    return ""


# Descriptions already produced, keyed by the image bytes. An OpenAI client
# resends the whole conversation every turn, so without this a picture sent
# once is re-read on every subsequent turn - paying its cost again and, worse,
# describing it differently each time, so the answering model sees the same
# image change its mind. Bounded because the entries are long-lived by design.
_SEEN = {}
_SEEN_ORDER = []
_SEEN_MAX = 64
_SEEN_LOCK = threading.Lock()

# Serial reads with an independent timeout each, so one request could hold a
# connection for images x 600 s. Four covers comparing a couple of renders,
# which is what a caller actually does; more than that is a client bug or a
# way to occupy the gateway indefinitely.
MAX_IMAGES = int(os.environ.get("FXLLA_VISION_MAX_IMAGES", "4"))


def _cache_key(part):
    url = ((part or {}).get("image_url") or {}).get("url")
    if not isinstance(url, str):
        return None
    return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()


def _read_image(part, asked):
    """One image, as text, from the local vision model."""
    alias = _vision_alias()
    if not alias:
        raise RuntimeError(
            "this request carries an image and no catalog model can read one. "
            "Add a model with role 'vision' (see config/models.conf) and pull it")
    try:
        port, model_field = MANAGER.ensure(alias)
    except KeyError:
        raise RuntimeError(
            "the vision model %r is not downloaded. Pull it: fxlla pull %s"
            % (alias, alias))
    question = ("Describe this image for someone who cannot see it. List what "
                "is actually present and quote any text or lettering exactly. "
                "Report only what is there - do not judge whether anything is "
                "correct or expected.")
    if asked:
        question += ("\n\nFor relevance, they were asked: %r. Let that guide "
                     "what you cover, but still report what is present rather "
                     "than confirming anything." % asked)
    body = {"model": model_field, "max_tokens": 700, "messages": [
        {"role": "user", "content": [{"type": "text", "text": question},
                                     dict(part)]}]}
    # Straight to the backend port, not back through this server: a request
    # that re-entered here would translate its own translation.
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % port,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        answer = json.loads(r.read())
    choices = answer.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
    if not text:
        raise RuntimeError("the vision model returned nothing for this image")
    return alias, text


def _read_cached(part, asked):
    """The description for this image, computed once per conversation."""
    key = _cache_key(part)
    if key is not None:
        with _SEEN_LOCK:
            hit = _SEEN.get(key)
        if hit:
            return hit
    result = _read_image(part, asked)
    if key is not None:
        with _SEEN_LOCK:
            if key not in _SEEN:
                _SEEN[key] = result
                _SEEN_ORDER.append(key)
                while len(_SEEN_ORDER) > _SEEN_MAX:
                    _SEEN.pop(_SEEN_ORDER.pop(0), None)
    return result


def add_vision(body, alias):
    """Replace images the chosen model cannot read with a description of them.

    Returns the alias that did the reading, or None when nothing was done -
    no image, routing disabled, or the chosen model is trusted to see for
    itself, in which case the image is passed through untouched because a
    description is strictly lossier than the thing itself.

    The order matters. Whether there is an image at all is answered in memory,
    so a request without one - nearly all of them - never touches the disk."""
    if not VISION_ROUTING:
        return None
    parts = _image_parts(body)
    if not parts:
        return None
    # Checked before the cap: the cap exists because each image costs a serial
    # read, and a model reading them itself pays no such price.
    if _can_see(alias):
        return None
    if len(parts) > MAX_IMAGES:
        raise RuntimeError(
            "this request carries %d images and the limit is %d: each is read "
            "separately, so a large batch holds the connection open for as long "
            "as all of them take. Send fewer, or raise "
            "FXLLA_VISION_MAX_IMAGES." % (len(parts), MAX_IMAGES))
    reader = None
    for message, index in parts:
        reader, text = _read_cached(message["content"][index], _asked(body))
        # Marked as a description on purpose: the model downstream must not
        # answer as though it had seen the image itself.
        message["content"][index] = {
            "type": "text",
            "text": "[an image was attached; %s read it and reports:]\n%s"
                    % (reader, text)}
    return reader


def _embed_identities():
    """(aliases, repos) of catalog entries with role embed. Both are needed:
    a pull by alias names the directory after the alias, a pull by org/repo
    names it after the repo, and .source records which repo either came from."""
    aliases, repos = set(), set()
    try:
        with open(CATALOG, encoding="utf-8") as fh:
            for line in fh:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5 and not parts[0].startswith("#") and parts[3] == "embed":
                    aliases.add(parts[0])
                    repos.add(parts[1])
    except OSError:
        pass
    return aliases, repos


# What llama-server is started with (-c) by cmd_backend: for gguf the SERVED
# window, whatever the weights could do. mlx_lm.server serves the model's own
# window, which config.json declares.
SERVED_GGUF_CTX = int(os.environ.get("FXLLA_CTX", "32768"))


def model_context(alias):
    """The context window a request to this model actually gets, or None when
    it cannot be determined. This feeds opencode's per-model limit: without a
    declared limit its context meter and auto-compaction run on a made-up
    number."""
    d = os.path.join(MODELS_DIR, alias)
    if engine_for(alias) == "gguf":
        # The same reader bin/fxlla starts the backend with, so this reports
        # what is actually served rather than a number that merely used to be
        # passed. Falls back to the cap when the header cannot be read, which
        # is what the backend falls back to as well.
        served, _rope = ggufmeta.serve_plan(d, SERVED_GGUF_CTX)
        return served or SERVED_GGUF_CTX
    try:
        with open(os.path.join(d, "config.json"), encoding="utf-8") as fh:
            value = json.load(fh).get("max_position_embeddings")
        return int(value) if value else None
    except (OSError, ValueError, TypeError):
        return None


def downloaded_models():
    """Map alias -> {size_mb} for CHAT models with a completion marker.

    Embedding models live in the same store but cannot chat: serving one here
    would spawn llama-server without --embeddings on a BERT, and registering
    one in an editor is exactly how a non-chat model ends up as somebody's
    local chat model. They are fxlla kb's business, not the gateway's."""
    out = {}
    if not os.path.isdir(MODELS_DIR):
        return out
    embed_aliases, embed_repos = _embed_identities()
    for name in sorted(os.listdir(MODELS_DIR)):
        d = os.path.join(MODELS_DIR, name)
        if not (os.path.isdir(d) and os.path.exists(os.path.join(d, ".source"))):
            continue
        if name in embed_aliases:
            continue
        try:
            with open(os.path.join(d, ".source"), encoding="utf-8") as fh:
                if fh.read().strip() in embed_repos:
                    continue
        except OSError:
            pass
        out[name] = {"size_mb": dir_size_mb(d)}
    return out


class Backend:
    def __init__(self, alias, port, proc, size_mb, model_field, engine):
        self.alias = alias
        self.port = port
        self.proc = proc
        self.size_mb = size_mb
        self.model_field = model_field  # value to send in the proxied 'model' field
        self.engine = engine            # 'mlx' or 'gguf', resolved once at load
        self.last_used = time.monotonic()


def engine_for(alias):
    """Engine marker for a model: 'gguf' or 'mlx' (the default)."""
    try:
        with open(os.path.join(MODELS_DIR, alias, ".engine")) as f:
            return f.read().strip() or "mlx"
    except Exception:
        return "mlx"


def model_field_from(alias, engine):
    """The 'model' value a backend expects given its engine: the path for MLX,
    the alias for GGUF (llama-server --alias). Never trust the backend's
    enumerated id, since mlx_lm.server lists the whole HF cache in /v1/models."""
    return alias if engine == "gguf" else os.path.join(MODELS_DIR, alias)


def model_field_for(alias):
    """model_field_from with the engine resolved from disk."""
    return model_field_from(alias, engine_for(alias))


def rss_mb(pid):
    """Resident memory of a process in MB via ps, or 0 if unavailable."""
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)],
                                      stderr=subprocess.DEVNULL)
        return int(out.strip()) // 1024
    except Exception:
        return 0


class Manager:
    def __init__(self):
        self.backends = {}          # alias -> Backend
        self.loading = {}           # alias -> (Event, port) for in-flight loads
        self.lock = threading.Lock()
        self.epoch = 0              # bumped by unload_all to cancel in-flight loads

    def _alloc_port(self):
        used = {b.port for b in self.backends.values()}
        used |= {p for (_ev, p) in self.loading.values()}
        p = PORT_BASE
        while p in used:
            p += 1
        return p

    def _resident_mb(self):
        return sum(b.size_mb for b in self.backends.values())

    # Waits for a freshly spawned backend to answer. The poll interval decides the
    # floor on a model switch: a small model answers in about a second, and at a
    # one-second interval the first probe missed it and the loop reported ready at
    # two - a full second of sleep on every load. Watching the process as well
    # turns a backend that dies on start into an immediate failure instead of
    # burning the whole timeout.
    def _wait_ready(self, port, timeout=180, proc=None):
        url = "http://127.0.0.1:%d/v1/models" % port
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    r.read()
                    return True
            except Exception:
                pass
            if proc is not None and proc.poll() is not None:
                return False  # it exited instead of listening
            time.sleep(READY_POLL_INTERVAL)
        return False

    def _evict_one(self):
        if not self.backends:
            return
        victim = min(self.backends.values(), key=lambda b: b.last_used)
        log("evicting %s (LRU, %d MB)" % (victim.alias, victim.size_mb))
        try:
            victim.proc.terminate()
            try:
                victim.proc.wait(timeout=10)
            except Exception:
                victim.proc.kill()
        except Exception:
            pass
        del self.backends[victim.alias]

    def ensure(self, alias):
        """Return (port, model_field) for alias, loading and evicting as needed.

        The lock is held only for the fast registry operations. The slow model
        load runs outside the lock, so requests to already-loaded models never
        block behind another model's startup. Concurrent requests for the same
        not-yet-loaded model wait on a per-model Event instead of the lock."""
        with self.lock:
            b = self.backends.get(alias)
            if b is not None:
                b.last_used = time.monotonic()
                return b.port, b.model_field
            entry = self.loading.get(alias)
            if entry is not None:
                ev = entry[0]
                loader = False
            else:
                models = downloaded_models()
                if alias not in models:
                    raise KeyError(alias)
                size_mb = models[alias]["size_mb"]
                while self.backends and self._resident_mb() + size_mb > BUDGET_MB:
                    self._evict_one()
                port = self._alloc_port()
                ev = threading.Event()
                self.loading[alias] = (ev, port)
                loader = True
                load_epoch = self.epoch

        if not loader:
            # another thread is loading this model; wait for it, do not hold a lock
            ev.wait(timeout=200)
            with self.lock:
                b = self.backends.get(alias)
                if b is None:
                    raise RuntimeError("model '%s' failed to load" % alias)
                b.last_used = time.monotonic()
                return b.port, b.model_field

        # loader path: spawn and wait OUTSIDE the lock
        proc = None
        ready = False
        stale = False
        model_field = None
        try:
            log("loading %s on :%d (%d MB)" % (alias, port, size_mb))
            proc = subprocess.Popen([FXLLA_BIN, "_backend", alias, str(port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ready = self._wait_ready(port, proc=proc)
        finally:
            with self.lock:
                self.loading.pop(alias, None)
                # If unload_all ran while this model was loading, do not register
                # it: the gateway already reported the memory freed.
                stale = ready and self.epoch != load_epoch
                if ready and not stale:
                    engine = engine_for(alias)
                    model_field = model_field_from(alias, engine)
                    self.backends[alias] = Backend(
                        alias, port, proc, size_mb, model_field, engine)
                ev.set()
        if not ready or stale:
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            if stale:
                raise RuntimeError("model '%s' was unloaded during load" % alias)
            raise RuntimeError("backend for %s did not become ready" % alias)
        return port, model_field

    def backend_meta(self, alias):
        """(pid, engine) for a resident backend, or None if it is not loaded."""
        with self.lock:
            b = self.backends.get(alias)
            if b is None or b.proc is None:
                return None
            return b.proc.pid, b.engine

    def status(self):
        with self.lock:
            return [{"alias": b.alias, "port": b.port, "size_mb": b.size_mb,
                     "idle_s": int(time.monotonic() - b.last_used)}
                    for b in self.backends.values()]

    def unload_all(self):
        """Terminate every resident backend and clear the registry, freeing
        their memory. The gateway keeps serving and reloads a model on the next
        request. Returns the aliases that were unloaded, only after the
        processes have actually exited so their memory is released."""
        with self.lock:
            self.epoch += 1          # cancel any in-flight load (see ensure)
            victims = list(self.backends.values())
            freed = list(self.backends.keys())
            self.backends.clear()
        # terminate and wait outside the lock so a slow exit does not block
        # other requests; wait so the memory is gone before we return.
        for b in victims:
            try:
                b.proc.terminate()
            except Exception:
                pass
        for b in victims:
            try:
                b.proc.wait(timeout=10)
            except Exception:
                try:
                    b.proc.kill()
                except Exception:
                    pass
        return freed

    def shutdown(self):
        self.unload_all()


MANAGER = Manager()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # quiet; the gateway logs what it needs

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            models = downloaded_models()
            self._json(200, {"object": "list", "data": [
                {"id": a, "object": "model", "owned_by": "fxlla",
                 "size_mb": m["size_mb"], "context": model_context(a)}
                for a, m in models.items()]})
        elif self.path.rstrip("/") in ("/health", "/v1/health"):
            self._json(200, {"status": "ok", "resident": MANAGER.status(),
                             "budget_mb": BUDGET_MB})
        else:
            self._json(404, {"error": {"message": "not found: %s" % self.path}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""

        # Admin: free resident models so a heavy local job (media generation)
        # has headroom in unified memory. The gateway reloads on demand after.
        # Loopback only, so a non-local bind (FXLLA_HOST=0.0.0.0) cannot let a
        # remote host unload models and deny inference.
        if self.path.rstrip("/") == "/admin/unload":
            if not _is_loopback(self.client_address[0]):
                self._json(403, {"error": {"message": "admin endpoints are loopback-only"}})
                return
            freed = MANAGER.unload_all()
            if freed:
                log("unloaded on request: %s" % ", ".join(freed))
            self._json(200, {"unloaded": freed})
            return

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            self._json(400, {"error": {"message": "invalid JSON body"}})
            return

        alias = body.get("model")
        if not alias:
            self._json(400, {"error": {"message": "missing 'model' field"}})
            return

        # An image the chosen model cannot read is translated before anything
        # else, so a failure here is reported as itself rather than surfacing
        # as the backend's "Only 'text' content type is supported".
        try:
            reader = add_vision(body, alias)
        except Exception as e:
            self._json(502, {"error": {
                "message": "could not read the image in this request: %s" % e,
                "type": "vision_failed"}})
            return

        try:
            port, model_field = MANAGER.ensure(alias)
        except KeyError:
            self._json(404, {"error": {
                "message": "model '%s' is not downloaded. Pull it: fxlla pull %s" % (alias, alias),
                "type": "model_not_found"}})
            return
        except Exception as e:
            self._json(503, {"error": {"message": "could not load '%s': %s" % (alias, e)}})
            return

        # send the model value each backend expects (path for MLX, alias for GGUF)
        if reader:
            log("read the image with %s, answering with %s" % (reader, alias))
        body["model"] = model_field
        payload = json.dumps(body).encode()
        upstream = "http://127.0.0.1:%d%s" % (port, self.path)
        req = urllib.request.Request(upstream, data=payload,
                                     headers={"Content-Type": "application/json"})
        measure = metrics.is_completion_path(self.path)
        start = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            if reader:
                self.send_header("X-Fxlla-Vision", reader)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        except Exception as e:
            self._json(502, {"error": {"message": "backend error: %s" % e}})
            return

        # stream the response back (works for both plain JSON and SSE)
        self.send_response(resp.status)
        ctype = resp.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", ctype)
        # Say that two models were used. Without this, a description that goes
        # wrong looks like the answering model being wrong, and the debugging
        # goes to the wrong place.
        if reader:
            self.send_header("X-Fxlla-Vision", reader)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        streaming = "event-stream" in ctype
        sm = metrics.StreamMetrics(start) if (measure and streaming) else None
        buf = bytearray() if (measure and not streaming) else None
        try:
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                if sm is not None:
                    try:
                        sm.feed(chunk)
                    except Exception:
                        sm = None  # never let metrics break the proxy
                elif buf is not None and len(buf) < 512 * 1024:
                    buf.extend(chunk)
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except Exception:
            pass
        if measure:
            self._record(alias, start, sm, bytes(buf) if buf is not None else None)

    def _record(self, alias, start, sm, body_bytes):
        """Append one passive metrics sample derived from a completed request.

        Best-effort: any failure here is logged and swallowed so telemetry never
        affects the proxied response."""
        try:
            end = time.monotonic()
            if sm is not None:
                ttft_ms, tps, tokens = sm.result(end)
            elif body_bytes is not None:
                # Non-streamed: no first-token signal, so tps is over the whole
                # wall time (includes prompt processing); an approximation.
                tokens = metrics.usage_from_json(body_bytes)
                ttft_ms = None
                tps = round(tokens / (end - start), 1) if tokens and end > start else None
            else:
                return
            if not tokens:
                return  # nothing generated (e.g. an error body): do not record
            meta = MANAGER.backend_meta(alias)
            if meta is None:
                return  # backend evicted between response and record
            pid, engine = meta
            sample = metrics.build_sample(
                time.time(), alias, engine, rss_mb(pid), ttft_ms, tps)
            metrics.append_sample(metrics.stats_file(), sample)
        except Exception as e:
            log("metrics: %s" % e)


def _term(signum, frame):
    raise KeyboardInterrupt()


def main():
    signal.signal(signal.SIGTERM, _term)
    if not STORE or not os.path.isdir(MODELS_DIR):
        log("FXLLA_STORE is unset or has no models dir: %r (start via 'fxlla serve')" % STORE)
        sys.exit(1)
    log("store=%s budget=%d MB backends from :%d" % (STORE, BUDGET_MB, PORT_BASE))
    log("models: %s" % (", ".join(downloaded_models().keys()) or "(none)"))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log("listening on http://%s:%d/v1" % (HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        MANAGER.shutdown()


if __name__ == "__main__":
    main()
