#!/usr/bin/env python3
"""Background jobs for fxlla media generation.

Heavy renders (video especially) can run for minutes, which is too long for an
MCP tool call to block on. Submitting a job returns an id immediately; the caller
polls for status and the output path.

No daemon and no dependencies: submitting writes a JSON record under
<media out>/jobs and spawns a detached worker (this module's `run`) that owns the
record for the rest of the job's life. The worker takes an exclusive flock before
starting, so media jobs run one at a time - they share unified memory with the
gateway's models and two concurrent renders would thrash or OOM the machine.
A job waiting on that lock stays 'queued'.

Usage (normally driven via `fxlla media`):
  jobs.py run <id>     execute a submitted job (the detached worker)
"""
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid

STORE = os.environ.get("FXLLA_STORE", "")
OUT_DIR = os.environ.get("FXLLA_MEDIA_OUT") or os.path.join(STORE, "media")
JOBS_DIR = os.path.join(OUT_DIR, "jobs")
GENERATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate.py")

# Statuses a job can still leave on its own.
ACTIVE = ("queued", "running")
_ID_RE = re.compile(r"^[0-9]{10,}-[0-9a-f]{6}$")


def _dir():
    os.makedirs(JOBS_DIR, exist_ok=True)
    return JOBS_DIR


def valid_id(job_id):
    # Job ids reach this module from MCP tool arguments, so validate the shape
    # instead of trusting it in a path join.
    return bool(_ID_RE.match(job_id or ""))


def _path(job_id):
    return os.path.join(_dir(), job_id + ".json")


def log_path(job_id):
    return os.path.join(_dir(), job_id + ".log")


def new_id():
    return "%d-%s" % (time.time(), uuid.uuid4().hex[:6])


def _read(job_id):
    try:
        with open(_path(job_id), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write(rec):
    # Atomic: readers (fxlla media jobs) never see a half-written record.
    path = _path(rec["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)


def _update(job_id, **fields):
    """Re-read then write, so concurrent updates do not clobber each other."""
    rec = _read(job_id)
    if rec is None:
        return None
    rec.update(fields)
    _write(rec)
    return rec


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap(rec):
    """Mark a job failed when its worker vanished (crash, reboot, kill -9)."""
    if rec and rec.get("status") in ACTIVE and rec.get("pid") and not _alive(rec["pid"]):
        rec = _update(rec["id"], status="failed", finished=time.time(),
                      error="worker process died") or rec
    return rec


def submit(kind, argv, summary=""):
    """Record a job and spawn its detached worker. Returns the job record."""
    job_id = new_id()
    rec = {"id": job_id, "kind": kind, "status": "queued", "argv": list(argv),
           "summary": summary, "output": None, "error": None, "pid": None,
           "created": time.time(), "started": None, "finished": None,
           "log": log_path(job_id)}
    _write(rec)
    with open(log_path(job_id), "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "run", job_id],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True, env=os.environ)
    # start_new_session makes the worker a process-group leader, so cancelling it
    # can signal the whole group (worker plus the generator it spawns).
    if (_read(job_id) or {}).get("status") == "queued":
        rec = _update(job_id, pid=proc.pid) or rec
    return rec


def get(job_id):
    if not valid_id(job_id):
        return None
    return _reap(_read(job_id))


def listing():
    jobs = []
    for name in sorted(os.listdir(_dir())):
        if not name.endswith(".json"):
            continue
        rec = _reap(_read(name[:-len(".json")]))
        if rec:
            jobs.append(rec)
    jobs.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return jobs


def prune():
    """Drop finished job records (and their logs). Active jobs are kept."""
    removed = 0
    for rec in listing():
        if rec["status"] in ACTIVE:
            continue
        for path in (_path(rec["id"]), log_path(rec["id"])):
            try:
                os.remove(path)
            except OSError:
                pass
        removed += 1
    return removed


def cancel(job_id):
    rec = get(job_id)
    if rec is None or rec["status"] not in ACTIVE:
        return rec
    pid = rec.get("pid")
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return _update(job_id, status="cancelled", finished=time.time())


def run(job_id):
    """The detached worker: serialize on the lock, then run the generator."""
    if not valid_id(job_id) or _read(job_id) is None:
        sys.exit("unknown job: %s" % job_id)
    with open(os.path.join(_dir(), "lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rec = _read(job_id)
        # Cancelled while it sat in the queue: nothing left to do.
        if rec is None or rec["status"] not in ACTIVE:
            return
        _update(job_id, status="running", started=time.time(), pid=os.getpid())
        try:
            proc = subprocess.run([sys.executable, GENERATE] + list(rec["argv"]),
                                  capture_output=True, text=True, env=os.environ)
        except Exception as exc:  # the generator could not even start
            _update(job_id, status="failed", finished=time.time(), error=str(exc))
            return
        if proc.returncode != 0:
            _update(job_id, status="failed", finished=time.time(),
                    error=(proc.stderr.strip() or "generation failed")[-800:])
            return
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            _update(job_id, status="failed", finished=time.time(),
                    error="generator returned no output path")
            return
        _update(job_id, status="done", finished=time.time(), output=lines[-1])


def _fmt_age(rec):
    end = rec.get("finished") or time.time()
    start = rec.get("created") or end
    return "%ds" % int(end - start)


def describe(rec):
    """One line for `fxlla media jobs`."""
    tail = rec.get("output") or ""
    if not tail and rec.get("error"):
        # Errors are captured stderr, usually a traceback: the last line carries
        # the actual message, so show that rather than the top of the trace.
        lines = [ln.strip() for ln in rec["error"].splitlines() if ln.strip()]
        tail = lines[-1] if lines else ""
    if len(tail) > 60:
        tail = tail[:57] + "..."
    return "%s  %-8s %-9s %-6s %s" % (rec["id"], rec["kind"], rec["status"],
                                      _fmt_age(rec), tail or rec.get("summary", ""))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "run":
        sys.exit("usage: jobs.py run <id>")
    run(sys.argv[2])
