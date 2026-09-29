# Releasing

Releases are cut **locally** with `scripts/release.zsh`, signing and notarizing with your own
Developer ID. CI never holds signing keys. After you publish, the **Verify release** workflow
independently checks the assets: signatures, notarization, checksums, and that versions match.

## One-time setup

1. **Developer ID Application** certificate (required). Create it at developer.apple.com ▸
   Certificates, IDs & Profiles ▸ **+** ▸ *Developer ID Application* (Account Holder role), or in
   Xcode ▸ Settings ▸ Accounts ▸ Manage Certificates. Check that it's installed:
   ```zsh
   security find-identity -v -p codesigning | grep "Developer ID Application"
   ```
2. **Developer ID Installer** certificate (optional, preferred). With it, roomagent ships as a
   signed, notarized, **stapled** `.pkg`. Without it, it ships as a notarized `.zip` of the signed
   binary. Both give the same TCC stability, because grants bind to the Developer ID code
   signature. `node/update.zsh` installs either.
3. **Notarization credentials** stored in your keychain. You need an app-specific password from
   <https://account.apple.com> ▸ Sign-In and Security ▸ App-Specific Passwords:
   ```zsh
   xcrun notarytool store-credentials zoom-control-notary \
     --apple-id you@example.com --team-id W7EJE9LZ23
   ```
   Use another profile name via `NOTARY_PROFILE=…`.
4. `brew install gh python@3.12`, `gh auth login`, and Docker running (or pass `--no-docker`).

## Cut a release

```zsh
scripts/release.zsh --check                                       # prerequisites only
scripts/release.zsh 0.1.1 --config-repo ../zoom-control-config    # the real thing
```

What it does, stopping at the first failure:

1. Checks prerequisites: clean `main` in sync with origin, identities, notary profile, the new
   version is higher than the current one and its tag is unused.
2. Bumps the version in `ctl/pyproject.toml`, `ctl/zoomctl/__init__.py` and `Report.swift`.
3. Runs every test suite (`scripts/test-all.zsh`: Docker Python, Docker Linux Swift, native Swift)
   and the schema drift check.
4. Builds a universal `roomagent`, signs it (hardened runtime, timestamp), notarizes it, and writes
   `dist/v<version>/`:
   `roomagent-<v>.pkg` or `roomagent-<v>-macos.zip`, `roomagent-<v>.requirement`,
   `zoomctl-<v>-py3-none-any.whl`, `SHA256SUMS`.
5. Commits `Release v<version>`, tags `v<version>`, pushes both, and creates the GitHub Release.
6. With `--config-repo`, opens a PR there bumping `setup-zoomctl@v<version>`.

Then:

```zsh
gh run watch $(gh run list -w verify-release.yml -L 1 --json databaseId -q '.[0].databaseId')
```

Merge the config-repo PR. On each node, between events: `node/update.zsh --version v<version>`.
If the `.requirement` changed (for example a new team ID), re-deploy the PPPC profile.

Useful flags: `--no-publish` stops after the local commit and tag. `--no-docker` runs native tests
only. `--skip-tests` exists for emergencies.

## If something fails midway

* Before step 5, nothing has left your machine. `git checkout -- .` reverts the version bump.
* A failure after tagging but before publishing: `git tag -d v<version> && git reset --hard HEAD~1`.
* Published but verification failed: delete the release and tag
  (`gh release delete v<version> --cleanup-tag`), fix the problem, and release the next patch version.
  Never re-use a published version.

## Note on v0.1.0

v0.1.0 was built by the retired CI release job and contains only an **UNSIGNED** pkg, which the node
scripts refuse to install. Cut v0.1.1 with this script before provisioning nodes.
