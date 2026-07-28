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

HOST = os.environ.get("FXLLA_HOST", "127.0.0.1")
PORT = int(os.environ.get("FXLLA_PORT", "8080"))
STORE = os.environ.get("FXLLA_STORE", "/Volumes/1TB-WD750-1/llm")
MODELS_DIR = os.path.join(STORE, "models")
PORT_BASE = int(os.environ.get("FXLLA_BACKEND_PORT_BASE", "8100"))
FXLLA_BIN = os.environ.get("FXLLA_BIN", "fxlla")


def log(msg):
    sys.stderr.write("[gateway] %s\n" % msg)
    sys.stderr.flush()


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
    def __init__(self, alias, port, proc, size_mb, model_field):
        self.alias = alias
        self.port = port
        self.proc = proc
        self.size_mb = size_mb
        self.model_field = model_field  # value to send in the proxied 'model' field
        self.last_used = time.monotonic()


def model_field_for(alias):
    """The 'model' value each backend expects: the path for MLX, the alias for
    GGUF (llama-server --alias). Never trust the backend's enumerated id, since
    mlx_lm.server lists the whole HF cache in /v1/models."""
    engine = "mlx"
    try:
        with open(os.path.join(MODELS_DIR, alias, ".engine")) as f:
            engine = f.read().strip() or "mlx"
    except Exception:
        pass
    return alias if engine == "gguf" else os.path.join(MODELS_DIR, alias)


class Manager:
    def __init__(self):
        self.backends = {}          # alias -> Backend
        self.lock = threading.RLock()
        self.next_port = PORT_BASE

    def _alloc_port(self):
        used = {b.port for b in self.backends.values()}
        p = PORT_BASE
        while p in used:
            p += 1
        return p

    def _resident_mb(self):
        return sum(b.size_mb for b in self.backends.values())

    def _wait_ready(self, port, timeout=180):
        url = "http://127.0.0.1:%d/v1/models" % port
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    r.read()
                    return True
            except Exception:
                time.sleep(1)
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
        """Return (port, model_field) for alias, loading and evicting as needed."""
        with self.lock:
            b = self.backends.get(alias)
            if b is not None:
                b.last_used = time.monotonic()
                return b.port, b.model_field

            models = downloaded_models()
            if alias not in models:
                raise KeyError(alias)
            size_mb = models[alias]["size_mb"]

            # evict LRU until the new model fits the budget
            while self.backends and self._resident_mb() + size_mb > BUDGET_MB:
                self._evict_one()

            port = self._alloc_port()
            log("loading %s on :%d (%d MB, resident %d/%d MB)"
                % (alias, port, size_mb, self._resident_mb(), BUDGET_MB))
            proc = subprocess.Popen([FXLLA_BIN, "_backend", alias, str(port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not self._wait_ready(port):
                try:
                    proc.terminate()
                except Exception:
                    pass
                raise RuntimeError("backend for %s did not become ready" % alias)
            model_field = model_field_for(alias)
            self.backends[alias] = Backend(alias, port, proc, size_mb, model_field)
            return port, model_field

    def status(self):
        with self.lock:
            return [{"alias": b.alias, "port": b.port, "size_mb": b.size_mb,
                     "idle_s": int(time.monotonic() - b.last_used)}
                    for b in self.backends.values()]

    def shutdown(self):
        with self.lock:
            for b in list(self.backends.values()):
                try:
                    b.proc.terminate()
                except Exception:
                    pass
            self.backends.clear()


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
        try:
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except Exception:
            pass


def _term(signum, frame):
    raise KeyboardInterrupt()


def main():
    signal.signal(signal.SIGTERM, _term)
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
