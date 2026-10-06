#!/bin/zsh
# Archives an iOS app and uploads it to App Store Connect from this Mac, signed the way
# BaltaJmn/ci ios-testflight-release.yml signs in CI: both identities from ~/keys in a temporary
# keychain, so neither a new development certificate nor cloud signing (ITMS-90035) gets in.
# For when Actions cannot run: no macOS runner available, or billing stops a private repo.
#
#   testflight.sh <repo dir> [version file]
#
# The version and build are the Android versionName and versionCode, like in CI. The version file
# defaults to androidApp/build.gradle.kts; FlowTime keeps them in
# build-logic/plugins/src/main/java/Config.kt.
set -euo pipefail
KEYS=~/keys
PREFIX=dev.baltajmn
secret() { security find-generic-password -a "$USER" -s "$PREFIX.$1" -w; }

[[ -d ${1:-} ]] || { sed -n '2,11p' $0; exit 1; }
# One run at a time, across sessions: each run swaps the user keychain search list.
if [[ -z ${TESTFLIGHT_LOCKED:-} ]]; then
  export TESTFLIGHT_LOCKED=1
  exec lockf -k /tmp/testflight.lock $0 "$@"
fi
cd "$1"
VERSION_FILE=${2:-androidApp/build.gradle.kts}
VERSION_NAME=$(sed -n -E 's/.*versionName *= *"([^"]+)".*/\1/p' "$VERSION_FILE" | head -1)
VERSION_CODE=$(sed -n -E 's/.*versionCode *= *([0-9]+).*/\1/p' "$VERSION_FILE" | head -1)
[[ -n $VERSION_NAME && -n $VERSION_CODE ]] || { print "Sin versionName o versionCode en $VERSION_FILE"; exit 1; }

WORK=$(mktemp -d)
KEYCHAIN=$WORK/signing.keychain-db
OLD_LIST=(${(f)"$(security list-keychains -d user | tr -d '" ')"})
trap 'security list-keychains -d user -s $OLD_LIST; security delete-keychain $KEYCHAIN 2>/dev/null; rm -rf $WORK' EXIT
KEYCHAIN_PASSWORD=$(uuidgen)
security create-keychain -p $KEYCHAIN_PASSWORD $KEYCHAIN
security set-keychain-settings -lut 21600 $KEYCHAIN
security unlock-keychain -p $KEYCHAIN_PASSWORD $KEYCHAIN
for kind in development distribution; do
  security import $KEYS/apple-$kind.p12 -k $KEYCHAIN -P "$(secret apple-$kind-p12)" -T /usr/bin/codesign > /dev/null
done
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k $KEYCHAIN_PASSWORD $KEYCHAIN > /dev/null
security list-keychains -d user -s $KEYCHAIN $OLD_LIST

TEAM_ID=$(secret apple-team-id)
AUTH=(-allowProvisioningUpdates -authenticationKeyPath $KEYS/appstore-api.p8
  -authenticationKeyID "$(secret appstore-key-id)" -authenticationKeyIssuerID "$(secret appstore-issuer-id)")

print "Archivando $VERSION_NAME ($VERSION_CODE) de $PWD..."
xcodebuild archive -project iosApp/iosApp.xcodeproj -scheme iosApp -configuration Release \
  -destination 'generic/platform=iOS' -archivePath $WORK/app.xcarchive $AUTH \
  TEAM_ID=$TEAM_ID MARKETING_VERSION=$VERSION_NAME CURRENT_PROJECT_VERSION=$VERSION_CODE \
  > $WORK/archive.log 2>&1 || { grep -E "error:|ARCHIVE FAILED" $WORK/archive.log | head -20; exit 1; }

cat > $WORK/ExportOptions.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$TEAM_ID</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict>
</plist>
PLIST

print "Subiendo..."
xcodebuild -exportArchive -archivePath $WORK/app.xcarchive -exportPath $WORK/export \
  -exportOptionsPlist $WORK/ExportOptions.plist $AUTH > $WORK/export.log 2>&1 ||
  { grep -E "error|90[0-9]{3}|FAILED" $WORK/export.log | cut -c1-400 | head -20; exit 1; }
grep -q "Upload succeeded" $WORK/export.log && print "Subida $VERSION_NAME ($VERSION_CODE): App Store Connect la está procesando."
