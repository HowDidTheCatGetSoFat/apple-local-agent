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
import os
import sqlite3
import sys

STORE = os.environ.get("FXLLA_STORE", "")
KB_DIR = os.path.join(STORE, "kb")
DB_PATH = os.path.join(KB_DIR, "kb.db")


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


def cmd_add(_args):
    sys.exit("add: not implemented yet")


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
