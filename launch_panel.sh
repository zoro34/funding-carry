#!/bin/bash
# Paneli oturumdan bagimsiz baslat (kabuk kapansa da calismaya devam eder).
# Kullanim: bash launch_panel.sh [port]
set -u
cd /root/binance-bot || exit 1
PORT="${1:-8080}"
LOG=/root/binance-bot/reports/dashboard.log

# ayni portta calisan varsa durdur
OLD=$(ss -tlnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $0}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)
[ -n "${OLD:-}" ] && kill "$OLD" 2>/dev/null && sleep 1

setsid nohup .venv/bin/python dashboard.py --host 0.0.0.0 --port "$PORT" >> "$LOG" 2>&1 &
sleep 3
if ss -tlnp 2>/dev/null | grep -q ":$PORT"; then
  echo "panel calisiyor: http://127.0.0.1:$PORT  (log: $LOG)"
else
  echo "HATA: panel baslamadi — son loglar:"; tail -5 "$LOG"
  exit 1
fi
