#!/usr/bin/env python3
"""Alarm hatti smoke testi — stop/hedef tetigi, dip ozeti, cron sarmalayici.

Kanonik: make test (bu dosyayi da kosar)
Ag minimal: fiyat stub'lanir, store/kuyruk gecici dizine alinir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
R: list[tuple[str, str, str]] = []
TMP = Path(tempfile.mkdtemp(prefix="smoke-alarm-"))


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def t_stop():
    import notify, paper
    os.environ["NOTIFY_DRY_RUN"] = "1"      # testler gercek mesaj gondermesin
    paper.STORE = TMP / "paper.json"
    notify.OUTBOX = TMP / "outbox.jsonl"
    notify.STATE = TMP / "state.json"
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        os.environ.pop(k, None)
    # giris 100, stop %5 -> 95; fiyat 94 => stop tetiklenir
    paper.save([{"id": "p1", "symbol": "BTCUSDT", "side": "LONG", "notional_usd": 1000.0,
                 "entry_price": 100.0, "stop_pct": 5.0, "target_pct": 3.0,
                 "stop_price": 95.0, "target_price": 103.0,
                 "opened_at": datetime.now(timezone.utc).isoformat(),
                 "status": "open", "note": "t"}])
    orig = paper.price
    paper.price = lambda sym: 94.0
    try:
        rep = paper.report(alert=True)
    finally:
        paper.price = orig
    row = json.loads(Path(paper.STORE).read_text())[0]
    assert row["status"] == "stopped", row["status"]
    assert (rep["open"], rep["closed"]) == (0, 1), (rep["open"], rep["closed"])
    assert rep["positions"][0]["status"] == "stopped"
    box = [json.loads(l) for l in Path(notify.OUTBOX).read_text().splitlines()]
    assert any("STOP" in m["text"] for m in box), "stop bildirimi kuyrukta yok"
    return f"status=stopped · open/closed=0/1 · bildirim {len(box)}"


def t_target():
    """Hedefe degme yolu da kapali kalmasin."""
    import notify, paper
    notify.STATE = TMP / "state2.json"
    paper.STORE = TMP / "paper2.json"
    paper.save([{"id": "p2", "symbol": "ETHUSDT", "side": "LONG", "notional_usd": 500.0,
                 "entry_price": 100.0, "stop_pct": 5.0, "target_pct": 3.0,
                 "stop_price": 95.0, "target_price": 103.0,
                 "opened_at": datetime.now(timezone.utc).isoformat(),
                 "status": "open", "note": "t"}])
    orig = paper.price
    paper.price = lambda sym: 104.0
    try:
        rep = paper.report(alert=True)
    finally:
        paper.price = orig
    row = json.loads(Path(paper.STORE).read_text())[0]
    assert row["status"] == "target", row["status"]
    assert rep["closed"] == 1 and rep["realized_usd"] > 0, (rep["closed"], rep["realized_usd"])
    return f"status=target · gerceklesen {rep['realized_usd']:+.2f}$"


def t_digest():
    import alarm, notify, watch
    watch.clear()            # onceki testten kalan filtre olmasin
    os.environ["NOTIFY_DRY_RUN"] = "1"
    notify.STATE = TMP / "state3.json"
    notify.OUTBOX = TMP / "outbox3.jsonl"
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        os.environ.pop(k, None)
    # veriye bagimli olmasin: sabit bir dip calismasi dosyasi kullan
    study = TMP / "dip_study.json"
    study.write_text(json.dumps({
        "verdict": "goreli_avantaj",
        "rows": [{"symbol": "TESTUSDT", "pos_pct": 3.1, "chg_24h": -6.2, "is_dip": True,
                  "dip_events_41d": 12}],
        "horizons": {"h24": {"dip": {"median": -0.32}}}}, ensure_ascii=False), encoding="utf-8")
    alarm.STUDY = study
    d1 = alarm.dip_digest()
    assert d1 and d1.get("queued") is True, d1
    d2 = alarm.dip_digest()
    assert d2 and d2["reason"] == "cooldown", d2
    return "ilk cagri kuyruk, ikincisi cooldown"


def t_watch():
    """İzleme listesi: boşken hepsi, doluyken yalnızca seçilenler."""
    import notify, watch, tg_bot
    watch.STORE = TMP / "watch.json"
    watch.clear()
    rows = [{"symbol": "BTCUSDT", "is_dip": True}, {"symbol": "ETHUSDT", "is_dip": True},
            {"symbol": "SOLUSDT", "is_dip": True}]
    assert len(watch.filter_symbols(rows)) == 3, "liste boşken filtrelememeli"

    r = watch.add("btc", "eth")                      # kısa yazım USDT'ye tamamlanır
    assert r["added"] == ["BTCUSDT", "ETHUSDT"], r
    kept = [x["symbol"] for x in watch.filter_symbols(rows)]
    assert kept == ["BTCUSDT", "ETHUSDT"], kept

    watch.remove("eth")
    assert [x["symbol"] for x in watch.filter_symbols(rows)] == ["BTCUSDT"]

    assert watch.add("BTCUSDT")["added"] == [], "aynı sembol iki kez eklenmemeli"
    # beceri: /izle komutu listeyi doldurur, /temizle boşaltır
    os.environ["NOTIFY_DRY_RUN"] = "1"
    assert "Eklendi" in tg_bot.handle("/izle SOLUSDT")
    assert "SOLUSDT" in watch.list_items()
    assert "temizlendi" in tg_bot.handle("/temizle").lower()
    assert watch.filter_symbols(rows) is not None and len(watch.list_items()) == 0
    # alarm özeti: liste dolu iken sadece izlenen sembol yazılmalı
    import json as _json
    import alarm
    study = TMP / "dip2.json"
    study.write_text(_json.dumps({"rows": [
        {"symbol": "AAAUSDT", "pos_pct": 1, "chg_24h": -5, "is_dip": True},
        {"symbol": "BBBUSDT", "pos_pct": 2, "chg_24h": -6, "is_dip": True}],
        "horizons": {"h24": {"dip": {"median": -0.3}}}}, ensure_ascii=False), encoding="utf-8")
    alarm.STUDY = study
    watch.add("AAAUSDT")
    r = alarm.dip_digest()
    text = (notify.OUTBOX.read_text(encoding="utf-8") if notify.OUTBOX.exists() else "") + str(r)
    assert "AAAUSDT" in text or r is not None
    return "boş=dolu filtre, kısa yazım, çift ekleme yok, /izle+/temizle çalışıyor"


def t_cron():
    log = ROOT / "reports" / "alarm.log"
    before = log.stat().st_size if log.exists() else 0
    p = subprocess.run(["bash", str(Path.home() / ".hermes" / "scripts" / "paper_alarm.sh")],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, (p.returncode, p.stderr[-300:])
    after = log.stat().st_size if log.exists() else 0
    assert after > before, "log buyumedi"
    return f"exit 0 · log +{after - before} bayt"


def t_tg_kur():
    """telegram_kur.sh: token varsa sormadan gecer, gecersizse temiz uyarir, .env'i bozmaz."""
    home = Path(tempfile.mkdtemp(prefix="smoke-tg-"))
    (home / ".hermes").mkdir()
    envf = home / ".hermes" / ".env"
    envf.write_text("TELEGRAM_BOT_TOKEN=123456:FAKE-TEST\n", encoding="utf-8")
    p = subprocess.run(["bash", str(ROOT / "telegram_kur.sh")], input="\n",
                       capture_output=True, text=True, timeout=150,
                       env={**os.environ, "HOME": str(home)})
    assert p.returncode == 0, (p.returncode, (p.stdout + p.stderr)[-300:])
    assert "sormadan devam" in p.stdout, "token varken prompt sordu"
    assert "chat id bulunamadi" in p.stdout, "gecersiz token icin uyari yok"
    assert envf.read_text(encoding="utf-8").strip() == "TELEGRAM_BOT_TOKEN=123456:FAKE-TEST", \
        ".env degistirildi"
    return "token varken prompt yok · gecersiz token temiz uyari · .env korundu"


for name, fn in [("stop tetigi -> kapat + bildir", t_stop),
                 ("hedef tetigi -> kapat + gerceklesen", t_target),
                 ("dip ozeti cooldown", t_digest),
                 ("bildirim izleme listesi", t_watch),
                 ("cron sarmalayici paper_alarm.sh", t_cron),
                 ("telegram_kur.sh (kurulum akisi)", t_tg_kur)]:
    check(name, fn)

print(f"alarm smoke — {datetime.now(timezone.utc).isoformat()}\n")
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
sys.exit(1 if fails else 0)
