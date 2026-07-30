#!/usr/bin/env python3
"""fxlla multi-model gateway.

One OpenAI-compatible endpoint that fronts many models. It aggregates the
downloaded models in /v1/models and, on each request, routes to the backend for
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
  FXLLA_STATS_FILE              passive metrics time-series (default: the CLI's
                                stats.jsonl under the state dir)
"""

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
import metrics  # noqa: E402  (local module, added to sys.path above)

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


def downloaded_models():
    """Map alias -> {size_mb} for models with a completion marker."""
    out = {}
    if not os.path.isdir(MODELS_DIR):
        return out
    for name in sorted(os.listdir(MODELS_DIR)):
        d = os.path.join(MODELS_DIR, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, ".source")):
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
                 "size_mb": m["size_mb"]} for a, m in models.items()]})
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
