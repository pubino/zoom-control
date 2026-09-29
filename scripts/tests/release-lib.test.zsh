#!/usr/bin/env zsh
# Unit tests for scripts/lib/release.zsh. Runs against a scratch copy of the version files.
set -uo pipefail
here="${0:A:h}"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
for f in ctl/pyproject.toml ctl/zoomctl/__init__.py agent/Sources/RoomAgentCore/Report.swift; do
  mkdir -p "$scratch/${f:h}" && cp "$here/../../$f" "$scratch/$f"
done
export REPO_ROOT="$scratch"
source "$here/../lib/release.zsh"

passed=0 failed=0
t() {  # t "name" command...
  local name="$1"; shift
  if ( "$@" ) >/dev/null 2>&1; then passed=$((passed + 1)); print "ok   $name"
  else failed=$((failed + 1)); print "FAIL $name"; fi
}
nt() {  # expects the command to fail (including via exit)
  local name="$1"; shift
  if ( "$@" ) >/dev/null 2>&1; then failed=$((failed + 1)); print "FAIL $name"
  else passed=$((passed + 1)); print "ok   $name"; fi
}
eq() { [[ "$1" == "$2" ]]; }

cur="$(current_versions | head -1 | cut -f2)"
t  "current versions are consistent"         check_versions
t  "current version matches itself"           check_versions "$cur"
nt "different expected version fails"         check_versions "99.0.0"
t  "semver accepted"                          is_semver 1.2.3
nt "v-prefix rejected"                        is_semver v1.2.3
nt "two-part rejected"                        is_semver 1.2
t  "0.1.10 > 0.1.9"                           version_gt 0.1.10 0.1.9
t  "1.0.0 > 0.9.9"                            version_gt 1.0.0 0.9.9
nt "equal is not greater"                     version_gt 0.1.0 0.1.0
nt "older is not greater"                     version_gt 0.1.0 0.2.0

set_version 4.5.6 >/dev/null 2>&1
t  "set_version updates all three files"      check_versions 4.5.6
t  "pyproject rewritten"                      grep -q '^version = "4.5.6"' "$scratch/ctl/pyproject.toml"
t  "swift constant rewritten"                 grep -q 'roomAgentVersion = "4.5.6"' "$scratch/agent/Sources/RoomAgentCore/Report.swift"
nt "set_version rejects non-semver"           set_version banana
sed -i '' 's/__version__ = "4.5.6"/__version__ = "4.5.7"/' "$scratch/ctl/zoomctl/__init__.py"
nt "drift in one file is detected"            check_versions

one='  1) 273475FF24FE9BDE37878FB68EE312B49156F907 "Apple Distribution: A B (TEAM000001)"
  2) DA60E7B212999B7B7E206C24A5FAD397FE2DF0E4 "Developer ID Application: A B (TEAM000001)"
     2 valid identities found'
t  "finds the single Developer ID Application" eq "$(FIND_IDENTITY_OUTPUT="$one" find_identity "Developer ID Application")" "Developer ID Application: A B (TEAM000001)"
nt "no installer identity → failure"          env FIND_IDENTITY_OUTPUT="$one" zsh -c "source $here/../lib/release.zsh; find_identity 'Developer ID Installer'"
two="$one
  3) 0000000000000000000000000000000000000000 \"Developer ID Application: C D (TEAM000002)\""
nt "ambiguous identities → failure"           env FIND_IDENTITY_OUTPUT="$two" zsh -c "source $here/../lib/release.zsh; find_identity 'Developer ID Application'"
t  "APPLE_* env yields password credentials"  eq "$(APPLE_ID=a@b.c APPLE_TEAM_ID=T APPLE_APP_PASSWORD=p notary_args)" "--apple-id a@b.c --team-id T --password p"
nt "missing profile and env → failure"        env -u APPLE_ID NOTARY_PROFILE=definitely-not-a-profile-xyz zsh -c "source $here/../lib/release.zsh; notary_args"

print "\n$passed passed, $failed failed"
(( passed > 0 )) || { print -u2 "no tests ran"; exit 1; }
(( failed == 0 ))
