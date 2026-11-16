#!/bin/bash
# Günlük yedek — data/ + reports/ + konfig (şifreler hariç).
# Kullanım: bash yedek.sh [--restore <arsiv> <hedef>]
set -u
ROOT=/root/binance-bot
DEST=${YEDEK_DIR:-/root/yedek}
KEEP=${YEDEK_KEEP:-7}
TS=$(date +%Y%m%d-%H%M)

if [ "${1:-}" = "--restore" ]; then
  ARC=${2:?arsiv yolu gerekli}
  OUT=${3:-/tmp/yedek-restore-$$}
  mkdir -p "$OUT"
  tar xzf "$ARC" -C "$OUT" && echo "geri yüklendi: $OUT" && ls "$OUT"
  exit $?
fi

mkdir -p "$DEST"
ARC="$DEST/funding-carry-$TS.tar.gz"

# Konum bazlı veriler (pozisyonlar, snapshotlar, günlük model) + raporlar
tar czf "$ARC" \
  -C "$ROOT" \
  --exclude='*.pyc' --exclude='__pycache__' \
  data reports README.md Makefile *.py 2>/dev/null

if [ ! -s "$ARC" ]; then echo "HATA: arsiv olusmadi"; exit 1; fi

# Bütünlük: arşiv okunabiliyor mu + kaç dosya
CNT=$(tar tzf "$ARC" | wc -l)
SIZE=$(du -h "$ARC" | cut -f1)

# Eski yedekleri temizle (son N)
ls -1t "$DEST"/funding-carry-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
done

echo "$(date '+%F %T') yedek: $ARC ($SIZE, $CNT dosya)"
