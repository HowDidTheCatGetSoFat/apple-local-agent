# Contributor and agent guide

Conventions for anyone working in this repository, human or automated. Read
this before opening a pull request.

## Language

- All code, comments, identifiers, commit messages, and documentation are in
  professional English. No other language in the repository.
- The end-user application may be localized (English, Portuguese, Spanish).
  Localization lives in resource files, never hardcoded in logic. Today that
  resource layer is `app/Sources/fxllaMenuBar/L.swift`: its English keys map to
  Portuguese and Spanish values, which are the one place non-English text and
  accented characters are expected. English remains the base language, and no
  other file carries translated strings.

## Shell scripts

- Target the macOS system bash (3.2). Avoid features that require bash 4 or
  later.
- Scripts must run when invoked from any shell. The entrypoint re-execs under
  bash when started by another shell such as zsh.
- Quote expansions. For possibly-empty arrays under `set -u`, use
  `${arr[@]+"${arr[@]}"}`.
- Run `bash -n` on every script before committing.

## Style

- No em dashes, en dashes, arrows, or emojis in any tracked file. Use plain
  ASCII punctuation.
- Keep comments at the density of the surrounding code.
- The CLI is the single source of truth. The app and MCP servers call the CLI
  and read its state; they do not duplicate its logic.

## Secrets

- Never commit tokens or keys. `config/config.env` is git-ignored and is the
  only place for tokens (`HF_TOKEN`, `FXLLA_CIVITAI_TOKEN`).
- Do not print secrets in logs or error messages.

## Commits and pull requests

- Small, focused commits with clear messages.
- Accumulate work on a feature branch and request a review only once it has
  about 20 commits, so reviews batch meaningful work instead of fragmenting.
- Do not request a review on a trivial or single-commit change.

## Review

- Greptile reviews pull requests (installed as a GitHub app on the org).
  Request a Greptile review once the branch has reached about 20 commits.
- CodeRabbit is available on demand as a second opinion. Trigger it with a
  comment on the pull request (`@coderabbitai review`).
- CI runs shell lint (shellcheck and `bash -n`) plus every `tests/test_*.sh`
  suite, and the Python unit tests (`rag`, `graph`, `gateway`, `media`,
  `evals`) on changes to `bin/`, `lib/`, shell scripts, or the `rag/`,
  `graph/`, `gateway/`, or `media/` directories. A separate App workflow
  compiles the Swift app on changes to `app/`. CodeQL analyzes the Python
  sources and the workflow files on pushes to main and pull requests that
  touch them.

## Testing

- `bash -n bin/fxlla lib/core.sh tests/*.sh` for syntax.
- Run the shell suites: `for t in tests/test_*.sh; do bash "$t" || exit 1; done`.
- Run the Python suites: `FXLLA_STORE=/tmp python3 -m unittest rag.test_rag graph.test_graph gateway.test_gateway gateway.test_metrics gateway.test_gateway_e2e media.test_media evals.test_evals`.
- Smoke test the read-only commands: `fxlla models`, `fxlla config`, `fxlla ram`.
- For runtime changes, validate with the `tiny` model end to end.
