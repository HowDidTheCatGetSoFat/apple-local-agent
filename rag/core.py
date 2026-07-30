#!/usr/bin/env python3
"""fxlla RAG: index files into a local knowledge base and search them.

Storage is a SQLite database under <store>/kb. Embeddings come from a local
llama.cpp embedding server (the 'embed' catalog model). Standard library only,
plus llama-server for embeddings.

Search defaults to a brute-force cosine scan. Set FXLLA_KB_INDEX=1 and run this
module under a python that can load extensions (e.g. `uv run --with sqlite-vec`)
to use a sqlite-vec KNN index instead; the index is rebuilt from the chunks table
on demand and the code silently falls back to the scan when unavailable.

Usage (normally driven via `fxlla kb`):
  core.py add <kb> <path...>    index files or directories into a knowledge base
  core.py search <kb> <query>   top-k chunks for a query
  core.py ls                    list knowledge bases
  core.py rm <kb>               delete a knowledge base
"""
import argparse
import fcntl
import glob
import json
import math
import os
import re
import sqlite3
import http.client
import struct
import subprocess
import sys
import time
import urllib.request

STORE = os.environ.get("FXLLA_STORE", "")
KB_DIR = os.path.join(STORE, "kb")
DB_PATH = os.path.join(KB_DIR, "kb.db")
EMBED_DIR = os.path.join(STORE, "models", "embed")
EMBED_PORT = int(os.environ.get("FXLLA_EMBED_PORT", "8090"))


def _index_enabled():
    return os.environ.get("FXLLA_KB_INDEX", "").lower() in ("1", "true", "yes", "on")


def _embed_model():
    ggufs = sorted(glob.glob(os.path.join(EMBED_DIR, "*.gguf")))
    return ggufs[0] if ggufs else None


# The poll interval is what decides the cost of a query: the server answers in
# well under a second, so at a one-second interval every search slept through a
# server that was already up. The deadline is generous instead, because a larger
# model on an external disk can genuinely take a while to load.
_READY_TIMEOUT = 180.0
_POLL_INTERVAL = 0.02
# A loaded machine can be slow to answer /health. Too short a timeout reports a
# working server as absent, which then fails the query outright.
_HEALTH_TIMEOUT = 5.0


def _server_healthy(timeout=_HEALTH_TIMEOUT):
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{EMBED_PORT}/health", timeout=timeout).read()
        return True
    except Exception:
        return False


# Runs a local llama.cpp embedding server for the lifetime of the context, or
# reuses one that is already listening.
class Embedder:
    def __enter__(self):
        # A server someone else started is not ours to stop. Reusing it is also
        # what makes repeated queries cost milliseconds: leave one running and
        # searches skip startup entirely.
        self.proc = None
        self._lock = None
        if _server_healthy():
            return self
        # Serialize starting one. Without this, concurrent searches each spawn a
        # server, all but one fail to bind, and the losers have to guess whether
        # the port is held by a peer or by something unrelated. Holding the lock
        # makes that unambiguous: nobody else is starting one, so if ours dies the
        # failure is real.
        os.makedirs(KB_DIR, exist_ok=True)
        self._lock = open(os.path.join(KB_DIR, "embed.lock"), "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX)
            if _server_healthy():  # started while we waited for the lock
                return self
            model = _embed_model()
            if not model:
                sys.exit("embedding model not found. Run: fxlla pull embed --quant Q5_K_M")
            self.proc = subprocess.Popen(
                ["llama-server", "--embeddings", "-m", model, "--host", "127.0.0.1",
                 "--port", str(EMBED_PORT), "--pooling", "mean"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + _READY_TIMEOUT
            while time.time() < deadline:
                if _server_healthy():
                    return self
                if self.proc.poll() is not None:
                    self.proc = None
                    sys.exit("the embedding server exited on start. Port %d may be "
                             "held by something else, or the model may be unreadable."
                             % EMBED_PORT)
                time.sleep(_POLL_INTERVAL)
            self.__exit__(None, None, None)
            sys.exit("embedding server did not become ready")
        finally:
            self._release_lock()

    def _release_lock(self):
        if self._lock is not None:
            try:
                fcntl.flock(self._lock, fcntl.LOCK_UN)
                self._lock.close()
            except Exception:
                pass
            self._lock = None

    def _post(self, texts):
        body = json.dumps({"input": texts}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{EMBED_PORT}/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"})
        data = json.load(urllib.request.urlopen(req, timeout=300))
        return [d["embedding"] for d in data["data"]]

    def embed(self, texts):
        try:
            return self._post(texts)
        # HTTPError subclasses OSError, so it has to be caught first or the retry
        # below swallows it - and retrying a rejected request is pointless anyway.
        except urllib.error.HTTPError as exc:
            sys.exit("the server on port %d rejected an embedding request (%s). Is "
                     "it an embedding server, started with --embeddings?"
                     % (EMBED_PORT, exc))
        except (http.client.RemoteDisconnected, ConnectionError, OSError) as first:
            # A borrowed server can be stopped by its owner while our request is
            # in flight. Re-acquire - starting one if needed - and try once more,
            # rather than dying with a traceback halfway through an index.
            self.__enter__()
            try:
                return self._post(texts)
            except Exception:
                sys.exit("the embedding server on port %d stopped responding (%s)"
                         % (EMBED_PORT, first))

    def __exit__(self, *_a):
        self._release_lock()
        if self.proc is None:
            return  # someone else's server: leave it running
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _db():
    os.makedirs(KB_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS chunks "
        "(kb TEXT, source TEXT, idx INTEGER, text TEXT, emb BLOB)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS chunks_kb ON chunks(kb)")
    return con


# Vector width already stored for a knowledge base, or None when it is empty.
def _kb_dim(con, kb):
    row = con.execute(
        "SELECT length(emb) FROM chunks WHERE kb=? LIMIT 1", (kb,)).fetchone()
    return row[0] // 4 if row else None


# Embeddings from a different model are not comparable with the stored ones, and
# _cosine silently zips to the shorter vector, so a mismatch does not fail - it
# quietly returns nonsense and, on add, mixes widths into one base permanently.
# The reachable cause is a server on EMBED_PORT belonging to another model.
def _require_dim(con, kb, vector):
    stored = _kb_dim(con, kb)
    if stored is not None and len(vector) != stored:
        sys.exit(
            "the embedding server returned %d dimensions but '%s' holds %d. That "
            "server is a different model than the one this base was built with. "
            "Point FXLLA_EMBED_PORT at the right server, or rebuild the base."
            % (len(vector), kb, stored))


def _valid_kb(name):
    if not re.match(r"^[A-Za-z0-9._-]+$", name or ""):
        sys.exit("invalid knowledge base name (use letters, digits, . _ -)")
    return name


def chunk_text(text, size=800, overlap=120):
    text = text.strip()
    out = []
    i = 0
    while i < len(text):
        piece = text[i:i + size]
        if piece.strip():
            out.append(piece)
        i += max(size - overlap, 1)
    return out


def cmd_ls(_args):
    con = _db()
    rows = con.execute(
        "SELECT kb, COUNT(DISTINCT source), COUNT(*) FROM chunks GROUP BY kb"
    ).fetchall()
    if not rows:
        print("(no knowledge bases)")
        return
    for kb, sources, chunks in rows:
        print(f"{kb}\t{sources} sources\t{chunks} chunks")


def cmd_rm(args):
    _valid_kb(args.name)
    con = _db()
    n = con.execute("DELETE FROM chunks WHERE kb=?", (args.name,)).rowcount
    # Dropping a vec0 virtual table needs the extension loaded on this connection.
    # Load it best-effort; if it is unavailable the stale index is left behind and
    # a later search rebuilds it once the extension is present.
    _load_vec_raw(con)
    try:
        con.execute("DROP TABLE IF EXISTS %s" % _vec_table(args.name))
    except sqlite3.OperationalError:
        pass
    con.commit()
    print(f"removed {n} chunks from '{args.name}'")


TEXT_EXTS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".swift", ".sh", ".json",
    ".yaml", ".yml", ".toml", ".html", ".css", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp",
}


def _pack(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def _gather(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                if (os.sep + ".git" + os.sep) in (root + os.sep) or root.endswith(os.sep + ".git"):
                    continue
                for n in names:
                    if os.path.splitext(n)[1].lower() in TEXT_EXTS:
                        files.append(os.path.join(root, n))
        elif os.path.isfile(p):
            files.append(p)
    return files


def cmd_add(args):
    _valid_kb(args.name)
    files = _gather(args.paths)
    if not files:
        sys.exit("no text files found in the given paths")
    con = _db()
    total = 0
    with Embedder() as emb:
        for f in files:
            try:
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except Exception:
                continue
            chunks = chunk_text(text)
            if not chunks:
                continue
            # embed first: on failure the existing chunks are left intact
            vecs = emb.embed(chunks)
            _require_dim(con, args.name, vecs[0])
            con.execute("DELETE FROM chunks WHERE kb=? AND source=?", (args.name, f))
            con.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?)",
                [(args.name, f, i, c, _pack(v)) for i, (c, v) in enumerate(zip(chunks, vecs))],
            )
            con.commit()
            total += len(chunks)
            print(f"  {f}: {len(chunks)} chunks")
    print(f"indexed {len(files)} files, {total} chunks into '{args.name}'")


def _unpack(blob):
    return struct.unpack(f"{len(blob) // 4}f", blob)


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# Loads the sqlite-vec extension into a connection regardless of FXLLA_KB_INDEX.
# Returns the module on success, else None. Used by maintenance paths (rm) that
# must touch a vec0 table even when the index is not the active search backend.
def _load_vec_raw(con):
    try:
        import sqlite_vec
    except Exception:
        return None
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    except Exception:
        return None
    return sqlite_vec


# Loads sqlite-vec only when the index is opted in (FXLLA_KB_INDEX) and a python
# that permits loading extensions is in use (e.g. `uv run --with sqlite-vec`).
# Returns None otherwise, so the caller falls back to the brute-force scan.
def _load_vec(con):
    if not _index_enabled():
        return None
    return _load_vec_raw(con)


def _vec_table(kb):
    # kb is validated against [A-Za-z0-9._-]+, so double-quoting is safe.
    return '"vec_%s"' % kb.replace('"', '')


# Rebuilds a per-kb vec0 index from the chunks table when it is missing or stale
# (row count differs). The chunks table stays the source of truth; the index only
# stores each chunk's rowid and embedding, so a rebuild never re-embeds anything.
def _ensure_vec_index(con, vec, kb):
    rows = con.execute("SELECT rowid, emb FROM chunks WHERE kb=?", (kb,)).fetchall()
    if not rows:
        return 0
    table = _vec_table(kb)
    dim = len(rows[0][1]) // 4
    try:
        have = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except sqlite3.OperationalError:
        have = None
    if have == len(rows):
        return dim
    con.execute("DROP TABLE IF EXISTS %s" % table)
    con.execute(
        "CREATE VIRTUAL TABLE %s USING vec0(embedding float[%d] distance_metric=cosine)"
        % (table, dim)
    )
    con.executemany(
        "INSERT INTO %s(rowid, embedding) VALUES (?, ?)" % table,
        [(rid, vec.serialize_float32(list(_unpack(blob)))) for rid, blob in rows],
    )
    con.commit()
    return dim


# KNN search through the vec0 index. Cosine distance is 1 - cosine similarity, so
# the reported score matches the brute-force _cosine path.
def _search_indexed(con, vec, kb, qv, k):
    _ensure_vec_index(con, vec, kb)
    table = _vec_table(kb)
    hits = con.execute(
        "SELECT rowid, distance FROM %s WHERE embedding MATCH ? AND k = ? "
        "ORDER BY distance" % table,
        (vec.serialize_float32(list(qv)), k),
    ).fetchall()
    out = []
    for rowid, distance in hits:
        row = con.execute(
            "SELECT source, idx, text FROM chunks WHERE rowid=?", (rowid,)
        ).fetchone()
        if row:
            out.append((1.0 - distance, row[0], row[1], row[2]))
    return out


def cmd_search(args):
    _valid_kb(args.name)
    con = _db()
    n = con.execute("SELECT COUNT(*) FROM chunks WHERE kb=?", (args.name,)).fetchone()[0]
    if not n:
        sys.exit(f"knowledge base '{args.name}' is empty")
    with Embedder() as emb:
        qv = emb.embed([args.query])[0]
    _require_dim(con, args.name, qv)
    vec = _load_vec(con)
    if vec is not None:
        top = _search_indexed(con, vec, args.name, qv, args.k)
    else:
        rows = con.execute(
            "SELECT source, idx, text, emb FROM chunks WHERE kb=?", (args.name,)
        ).fetchall()
        scored = [
            (_cosine(qv, _unpack(blob)), source, idx, text)
            for source, idx, text, blob in rows
        ]
        scored.sort(reverse=True, key=lambda x: x[0])
        top = scored[: args.k]
    if args.json:
        print(json.dumps([
            {"score": round(s, 4), "source": src, "chunk": idx, "text": txt}
            for s, src, idx, txt in top
        ]))
        return
    for score, source, idx, text in top:
        print(f"[{score:.3f}] {source}#{idx}")
        print("  " + " ".join(text.split())[:200])


def main():
    p = argparse.ArgumentParser(prog="fxlla-rag")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ls")
    rm = sub.add_parser("rm")
    rm.add_argument("name")
    ad = sub.add_parser("add")
    ad.add_argument("name")
    ad.add_argument("paths", nargs="+")
    se = sub.add_parser("search")
    se.add_argument("name")
    se.add_argument("query")
    se.add_argument("-k", type=int, default=5)
    se.add_argument("-j", "--json", action="store_true")
    args = p.parse_args()
    {"ls": cmd_ls, "rm": cmd_rm, "add": cmd_add, "search": cmd_search}[args.cmd](args)


if __name__ == "__main__":
    if not STORE or not os.path.isdir(STORE):
        sys.exit("FXLLA_STORE is not set or does not exist")
    main()
