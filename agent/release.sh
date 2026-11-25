#!/bin/sh
# Builds a release APK and prints what to pin in src/cozysync/frames/kiosk.py.
# Sign every release with the same key (private/agent.keystore, or $AGENT_KEYSTORE):
# frames only accept an update signed like the installed agent.
set -eu
cd "$(dirname "$0")"
VERSION=$(sed -n 's/.*android:versionName="\([^"]*\)".*/\1/p' AndroidManifest.xml)
./build.sh
cp build/cozysync-agent.apk "build/cozysync-agent-$VERSION.apk"
echo
echo "Upload build/cozysync-agent-$VERSION.apk as a release asset, then pin in kiosk.py:"
echo "AGENT_VERSION = \"$VERSION\""
echo "AGENT_APK_URL = \"<the asset's download URL>\""
echo "AGENT_APK_SHA256 = \"$(shasum -a 256 "build/cozysync-agent-$VERSION.apk" | cut -d' ' -f1)\""
