#!/bin/bash
# Android SDK kurulumu (cmdline-tools + platform 34 + build-tools 34).
set -e
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export ANDROID_HOME=/opt/android-sdk
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

echo "JAVA: $(javac -version 2>&1)"

mkdir -p "$ANDROID_HOME/cmdline-tools"
cd /tmp

if [ ! -x "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" ]; then
  echo "cmdline-tools indiriliyor..."
  ZIP=$(curl -s https://dl.google.com/android/repository/repository2-3.xml \
        | grep -o 'commandlinetools-linux-[0-9]*_latest\.zip' | head -1)
  [ -z "$ZIP" ] && ZIP=commandlinetools-linux-11076728_latest.zip
  echo "dosya: $ZIP"
  curl -fL --retry 3 -o cmdtools.zip "https://dl.google.com/android/repository/$ZIP"
  rm -rf "$ANDROID_HOME/cmdline-tools/cmdline-tools"
  unzip -q -o cmdtools.zip -d "$ANDROID_HOME/cmdline-tools"
  mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" 2>/dev/null || true
  rm -f cmdtools.zip
fi

echo "lisanslar kabul ediliyor..."
yes | sdkmanager --licenses >/dev/null 2>&1 || true

echo "paketler kuruluyor..."
sdkmanager --install "platform-tools" "platforms;android-34" "build-tools;34.0.0" 2>&1 | tail -5

echo "--- kurulu ---"
sdkmanager --list_installed 2>/dev/null | head -12
echo "ANDROID_HOME=$ANDROID_HOME"
