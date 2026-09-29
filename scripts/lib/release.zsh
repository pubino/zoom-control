# Release helpers shared by scripts/release.zsh and agent/scripts/notarize.zsh. Source, don't run.
# REPO_ROOT may be overridden (tests point it at a scratch copy).
: "${REPO_ROOT:=${${(%):-%x}:A:h:h:h}}"

info() { print -P "%F{blue}▸%f $*" >&2; }
ok()   { print -P "%F{green}✓%f $*" >&2; }
warn() { print -P "%F{yellow}⚠%f $*" >&2; }
die()  { print -P "%F{red}✗%f $*" >&2; exit 1; }

VERSION_FILES=(ctl/pyproject.toml ctl/zoomctl/__init__.py agent/Sources/RoomAgentCore/Report.swift)

is_semver() { [[ "$1" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' ]]; }

# Print "file<TAB>version" for every place a version is declared.
current_versions() {
  local f
  print -r -- "ctl/pyproject.toml	$(sed -nE 's/^version = "([^"]+)"/\1/p' "$REPO_ROOT/ctl/pyproject.toml" | head -1)"
  print -r -- "ctl/zoomctl/__init__.py	$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$REPO_ROOT/ctl/zoomctl/__init__.py")"
  print -r -- "agent/Sources/RoomAgentCore/Report.swift	$(sed -nE 's/^public let roomAgentVersion = "([^"]+)"/\1/p' "$REPO_ROOT/agent/Sources/RoomAgentCore/Report.swift")"
}

# Succeeds iff every declared version equals $1 (or are all equal when $1 is empty).
check_versions() {
  local want="${1:-}" line file ver bad=0
  while IFS=$'\t' read -r file ver; do
    [[ -n "$ver" ]] || { warn "$file: no version found"; bad=1; continue; }
    [[ -z "$want" ]] && want="$ver"
    [[ "$ver" == "$want" ]] || { warn "$file: $ver != $want"; bad=1; }
  done < <(current_versions)
  return $bad
}

set_version() {
  local v="$1"
  is_semver "$v" || die "not a semantic version: $v"
  sed -i '' -E "s/^version = \"[^\"]+\"/version = \"$v\"/" "$REPO_ROOT/ctl/pyproject.toml"
  sed -i '' -E "s/^__version__ = \"[^\"]+\"/__version__ = \"$v\"/" "$REPO_ROOT/ctl/zoomctl/__init__.py"
  sed -i '' -E "s/^public let roomAgentVersion = \"[^\"]+\"/public let roomAgentVersion = \"$v\"/" \
    "$REPO_ROOT/agent/Sources/RoomAgentCore/Report.swift"
  check_versions "$v" || die "version bump did not apply cleanly"
}

# Newest-first semver comparison: succeeds iff $1 > $2.
version_gt() {
  [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" == "$1" ]]
}

# Print the single identity whose name starts with $1 ("Developer ID Application" / "…Installer").
# FIND_IDENTITY_OUTPUT can inject `security find-identity` output for tests.
find_identity() {
  local kind="$1" policy=codesigning out
  [[ "$kind" == *Installer* ]] && policy=basic
  out="${FIND_IDENTITY_OUTPUT-$(security find-identity -v -p "$policy" 2>/dev/null)}"
  local -a names=("${(@f)$(print -r -- "$out" | sed -nE "s/^ *[0-9]+\) [0-9A-F]{40} \"($kind: [^\"]+)\"\$/\1/p" | sort -u)}")
  names=(${names:#})
  (( ${#names} == 1 )) && { print -r -- "$names[1]"; return 0; }
  (( ${#names} > 1 )) && warn "multiple '$kind' identities; set the env override: ${(j:, :)names}"
  return 1
}

# Print notarytool credential args, preferring a keychain profile.
notary_args() {
  local profile="${NOTARY_PROFILE:-zoom-control-notary}"
  if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" && -z "${NOTARY_PROFILE:-}" ]]; then
    print -r -- "--apple-id $APPLE_ID --team-id $APPLE_TEAM_ID --password $APPLE_APP_PASSWORD"
  elif xcrun notarytool history --keychain-profile "$profile" >/dev/null 2>&1; then
    print -r -- "--keychain-profile $profile"
  else
    warn "notarytool keychain profile '$profile' not usable. Create it once with:"
    warn "  xcrun notarytool store-credentials $profile --apple-id <you@example.com> --team-id <TEAMID>"
    return 1
  fi
}
