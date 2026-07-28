# Contributor and agent guide

Conventions for anyone working in this repository, human or automated. Read
this before opening a pull request.

## Language

- All code, comments, identifiers, commit messages, and documentation are in
  professional English. No other language in the repository.
- The end-user application may be localized (English, Portuguese, Spanish).
  Localization lives in resource files, never hardcoded in logic.

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
  only place for `HF_TOKEN`.
- Do not print secrets in logs or error messages.

## Commits and pull requests

- Small, focused commits with clear messages.
- Group related work into a branch and accumulate enough commits before
  requesting review, so reviews are not fragmented.
- Do not request an automated review on a trivial or single-line change.

## Review

- Greptile reviews pull requests (installed as a GitHub app on the org). Open a
  PR only when the branch has enough commits to warrant a single review; do not
  request a review on a trivial or single-line change.
- CodeRabbit is available on demand as a second opinion. Trigger it with a
  comment on the pull request (`@coderabbitai review`).
- CI runs shell lint (shellcheck and `bash -n`) on changes to `bin/`, `lib/`,
  or shell scripts. CodeQL runs on pull requests that touch workflow files.

## Testing

- `bash -n bin/fxlla lib/core.sh` for syntax.
- Smoke test the read-only commands: `fxlla models`, `fxlla config`, `fxlla ram`.
- For runtime changes, validate with the `tiny` model end to end.
