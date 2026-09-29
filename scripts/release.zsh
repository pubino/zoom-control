#!/usr/bin/env zsh
# Cut a zoom-control release from this Mac, signing and notarizing with your local
# Developer ID identities and notarytool keychain profile.
#
#   scripts/release.zsh --check                 # verify prerequisites only (no changes)
#   scripts/release.zsh 0.1.1                   # bump, test, build, sign, notarize, tag, publish
#   scripts/release.zsh 0.1.1 --config-repo ../zoom-control-config   # …and open a pin-bump PR there
#
# Options:
#   --check            prerequisites only
#   --skip-tests       skip test suites (not recommended)
#   --no-docker        run native tests only (pytest + swift) instead of scripts/test-all.zsh
#   --no-publish       stop after building dist/<version>/ and committing + tagging locally
#   --config-repo DIR  after publishing, bump setup-zoomctl@vX in DIR and open a PR
#
# Env overrides: DEVELOPER_ID_APP, DEVELOPER_ID_INSTALLER (or "none"), NOTARY_PROFILE
# (default zoom-control-notary). See docs/releasing.md.
set -euo pipefail
source "${0:A:h}/lib/release.zsh"
cd "$REPO_ROOT"

version="" check_only=0 skip_tests=0 no_docker=0 publish=1 config_repo=""
while (( $# )); do
  case "$1" in
    --check) check_only=1; shift ;;
    --skip-tests) skip_tests=1; shift ;;
    --no-docker) no_docker=1; shift ;;
    --no-publish) publish=0; shift ;;
    --config-repo) config_repo="${2:A}"; shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [[ -z "$version" ]] || die "unexpected argument: $1"; version="${1#v}"; shift ;;
  esac
done
(( check_only )) || [[ -n "$version" ]] || die "usage: release.zsh <version> | --check"

# ---------------------------------------------------------------- prerequisites
problems=0
need() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else warn "$1 — ${3:-missing}"; problems=$((problems + 1)); fi; }

[[ "$(uname -s)" == Darwin ]] || die "releases are built on macOS"
need "gh authenticated" "gh auth status" "run: gh auth login"
need "Xcode command line tools" "xcrun --find notarytool" "install Xcode"
need "python3.12" "command -v python3.12" "brew install python@3.12"
app_id="${DEVELOPER_ID_APP:-$(find_identity "Developer ID Application" || true)}"
need "Developer ID Application identity${app_id:+ ($app_id)}" "[[ -n '$app_id' ]]" \
  "create at developer.apple.com ▸ Certificates (Developer ID Application) and install it"
inst_id="${DEVELOPER_ID_INSTALLER:-$(find_identity "Developer ID Installer" || true)}"
if [[ -n "$inst_id" && "$inst_id" != none ]]; then ok "Developer ID Installer identity ($inst_id) → signed .pkg"
else warn "no Developer ID Installer identity → roomagent ships as a notarized .zip (fine; .pkg preferred)"; fi
need "notarytool credentials (profile ${NOTARY_PROFILE:-zoom-control-notary})" "notary_args" \
  "run once: xcrun notarytool store-credentials ${NOTARY_PROFILE:-zoom-control-notary} --apple-id <id> --team-id <TEAMID>"
need "on branch main" "[[ \$(git rev-parse --abbrev-ref HEAD) == main ]]"
need "clean working tree" "[[ -z \$(git status --porcelain) ]]" "commit or stash changes"
git fetch -q origin main --tags 2>/dev/null || true
need "main up to date with origin" "[[ \$(git rev-parse HEAD) == \$(git rev-parse origin/main) ]]" "git pull / push first"
need "versions consistent" "check_versions" "ctl/pyproject.toml, zoomctl/__init__.py, Report.swift disagree"
(( no_docker || skip_tests )) || need "docker running" "docker info" "start Docker or pass --no-docker"

if [[ -n "$version" ]]; then
  current="$(current_versions | head -1 | cut -f2)"
  need "version $version is semver" "is_semver $version"
  need "version $version > current $current" "version_gt $version $current"
  need "tag v$version is free" "! git rev-parse -q --verify refs/tags/v$version && ! git ls-remote --exit-code --tags origin v$version"
fi
[[ -n "$config_repo" ]] && need "config repo at $config_repo" "git -C '$config_repo' rev-parse"

(( problems == 0 )) || die "$problems prerequisite(s) failed"
(( check_only )) && { ok "ready to release"; exit 0; }

# ---------------------------------------------------------------- bump + test
info "bumping version to $version"
set_version "$version"
(cd ctl && { [[ -x .venv/bin/python ]] || python3.12 -m venv .venv; } && .venv/bin/pip install -q -e '.[dev]')

if (( ! skip_tests )); then
  if (( no_docker )); then
    (cd ctl && .venv/bin/pytest -q)
    agent/scripts/test.zsh
  else
    scripts/test-all.zsh
  fi
fi
ctl/.venv/bin/zoomctl schema --check --out schemas

# ---------------------------------------------------------------- build artifacts
dist="$REPO_ROOT/dist/v$version"
rm -rf "$dist"; mkdir -p "$dist"
DEVELOPER_ID_APP="$app_id" DEVELOPER_ID_INSTALLER="${inst_id:-none}" VERSION="$version" OUT="$dist" \
  agent/scripts/notarize.zsh

info "building zoomctl wheel"
bvenv="$(mktemp -d)/venv"
python3.12 -m venv "$bvenv" && "$bvenv/bin/pip" install -q build
"$bvenv/bin/python" -m build -q --wheel --outdir "$dist" ctl
rm -rf "${bvenv:h}"
(cd "$dist" && shasum -a 256 * > SHA256SUMS)
ok "artifacts in $dist:"; ls -1 "$dist" >&2

# ---------------------------------------------------------------- commit + tag
git add "${VERSION_FILES[@]}"
git commit -qm "Release v$version"
git tag -a "v$version" -m "v$version"
ok "committed and tagged v$version"
(( publish )) || { ok "--no-publish: push with 'git push origin main v$version' and upload $dist yourself"; exit 0; }

# ---------------------------------------------------------------- publish
git push -q origin main "v$version"
pkg_note="roomagent: notarized $(ls "$dist" | grep -E '^roomagent-.*\.(pkg|zip)$')"
requirement="$(cat "$dist/roomagent-$version.requirement")"
gh release create "v$version" "$dist"/* --verify-tag --title "v$version" --notes "$(cat <<NOTES
zoomctl + roomagent v$version

- $pkg_note
- PPPC code requirement: \`$requirement\`
- Config repo pin: \`pu-shd/zoom-control/.github/actions/setup-zoomctl@v$version\`
- Node upgrade: \`node/update.zsh --version v$version\`
NOTES
)"
ok "published $(gh release view "v$version" --json url -q .url)"
info "the 'Verify release' workflow now checks the published assets; watch it with:"
info "  gh run watch \$(gh run list -w verify-release.yml -L 1 --json databaseId -q '.[0].databaseId')"

# ---------------------------------------------------------------- config pin bump
if [[ -n "$config_repo" ]]; then
  branch="bump-zoom-control-v$version"
  git -C "$config_repo" fetch -q origin main
  git -C "$config_repo" switch -q -c "$branch" origin/main
  sed -i '' -E "s#(pu-shd/zoom-control/\.github/actions/setup-zoomctl@)v[0-9]+\.[0-9]+\.[0-9]+#\1v$version#" \
    "$config_repo"/.github/workflows/*.yml
  git -C "$config_repo" add .github/workflows
  git -C "$config_repo" commit -qm "Bump zoom-control to v$version"
  git -C "$config_repo" push -q -u origin "$branch"
  (cd "$config_repo" && gh pr create --fill --base main --head "$branch")
  git -C "$config_repo" switch -q main
fi
