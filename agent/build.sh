#!/bin/sh
# Builds the agent APK without Gradle: aapt2, javac, d8, apksigner.
# Needs JDK 17 and the Android SDK (platform 34, build-tools 34.0.0).
# Signs with $AGENT_KEYSTORE (default: private/agent.keystore, created if missing).
# Updates only install over an APK signed with the same key, so keep it.
set -eu
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
BT=$SDK/build-tools/34.0.0
JAR=$SDK/platforms/android-34/android.jar
KEYSTORE=${AGENT_KEYSTORE:-../private/agent.keystore}
OUT=build

rm -rf "$OUT" && mkdir -p "$OUT/classes"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$JAR" --manifest AndroidManifest.xml
javac --release 8 -Xlint:-options -classpath "$JAR" -d "$OUT/classes" $(find src -name '*.java')
"$BT/d8" --release --min-api 23 --lib "$JAR" --output "$OUT" $(find "$OUT/classes" -name '*.class')
(cd "$OUT" && zip -q base.apk classes.dex)
"$BT/zipalign" -f 4 "$OUT/base.apk" "$OUT/aligned.apk"

if [ ! -f "$KEYSTORE" ]; then
  mkdir -p "$(dirname "$KEYSTORE")"
  keytool -genkeypair -keystore "$KEYSTORE" -storepass reframe -alias agent -keyalg RSA -keysize 3072 \
    -validity 36500 -dname "CN=reframe agent" >/dev/null
  echo "Created signing key $KEYSTORE"
fi
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-pass pass:reframe --out "$OUT/reframe-agent.apk" "$OUT/aligned.apk"
echo "Built $(pwd)/$OUT/reframe-agent.apk"
