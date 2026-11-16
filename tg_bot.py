#!/usr/bin/env python3
"""Telegram komut botu — cron her dakika çağırır, biriken mesajları işler.

Komutlar:
  /izle BTCUSDT          → bildirim listesine ekle
  /birak BTCUSDT         → listeden çıkar
  /liste                 → listeyi göster
  /temizle               → listeyi boşalt (tüm bildirimler)
  /dur                   → Telegram bildirimlerini kapat
  /basla                 → aç
  /yardim                → komutlar

Durum: data/tg_offset.json (son işlenen update id)
Yanıt: notify üzerinden (ana şalter kapalıysa yalnızca kuyruğa yazar)
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import notify
import watch

ROOT = Path(__file__).parent
OFFSET = ROOT / "data" / "tg_offset.json"
API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str | None:
    tok, _ = notify.creds_raw()
    return tok


def _call(method: str, params: dict) -> dict:
    tok = _token()
    if not tok:
        return {"ok": False, "error": "token yok"}
    url = API.format(token=tok, method=method) + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _offset() -> int:
    try:
        return int(json.loads(OFFSET.read_text())["offset"])
    except Exception:
        return 0


def _save_offset(v: int) -> None:
    OFFSET.parent.mkdir(parents=True, exist_ok=True)
    OFFSET.write_text(json.dumps({"offset": v}), encoding="utf-8")


def _reply(text: str) -> None:
    notify.send(text, key=None, cooldown_min=0)


def handle(text: str) -> str:
    parts = (text or "").strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    args = parts[1:]

    if cmd in ("izle", "ekle", "watch"):
        if not args:
            return "Kullanım: /izle BTCUSDT (birden fazla: /izle BTC ETH)"
        r = watch.add(*args)
        if not r["added"]:
            return "Zaten listede ya da geçersiz: " + ", ".join(args)
        return ("Eklendi: " + ", ".join(r["added"]) +
                f"\nListe ({len(r['symbols'])}): " + ", ".join(r["symbols"]))

    if cmd in ("birak", "cikar", "sil"):
        if not args:
            return "Kullanım: /birak BTCUSDT"
        r = watch.remove(*args)
        if not r["removed"]:
            return "Listede yok: " + ", ".join(args)
        return ("Çıkarıldı: " + ", ".join(r["removed"]) +
                (f"\nKalan: " + ", ".join(r["symbols"]) if r["symbols"] else "\nListe boş — tüm bildirimler açık."))

    if cmd in ("liste", "list"):
        s = watch.status()
        if not s["symbols"]:
            return "Liste boş: tüm dipte olanlar özetlenir."
        return f"İzlenen ({s['count']}): " + ", ".join(s["symbols"])

    if cmd in ("temizle", "hepsi"):
        watch.clear()
        return "Liste temizlendi — tüm dipte olanlar özetlenecek."

    if cmd in ("dur", "kapat", "stop"):
        notify.set_enabled(False)
        return "Telegram bildirimleri KAPATILDI. Açmak için /basla"

    if cmd in ("basla", "ac", "start"):
        notify.set_enabled(True)
        return "Telegram bildirimleri AÇILDI."

    if cmd in ("yardim", "help", "komutlar"):
        return ("Komutlar:\n/izle BTCUSDT — bildirime ekle\n/birak BTCUSDT — çıkar\n"
                "/liste — listeyi göster\n/temizle — listeyi boşalt\n"
                "/dur — bildirimleri kapat\n/basla — aç")
    return ""


def main() -> int:
    if not _token():
        print("token yok")
        return 0
    off = _offset()
    res = _call("getUpdates", {"offset": off + 1, "timeout": 0, "limit": 20})
    if not res.get("ok"):
        print("getUpdates hata:", res.get("error"))
        return 0
    n = 0
    for upd in res.get("result") or []:
        off = max(off, int(upd.get("update_id") or 0))
        msg = upd.get("message") or {}
        text = msg.get("text") or ""
        if not text.startswith("/"):
            continue
        reply = handle(text)
        if reply:
            _reply("<b>Funding Carry</b>\n" + reply)
            n += 1
    _save_offset(off)
    print(f"işlendi: {n} komut · offset {off}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
