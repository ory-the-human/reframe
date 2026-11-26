#!/bin/sh
# Builds a release APK and pins it in src/reframe/frames/kiosk.py.
# Usage: release.sh [expected version]; fails if it isn't the manifest's versionName.
# Sign every release with the same key (private/agent.keystore, or $AGENT_KEYSTORE):
# frames only accept an update signed like the installed agent.
set -eu
cd "$(dirname "$0")"
VERSION=$(sed -n 's/.*android:versionName="\([^"]*\)".*/\1/p' AndroidManifest.xml)
[ "${1:-$VERSION}" = "$VERSION" ] || { echo "version $1 isn't versionName $VERSION" >&2; exit 1; }
./build.sh
APK=build/reframe-agent-$VERSION.apk
cp build/reframe-agent.apk "$APK"
SHA=$(shasum -a 256 "$APK" | cut -d' ' -f1)
URL=https://github.com/ory-the-human/reframe/releases/download/agent-v$VERSION/reframe-agent-$VERSION.apk
perl -pi -e "s|^AGENT_VERSION = .*|AGENT_VERSION = \"$VERSION\"|; s|^AGENT_APK_URL = .*|AGENT_APK_URL = \"$URL\"|; s|^AGENT_APK_SHA256 = .*|AGENT_APK_SHA256 = \"$SHA\"|" ../src/reframe/frames/kiosk.py
echo "Pinned $VERSION in kiosk.py; publish $(pwd)/$APK as release agent-v$VERSION"
echo "sha256 $SHA"
