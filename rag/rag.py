#!/usr/bin/env python3
"""fxlla RAG: index files into a local knowledge base and search them.

Storage is a SQLite database under <store>/kb. Embeddings come from a local
llama.cpp embedding server (the 'embed' catalog model). Standard library only,
plus llama-server for embeddings.

Usage:
  rag.py add <kb> <path...>     index files or directories into a knowledge base
  rag.py search <kb> <query>    top-k chunks for a query
  rag.py ls                     list knowledge bases
  rag.py rm <kb>                delete a knowledge base
"""
import argparse
import glob
import json
import os
import sqlite3
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


def _embed_model():
    ggufs = sorted(glob.glob(os.path.join(EMBED_DIR, "*.gguf")))
    return ggufs[0] if ggufs else None


# Runs a local llama.cpp embedding server for the lifetime of the context.
class Embedder:
    def __enter__(self):
        model = _embed_model()
        if not model:
            sys.exit("embedding model not found. Run: fxlla pull embed --quant Q5_K_M")
        self.proc = subprocess.Popen(
            ["llama-server", "--embeddings", "-m", model, "--host", "127.0.0.1",
             "--port", str(EMBED_PORT), "--pooling", "mean"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{EMBED_PORT}/health", timeout=2).read()
                return self
            except Exception:
                time.sleep(1)
        self.__exit__(None, None, None)
        sys.exit("embedding server did not become ready")

    def embed(self, texts):
        body = json.dumps({"input": texts}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{EMBED_PORT}/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"})
        data = json.load(urllib.request.urlopen(req, timeout=300))
        return [d["embedding"] for d in data["data"]]

    def __exit__(self, *_a):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
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
    con = _db()
    n = con.execute("DELETE FROM chunks WHERE kb=?", (args.name,)).rowcount
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
                if os.sep + ".git" in root:
                    continue
                for n in names:
                    if os.path.splitext(n)[1].lower() in TEXT_EXTS:
                        files.append(os.path.join(root, n))
        elif os.path.isfile(p):
            files.append(p)
    return files


def cmd_add(args):
    files = _gather(args.paths)
    if not files:
        sys.exit("no text files found in the given paths")
    con = _db()
    total = 0
    with Embedder() as emb:
        for f in files:
            try:
                text = open(f, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            chunks = chunk_text(text)
            if not chunks:
                continue
            con.execute("DELETE FROM chunks WHERE kb=? AND source=?", (args.name, f))
            vecs = emb.embed(chunks)
            con.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?)",
                [(args.name, f, i, c, _pack(v)) for i, (c, v) in enumerate(zip(chunks, vecs))],
            )
            con.commit()
            total += len(chunks)
            print(f"  {f}: {len(chunks)} chunks")
    print(f"indexed {len(files)} files, {total} chunks into '{args.name}'")


def cmd_search(_args):
    sys.exit("search: not implemented yet")


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
    args = p.parse_args()
    {"ls": cmd_ls, "rm": cmd_rm, "add": cmd_add, "search": cmd_search}[args.cmd](args)


if __name__ == "__main__":
    if not STORE or not os.path.isdir(STORE):
        sys.exit("FXLLA_STORE is not set or does not exist")
    main()
