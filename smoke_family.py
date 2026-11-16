#!/usr/bin/env python3
"""Aile paneli smoke testi — kayıt, ajan raporu, ihlal, özet, API, gizlilik.

Kanonik: make test.  İzole store: gerçek çocuk kayıtlarına dokunmaz.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
R: list[tuple[str, str, str]] = []
TMP = Path(tempfile.mkdtemp(prefix="smoke-family-"))

os.environ["NOTIFY_DRY_RUN"] = "1"   # ihlal bildirimi gercek Telegram'a gitmesin
import family  # noqa: E402

family.STORE = TMP / "family.json"
family.REPORTS = TMP / "family_reports.jsonl"


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def t_add():
    c = family.add_child("Elif", 120)
    assert c["name"] == "Elif" and c["invite"] and c["paired"] is False
    assert c["rules"]["daily_limit_min"] == 120
    for bad in [("", None), ("Elif", None)]:
        try:
            family.add_child(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"reddedilmeliydi: {bad}")
    return f"eklendi (davet {c['invite'][:6]}…) · boş/çift isim reddedildi"


def t_ingest():
    c = family.load()["children"][0]
    tok = c["invite"]
    try:
        family.ingest({"token": "yanlis", "screen_minutes": 10})
    except ValueError:
        pass
    else:
        raise AssertionError("geçersiz token kabul edildi")
    res = family.ingest({"token": tok, "device": "Test Cihaz", "screen_minutes": 214,
                         "unlocks": 63, "battery": 41,
                         "apps": [{"pkg": "com.whatsapp", "minutes": 87},
                                  {"pkg": "com.mojang.minecraftpe", "minutes": 35}],
                         "location": {"lat": 41.0, "lon": 29.0, "accuracy_m": 10}})
    assert res["ok"] and res["child"] == "Elif"
    assert "screen_limit" in res["alerts"], res["alerts"]
    assert family.load()["children"][0]["paired"] is True
    return f"rapor alındı · ihlal: {res['alerts']}"


def t_blocked():
    c = family.load()["children"][0]
    family.set_rules(c["id"], blocked_apps=["com.mojang.minecraftpe"], app_limits={"com.whatsapp": 30})
    res = family.ingest({"token": c["invite"], "screen_minutes": 60,
                         "apps": [{"pkg": "com.mojang.minecraftpe", "minutes": 20},
                                  {"pkg": "com.whatsapp", "minutes": 45}]})
    kinds = res["alerts"]
    assert "blocked_app" in kinds, kinds
    assert any(k.startswith("app_limit") for k in kinds), kinds
    return f"kısıtlı uygulama + uygulama limiti yakalandı: {kinds}"


def t_notifications():
    c = family.load()["children"][0]
    res = family.ingest({"token": c["invite"], "screen_minutes": 20,
                         "notifications": [
                             {"pkg": "com.whatsapp", "title": "Anne", "text": "okuldan aldın mı?"},
                             {"pkg": "com.instagram.android", "title": "elif_k", "text": "gönderiyi beğendi"}]})
    assert res["ok"] is True
    s = family.summary()
    ns = s["children"][0]["recent_notifications"]
    assert any(n["pkg"] == "com.whatsapp" and "okuldan" in n["text"] for n in ns), ns
    return f"{len(ns)} bildirim önizlemesi özete girdi"


def t_summary():
    s = family.summary()
    c = s["children"][0]
    assert c["screen_today_min"] == 214, c["screen_today_min"]     # günün en yüksek değeri
    assert c["over_limit"] is True
    assert c["top_apps"][0]["pkg"] == "com.whatsapp", c["top_apps"]
    assert c["minutes_since_seen"] is not None
    assert s["totals"]["paired"] == 1
    assert "WhatsApp" in s["notice"], "şeffaflık notu yok"
    return f"{c['screen_today_h']} · limit {c['limit_min']}dk aşıldı · top {c['top_apps'][0]['pkg']}"


def t_api():
    import dashboard
    dashboard.family.STORE = family.STORE
    dashboard.family.REPORTS = family.REPORTS
    dashboard._family_cache["ts"] = 0.0
    cl = dashboard.app.test_client()
    assert cl.get("/api/family").status_code == 200
    r = cl.get("/api/family", environ_overrides={"REMOTE_ADDR": "10.5.5.5"})
    assert r.status_code == 403, f"aile paneli ağdan okunabiliyor: {r.status_code}"
    # ajan ucu token ile calisir (yerel kısıtı yok)
    tok = family.load()["children"][0]["invite"]
    r = cl.post("/api/family/report", json={"token": tok, "screen_minutes": 10},
                environ_overrides={"REMOTE_ADDR": "10.5.5.5"})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    r = cl.post("/api/family/report", json={"token": "kotu", "screen_minutes": 10})
    assert r.status_code == 403, r.status_code
    r = cl.post("/api/family/add", json={"name": "Elif"})
    assert r.status_code == 400, r.status_code
    inv = tok
    r = cl.get(f"/aile-davet?t={inv}")
    assert r.status_code == 200 and b"aile paneline" in r.data.lower()
    assert cl.get("/aile-davet?t=yok").status_code == 404
    return "okuma yerel · ajan raporu token ile · çift isim 400 · davet sayfası 200/404"


def t_privacy():
    src = (ROOT / "export_static.py").read_text(encoding="utf-8")
    assert '"family"' not in src and "family." not in src, \
        "aile verisi public data.json'a giriyor olabilir"
    return "export_static aile verisine dokunmuyor (konum/isim public olmuyor)"


for name, fn in [("aile: çocuk kaydı + doğrulama", t_add),
                 ("aile: ajan raporu + token kontrolü", t_ingest),
                 ("aile: kısıtlı uygulama + uygulama limiti", t_blocked),
                 ("aile: bildirim önizlemesi", t_notifications),
                 ("aile: günlük özet", t_summary),
                 ("aile: API uçları + davet sayfası", t_api),
                 ("aile: gizlilik (public data.json)", t_privacy)]:
    check(name, fn)

print(f"aile smoke — {datetime.now(timezone.utc).isoformat()}\n")
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
sys.exit(1 if fails else 0)
