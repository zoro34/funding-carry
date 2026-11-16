#!/bin/bash
# Telegram kurulumu — token'i EKRANA YAZMADAN ~/.hermes/.env'e ekler,
# chat id'yi botun gelen mesajlarindan otomatik bulur, test mesaji gonderir.
#
# Kullanim:  bash telegram_kur.sh
# Once Telegram'da @BotFather ile bot olustur ve botuna bir mesaj at.
set -u
ENVF="$HOME/.hermes/.env"
API="https://api.telegram.org"

echo "== Telegram kurulumu =="
echo "1) Telegram > @BotFather > /newbot > isim + kullanici adi (…bot ile bitsin)"
echo "2) BotFather'in verdigi token'i asagiya yapistir (ekranda gorunmez)"
echo "3) Bota Telegram'dan bir mesaj at (chat id icin), sonra Enter"
echo

read -rsp "Bot token: " TOKEN; echo
if [ -z "${TOKEN:-}" ]; then echo "HATA: token bos"; exit 1; fi
case "$TOKEN" in *:*) ;; *) echo "HATA: token '123456:ABC…' biciminde olmali"; exit 1;; esac

# gercek atama var mi, yoksa ekle (yorum satirlarini degistirme)
touch "$ENVF"; chmod 600 "$ENVF"
grep -qE "^\s*TELEGRAM_BOT_TOKEN=\S" "$ENVF" && \
  sed -i "s|^\s*TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TOKEN|" "$ENVF" || \
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" >> "$ENVF"
echo "token kaydedildi: $ENVF (izin 600)"

echo
read -rp "Bota Telegram'dan bir mesaj attin mi? (Enter)" _

# getUpdates ile chat id bul (son mesajin chat.id'si)
CHAT=$(curl -s -m 20 "$API/bot$TOKEN/getUpdates" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
    res=d.get("result") or []
    ids=[str(m["message"]["chat"]["id"]) for m in res if "message" in m]
    print(ids[-1] if ids else "")
except Exception:
    print("")')

if [ -z "$CHAT" ]; then
  echo "UYARI: chat id bulunamadi. Telegram'da botuna /start yaz, sonra bu betigi tekrar calistir."
else
  grep -qE "^\s*TELEGRAM_CHAT_ID=\S" "$ENVF" && \
    sed -i "s|^\s*TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=$CHAT|" "$ENVF" || \
    printf 'TELEGRAM_CHAT_ID=%s\n' "$CHAT" >> "$ENVF"
  echo "chat id kaydedildi: $CHAT"
fi

echo
echo "== test =="
cd /root/binance-bot 2>/dev/null && .venv/bin/python notify.py test || python3 notify.py test
