#!/usr/bin/env zsh
# Sign, package, notarize and staple roomagent as an installer .pkg.
#
# Required env:
#   DEVELOPER_ID_APP        "Developer ID Application: Name (TEAMID)"
#   DEVELOPER_ID_INSTALLER  "Developer ID Installer: Name (TEAMID)"
#   NOTARY_PROFILE          keychain profile from `xcrun notarytool store-credentials`
#     — or — APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
# Optional: VERSION (default: roomagent --version), OUT (default: dist/)
#
# A stable Developer ID signature is what lets PPPC/TCC grants (Accessibility, Screen
# Recording, Camera, Microphone) survive upgrades — see docs/security.md.
set -euo pipefail
cd "${0:A:h}/.."
: "${DEVELOPER_ID_APP:?set DEVELOPER_ID_APP}"
: "${DEVELOPER_ID_INSTALLER:?set DEVELOPER_ID_INSTALLER}"

bin="$(./scripts/build.zsh release | tail -1)"
version="${VERSION:-$("$bin" --version)}"
out="${OUT:-dist}"
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
mkdir -p "$out" "$root/usr/local/bin"
cp "$bin" "$root/usr/local/bin/roomagent"

codesign --force --options runtime --timestamp --identifier us.zoomcontrol.roomagent \
  --sign "$DEVELOPER_ID_APP" "$root/usr/local/bin/roomagent"
codesign --verify --strict --verbose=2 "$root/usr/local/bin/roomagent"

pkg="$out/roomagent-$version.pkg"
pkgbuild --root "$root" --identifier us.zoomcontrol.roomagent --version "$version" \
  --install-location / --sign "$DEVELOPER_ID_INSTALLER" "$pkg"

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$pkg" --keychain-profile "$NOTARY_PROFILE" --wait
else
  : "${APPLE_ID:?set NOTARY_PROFILE or APPLE_ID}" "${APPLE_TEAM_ID:?}" "${APPLE_APP_PASSWORD:?}"
  xcrun notarytool submit "$pkg" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait
fi
xcrun stapler staple "$pkg"
xcrun stapler validate "$pkg"
spctl --assess --type install --verbose "$pkg"

# Code requirement for PPPC profiles (mdm/roomagent-pppc.mobileconfig).
codesign -dr - "$root/usr/local/bin/roomagent" 2>&1 | sed -n 's/^designated => //p' > "$out/roomagent-$version.requirement"
print "✓ notarized $pkg"
print "  code requirement: $(cat "$out/roomagent-$version.requirement")"
