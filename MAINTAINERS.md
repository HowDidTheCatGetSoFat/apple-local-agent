# Maintainers

Maintainers own the roadmap, review and merge pull requests, and cut releases.

| Name         | GitHub   | Areas                          | Open to work                                  |
|--------------|----------|--------------------------------|-----------------------------------------------|
| Mariano Abad | @fxd0h   | CLI, engines, app, governance  | Yes - [LinkedIn](https://linkedin.com/in/fxd0h) |

Contact: weimaraner@gmail.com

## Responsibilities

- Triage issues and keep the roadmap current.
- Review pull requests and enforce the conventions in `AGENTS.md`.
- Manage releases and the changelog.
- Handle security reports per `SECURITY.md`.

## Releasing the macOS app

The app is signed with a Developer ID certificate and notarized by Apple. Two
different credentials, and confusing them wastes an afternoon:

- **Signing** uses the certificate in the keychain. `app/sign-lib.sh` names the
  default; override with `FXLLA_SIGN_ID`. Without it, `codesign` fails loudly.
- **Notarizing** does not use that certificate at all. It uploads to Apple with
  a *notarytool keychain profile* created once by
  `xcrun notarytool store-credentials <name> ...`, from either an app-specific
  password or an App Store Connect API key.

```
app/package-dmg.sh --check       # identity, notarytool, and whether the
                                 # notary profile authenticates
app/package-dmg.sh               # signed .dmg
app/package-dmg.sh --notarize    # signed, notarized, stapled
```

`--notarize` takes the profile name from `FXLLA_NOTARY_PROFILE` (see
`config/config.env.example`) when you do not pass one.

**To find out whether something was ever notarized, ask Apple, not the
keychain:** `xcrun notarytool history --keychain-profile <name>` lists every
submission and its status. Inspecting the keychain with `security` does not
reliably show notarytool profiles, and reading "no profile found" as "we have
no credentials" is how this project once concluded it had never notarized -
with three Accepted submissions in that history and the profile name sitting in
the git-ignored `config/config.env`.

A stapled `.dmg` validates offline: `xcrun stapler validate` and `spctl -a -t
open` should both accept. Unnotarized, `spctl` reports
`source=Unnotarized Developer ID` and Gatekeeper blocks it on someone else's
machine even though the signature is valid. The `.dmg` and `.app` are
git-ignored build artifacts - they have no history, so overwriting one loses
whatever was in it.

## Decision making

Maintainers decide by consensus. When there is no consensus, the lead
maintainer decides. Significant changes start as an issue before a pull
request.

Part of the HowDidTheCatGetSoFat community.
