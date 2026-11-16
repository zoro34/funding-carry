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

import alerts
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

    if cmd in ("alarm", "alarmkur"):
        # /alarm BTCUSDT > 70000   |  /alarm BTC > 70000  |  /alarm BTC %5
        raw = " ".join(args).replace(">=", ">").replace("<=", "<")
        if not raw:
            return ("Kullanım:\n/alarm BTCUSDT > 70000  (fiyat üstü)\n"
                    "/alarm BTC < 60000   (fiyat altı)\n/alarm ETH %5       (24s +%5)\n"
                    "/alarm ETH -%5      (24s -%5)\n/alarm SOL f0.05    (funding %0.05)")
        sym, rest = raw.split(" ", 1) if " " in raw else (raw, "")
        rest = rest.strip()
        try:
            if rest.startswith("> "):
                a = alerts.add(sym, "price_above", rest[2:])
            elif rest.startswith(">"):
                a = alerts.add(sym, "price_above", rest[1:])
            elif rest.startswith("< "):
                a = alerts.add(sym, "price_below", rest[2:])
            elif rest.startswith("<"):
                a = alerts.add(sym, "price_below", rest[1:])
            elif rest.startswith("-%"):
                a = alerts.add(sym, "pct_down", rest[2:])
            elif rest.startswith("%"):
                a = alerts.add(sym, "pct_up", rest[1:])
            elif rest.startswith("f"):
                a = alerts.add(sym, "funding_above", rest[1:])
            else:
                return "Koşul anlaşılmadı. Örnek: /alarm BTCUSDT > 70000"
        except ValueError as e:
            return f"Hata: {e}"
        return (f"Alarm kuruldu (#{a['id']}): {a['symbol']} · {alerts.KINDS[a['kind']]} "
                f"{a['value']:g}\nKapatmak için: /kapat {a['id']}")

    if cmd in ("alarmlar", "alarms"):
        rows = alerts.load()
        if not rows:
            return "Alarm yok. Kurmak için: /alarm BTCUSDT > 70000"
        out = ["Alarmlar:"]
        for r in rows:
            out.append(f"#{r['id']} {'✅' if r['enabled'] else '⛔'} {r['symbol']} "
                       f"{alerts.KINDS[r['kind']]} {r['value']:g}"
                       + (f" · tetik {r['trigger_count']}x" if r.get('trigger_count') else ""))
        out.append("Kapat/aç: /kapat <id> · sil: /sil <id>")
        return "\n".join(out)

    if cmd in ("kapat", "ac"):
        if not args:
            return "Kullanım: /kapat <id>  (yeniden açmak için aynı komut)"
        try:
            a = alerts.toggle(args[0])
        except ValueError as e:
            return f"Hata: {e}"
        return f"#{a['id']} {'AÇIK' if a['enabled'] else 'KAPALI'}"

    if cmd == "sil":
        if not args:
            return "Kullanım: /sil <id>"
        return "Silindi." if alerts.remove(args[0]) else "Alarm bulunamadı."

    if cmd in ("yardim", "help", "komutlar"):
        return ("Komutlar:\n/izle BTCUSDT — bildirime ekle\n/birak BTCUSDT — çıkar\n"
                "/liste — listeyi göster\n/temizle — listeyi boşalt\n"
                "/alarm BTCUSDT > 70000 — coin alarmı kur\n/alarmlar — alarmları göster\n"
                "/kapat <id> — alarmı aç/kapa\n/sil <id> — alarmı sil\n"
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
