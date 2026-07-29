---
name: fxlla-knowledge
description: Retrieve facts from a local fxlla RAG knowledge base before answering questions about the user's documents, notes, or a codebase that has been indexed. Use when the answer likely lives in indexed material rather than general knowledge.
---

# Use the local knowledge base

fxlla exposes a retrieval tool over the user's own files through an MCP server
(`rag_search`). Before answering a question that depends on the user's specific
documents, notes, or an indexed codebase, retrieve first instead of guessing.

## When to use

- The user asks about their own material ("what does our spec say about X",
  "how did we handle Y last time").
- The answer should come from indexed files, not general knowledge.
- You are about to state a project-specific fact you are not certain of.

## How

- Call `rag_search` with a focused query. Prefer specific terms over whole
  questions.
- Ground the answer in what comes back. Quote or cite the source path when it
  matters.
- If nothing relevant returns, say so rather than inventing an answer, and offer
  to index the relevant files (`fxlla kb add <name> <paths...>`).

## Notes

- Retrieval is local; nothing leaves the machine.
- A knowledge base must exist first. If none is set up, suggest
  `fxlla kb add <name> <paths...>` and `fxlla pull embed` for the embedder.
