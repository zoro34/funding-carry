#!/bin/bash
# APK'yi telefondan indirmek icin yerel ag paylasimi (ayni WiFi).
# Kullanim: bash apk_paylas.sh [port]
set -u
PORT="${1:-8090}"
SHARE=/root/apk-share
mkdir -p "$SHARE"
# Yalnizca EN YENI APK paylasilir; eskiler silinir (yanlis dosyayi kurma riski).
NEWEST=$(ls -1t /root/binance-bot/FundingCarry-*.apk 2>/dev/null | head -1)
[ -n "${NEWEST:-}" ] || { echo "HATA: /root/binance-bot altinda APK yok"; exit 1; }
rm -f "$SHARE"/FundingCarry-*.apk
cp "$NEWEST" "$SHARE"/ && echo "paylasilan: $(basename "$NEWEST") ($(du -h "$NEWEST" | cut -f1))"

cd "$SHARE" || exit 1
OLD=$(ss -tlnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $0}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)
[ -n "${OLD:-}" ] && kill "$OLD" 2>/dev/null && sleep 1

setsid nohup python3 -m http.server "$PORT" --bind 0.0.0.0 > /tmp/apk_share.log 2>&1 &
sleep 2
IP=$(hostname -I | awk '{print $1}')
if ss -tlnp 2>/dev/null | grep -q ":$PORT"; then
  echo "Telefonda ac:  http://$IP:$PORT/"
else
  echo "HATA: paylasim baslamadi"; tail -5 /tmp/apk_share.log; exit 1
fi
