#!/bin/bash
# APK üretimi: web varlıklarını paketle -> Capacitor sync -> gradle assembleDebug
set -e
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export ANDROID_HOME=/opt/android-sdk
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
cd "$(dirname "$0")"

echo "== 1) web varlıkları hazırlanıyor =="
rm -rf www && mkdir -p www/icons
cp ../dashboard.html www/index.html
python3 - <<'PY'
p = 'www/index.html'
s = open(p, encoding='utf-8').read()
tag = '<script src="local.js"></script>'
inject = '<script>window.FORCE_LOCAL=true;</script>\n'
# DIKKAT: 'FORCE_LOCAL' metni dashboard.html icinde zaten var (window.FORCE_LOCAL === true).
# Bu yuzden enjeksiyonun kendisini ariyoruz, kelimeyi degil.
if 'window.FORCE_LOCAL=true' not in s:
    assert tag in s, 'local.js script etiketi bulunamadi'
    s = s.replace(tag, inject + tag, 1)
    open(p, 'w', encoding='utf-8').write(s)
    print("FORCE_LOCAL enjekte edildi")
else:
    print("FORCE_LOCAL zaten var")
PY
cp ../data.json www/ 2>/dev/null && echo "data.json yedek anlık görüntü olarak eklendi" || true
cp ../local.js www/
cp ../manifest.json www/ 2>/dev/null || true
cp ../icons/*.png www/icons/
cp ../reports/learn.json www/learn.json
echo "www: $(ls www | tr '\n' ' ')"

echo "== 2) npm bağımlılıkları =="
[ -d node_modules ] || npm install --no-audit --no-fund --silent

echo "== 3) Android platformu =="
echo "sdk.dir=$ANDROID_HOME" > local.properties
[ -d android ] || npx --yes cap add android
npx --yes cap sync android

echo "== 4) uygulama ikonu =="
python3 - <<'PY'
from PIL import Image
from pathlib import Path
src = Image.open('../icons/icon-512.png').convert('RGBA')
sizes = {'mipmap-mdpi':48,'mipmap-hdpi':72,'mipmap-xhdpi':96,'mipmap-xxhdpi':144,'mipmap-xxxhdpi':192}
n = 0
for d, s in sizes.items():
    p = Path('android/app/src/main/res')/d/'ic_launcher.png'
    if p.parent.exists():
        src.resize((s, s), Image.LANCZOS).save(p); n += 1
        for alt in ('ic_launcher_round.png','ic_launcher_foreground.png'):
            q = p.parent/alt
            if q.exists():
                src.resize((s, s), Image.LANCZOS).save(q)
print(f"{n} yoğunluk için ikon yazıldı")
PY

echo "== 5) APK derleniyor =="
cd android
chmod +x gradlew
./gradlew assembleDebug --no-daemon --console=plain 2>&1 | tail -25

echo
echo "== SONUÇ =="
ls -lh app/build/outputs/apk/debug/*.apk
