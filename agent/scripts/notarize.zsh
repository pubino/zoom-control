#!/usr/bin/env zsh
# Build, sign, notarize (and staple where possible) roomagent for distribution.
#
# Output (in $OUT, default dist/):
#   roomagent-<v>.pkg           when a "Developer ID Installer" identity is available (preferred)
#   roomagent-<v>-macos.zip     otherwise: notarized, Developer ID-signed binary in a zip
#   roomagent-<v>.requirement   designated code requirement (paste into the PPPC profile)
#
# Identities are auto-detected from the login keychain; override with
#   DEVELOPER_ID_APP="Developer ID Application: Name (TEAMID)"
#   DEVELOPER_ID_INSTALLER="Developer ID Installer: Name (TEAMID)"   (or "none" to force zip)
# Notarization credentials (one of):
#   NOTARY_PROFILE  keychain profile from `xcrun notarytool store-credentials` (default: zoom-control-notary)
#   APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PASSWORD   (CI)
#
# A stable Developer ID signature is what lets TCC grants (Accessibility, Screen Recording,
# Camera, Microphone) survive upgrades — see docs/security.md.
set -euo pipefail
cd "${0:A:h}/.."
source "${0:A:h}/../../scripts/lib/release.zsh"

app_id="${DEVELOPER_ID_APP:-$(find_identity "Developer ID Application")}"
[[ -n "$app_id" ]] || die "no 'Developer ID Application' identity found (set DEVELOPER_ID_APP)"
inst_id="${DEVELOPER_ID_INSTALLER:-$(find_identity "Developer ID Installer" || true)}"
[[ "$inst_id" == none ]] && inst_id=""
notary=(${(z)$(notary_args)}) || die "no notarization credentials (see header)"

bin="$(./scripts/build.zsh release | tail -1)"
version="${VERSION:-$("$bin" --version)}"
out="${OUT:-dist}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$out" "$work/root/usr/local/bin"
staged="$work/root/usr/local/bin/roomagent"
cp "$bin" "$staged"

info "signing with $app_id"
codesign --force --options runtime --timestamp --identifier us.zoomcontrol.roomagent --sign "$app_id" "$staged"
codesign --verify --strict --verbose=2 "$staged"

if [[ -n "$inst_id" ]]; then
  artifact="$out/roomagent-$version.pkg"
  info "packaging $artifact (signed with $inst_id)"
  pkgbuild --root "$work/root" --identifier us.zoomcontrol.roomagent --version "$version" \
    --install-location / --sign "$inst_id" "$artifact"
  xcrun notarytool submit "$artifact" $notary --wait
  xcrun stapler staple "$artifact"
  xcrun stapler validate "$artifact"
  spctl --assess --type install --verbose "$artifact"
else
  warn "no 'Developer ID Installer' identity — shipping a notarized zip instead of a pkg"
  artifact="$out/roomagent-$version-macos.zip"
  (cd "$work/root/usr/local/bin" && ditto -c -k --keepParent roomagent "$work/roomagent.zip")
  xcrun notarytool submit "$work/roomagent.zip" $notary --wait
  # Bare Mach-O binaries cannot be stapled; Gatekeeper checks the ticket online.
  cp "$work/roomagent.zip" "$artifact"
  spctl --assess --type open --context context:primary-signature -vv "$staged" 2>&1 | tee "$work/spctl.log" || true
  grep -q "Notarized Developer ID" "$work/spctl.log" \
    || warn "spctl did not report 'Notarized Developer ID' yet (ticket propagation can take a minute)"
fi

codesign -dr - "$staged" 2>&1 | sed -n 's/^designated => //p' > "$out/roomagent-$version.requirement"
[[ -s "$out/roomagent-$version.requirement" ]] || die "could not extract code requirement"
ok "notarized $artifact"
print "  code requirement: $(cat "$out/roomagent-$version.requirement")"
