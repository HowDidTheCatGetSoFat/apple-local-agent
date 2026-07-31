#!/usr/bin/env python3
"""Run model-generated Python against a task's asserts, contained.

This is an accident guard, not a security boundary: it exists to stop runaway
loops, fork storms, giant files, casual network use, and leakage of the
caller's environment (config.env holds HF_TOKEN) into code a model wrote. The
prompts in tasks.json are the only thing models are asked to write; evaluating
weights you do not trust at all belongs in a virtual machine, and the README
says so.

Standard library only. The optional sandbox-exec wrapper is macOS hardening on
top, and it is STRICTER than the stdlib path: it denies network and
out-of-directory writes at the OS level, which no Python-level tripwire can
fully do (the socket patch below is a speed bump - _socket is still
importable - and file writes have no Python-level counterpart at all). For
code that does what the tasks ask, the two paths agree; for code probing the
sandbox itself, the seatbelt can flip a verdict from pass to fail, never the
reverse. The Linux CI runs the weaker stdlib path, so that is the floor the
tests pin.
"""
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile

WALL_CLOCK_S = 10       # the real backstop: Darwin does not enforce RLIMIT_AS
OUTPUT_CAP = 64 * 1024  # a solution that prints megabytes is not more correct

# The prologue runs before the solution is imported. -I strips the script
# directory from sys.path along with PYTHONPATH and user site, so the task
# directory - and only it - is put back explicitly: the point of -I here is
# that nothing from the CALLER's world is importable, not that the solution
# cannot find itself. socket.socket is replaced so casual network use raises
# no matter when it happens; a solution that needs the network is wrong for
# every task in the set, so raising is the correct verdict either way. This is
# a tripwire, not a wall - _socket remains importable - and the OS-level
# denial, where present, is the seatbelt's job.
_PROLOGUE = (
    "import os as _os, sys as _sys\n"
    "_sys.path.insert(0, _os.getcwd())\n"
    "import socket as _s\n"
    "import _socket as _rs\n"
    "def _deny(*a, **k):\n"
    "    raise RuntimeError('network is disabled in the eval sandbox')\n"
    "_s.socket = _deny\n"
    "_s.create_connection = _deny\n"
    "_rs.socket = _deny\n"
)

# Printed by check.py after the asserts, required for a pass verdict: exit
# status alone is forgeable from module level (os._exit(0) in a solution ends
# the interpreter with 0 before any assert has run).
_SENTINEL = "FXLLA-CHECK-COMPLETE"


def _rlimits():
    """Applied in the child between fork and exec. Each limit is best-effort:
    what Darwin refuses to enforce is documented rather than pretended, and the
    wall clock above is the backstop that always works."""
    for res, limit in (
        (resource.RLIMIT_CPU, (5, 5)),
        (resource.RLIMIT_FSIZE, (5 * 1024 * 1024, 5 * 1024 * 1024)),
        (resource.RLIMIT_NOFILE, (32, 32)),
        (resource.RLIMIT_NPROC, (16, 16)),        # fork storm
        (resource.RLIMIT_DATA, (1 << 30, 1 << 30)),  # unenforced on Darwin
    ):
        try:
            resource.setrlimit(res, limit)
        except (ValueError, OSError):
            pass


def _sandbox_exec_argv(argv, taskdir):
    """Wrap argv in the macOS seatbelt when the binary exists; else unchanged.

    Denies network and file writes outside the task directory at the OS level,
    which the rlimits above cannot. Existence-conditional so the same code runs
    on the Linux CI, where the stdlib path is the one the tests exercise.
    """
    sandbox = "/usr/bin/sandbox-exec"
    if not os.path.exists(sandbox):
        return argv, False
    # The path lands inside a double-quoted s-expression: escape it, or a
    # quote in a tempdir path produces an unparseable profile whose non-zero
    # exit would read as the model failing.
    path = os.path.realpath(taskdir).replace("\\", "\\\\").replace('"', '\\"')
    profile = (
        '(version 1)\n'
        '(allow default)\n'
        '(deny network*)\n'
        '(deny file-write*)\n'
        '(allow file-write* (subpath "%s"))\n'
        '(allow file-write* (literal "/dev/null"))\n'
    ) % path
    return [sandbox, "-p", profile] + argv, True


def run_code(solution_text, check_snippet, timeout_s=WALL_CLOCK_S, keep_dir=False):
    """Execute a solution against a task's asserts.

    Returns {"verdict": "pass"|"fail"|"timeout"|"error", "seconds": float,
    "output": str (capped), "taskdir": path or None}. "error" means the harness
    could not run the child at all, and must never be scored as a model failure.
    """
    taskdir = tempfile.mkdtemp(prefix="fxlla-eval-")
    try:
        with open(os.path.join(taskdir, "solution.py"), "w", encoding="utf-8") as fh:
            fh.write(solution_text)
        with open(os.path.join(taskdir, "check.py"), "w", encoding="utf-8") as fh:
            fh.write(_PROLOGUE + "\n" + check_snippet
                     + "\nprint(%r)\n" % _SENTINEL)

        # The env allowlist is load-bearing: HF_TOKEN and every FXLLA_* value
        # from config.env must never reach model-generated code.
        env = {"PATH": "/usr/bin:/bin", "HOME": taskdir, "TMPDIR": taskdir}
        argv = [sys.executable, "-I", "-B", "check.py"]
        argv, _wrapped = _sandbox_exec_argv(argv, taskdir)

        import time
        begin = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv, cwd=taskdir, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True,
                preexec_fn=_rlimits)
        except OSError as exc:
            return {"verdict": "error", "seconds": 0.0,
                    "output": str(exc), "taskdir": None}
        try:
            out, _ = proc.communicate(timeout=timeout_s)
            # Exit 0 alone is forgeable (os._exit(0) at module level ends the
            # interpreter before any assert runs): a pass also requires the
            # sentinel the check prints after its asserts.
            passed = (proc.returncode == 0
                      and _SENTINEL.encode() in (out or b""))
            verdict = "pass" if passed else "fail"
        except subprocess.TimeoutExpired:
            # Kill the whole group: the solution may have forked.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            # A grandchild that left the group can hold the pipe open past the
            # kill; an unbounded drain here would hang the whole eval on it.
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
                proc.wait()
                out = b"(output lost: a child kept the pipe open past the kill)"
            verdict = "timeout"
        seconds = time.monotonic() - begin
        text = (out or b"")[:OUTPUT_CAP].decode("utf-8", "replace")
        text = text.replace(_SENTINEL + "\n", "").replace(_SENTINEL, "")
        if text.startswith("sandbox-exec:"):
            # The seatbelt itself failed to start (bad profile, not bad code):
            # never chargeable to the model.
            return {"verdict": "error", "seconds": round(seconds, 2),
                    "output": text, "taskdir": taskdir if keep_dir else None}
        return {"verdict": verdict, "seconds": round(seconds, 2),
                "output": text, "taskdir": taskdir if keep_dir else None}
    finally:
        if not keep_dir:
            shutil.rmtree(taskdir, ignore_errors=True)
