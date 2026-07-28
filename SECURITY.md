# Security policy

## Reporting a vulnerability

Do not open a public issue for security reports. Use GitHub private
vulnerability reporting on this repository (Security tab, "Report a
vulnerability"). Include a description, reproduction steps, and affected
versions. Expect an initial response within a few business days.

## Supported versions

The project is pre-1.0. Only the latest release and the default branch receive
fixes.

## Handling of secrets

- The tool never transmits credentials to third parties. `HF_TOKEN` is used
  only for direct requests to Hugging Face.
- `config/config.env` holds local configuration and tokens and is git-ignored.
  Keep tokens out of the rest of the tree.
- Downloaded model weights are stored on the disk configured by `FXLLA_STORE`.

## Scope

This tool runs local model servers that bind to `127.0.0.1` by default. Do not
expose the endpoint to untrusted networks without adding authentication in
front of it.
