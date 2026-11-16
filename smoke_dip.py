#!/usr/bin/env python3
"""Dip + kagit pozisyon + Telegram smoke testi (izole store, ag minimal).

Kanonik komut: .venv/bin/python smoke_dip.py

Kapsam:
  1) dip.py: dip_flags mantigi (7g aralik konumu, kosul) — sentetik veri, deterministik
  2) dip.py: verdict medyan tabanli (ortalamayi kuyruk sisirirse 'kuyruk_piyangosu')
  3) paper.py: ekle/degerlendir/kapat + validasyon + stop/hedef varsayilanlari
  4) notify.py: token yokken kuyruk, cooldown bastirma
  5) API: /api/dip, /api/paper, /api/paper/add|close, /api/telegram + 403 korumasi
  6) export_static: paper USD alanlari gizlenir
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/binance-bot")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
R: list[tuple[str, str, str]] = []


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


tmp = Path(tempfile.mkdtemp(prefix="hermes-verify-dip-"))
import dip
import notify
import paper

# --- 1) dip_flags ------------------------------------------------------
def t_flags():
    # 300 bar: fiyat 100'de sabit, sonra 200. barda dibe iner
    n = 300
    closes = [100.0] * n
    for i in range(170, 200):
        closes[i] = 100 - (i - 170) * 0.5          # yavas dusus
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
    })
    out = dip.dip_flags(df)
    assert "pos_pct" in out.columns and "dip" in out.columns
    assert out["pos_pct"].iloc[168:].notna().any(), "rolling konum hesaplanmadi"
    lows = out["pos_pct"].min()
    assert lows <= dip.LOW_PCT + 1e-6, f"dip konumu yakalanmadi: min {lows}"
    return f"min konum %{lows:.1f}, kosul sutunu uretildi"

# --- 2) verdict mantigi (medyan birincil) ------------------------------
def t_verdict():
    src = (ROOT / "dip.py").read_text(encoding="utf-8")
    assert 'out["verdict"] = "kuyruk_piyangosu"' in src, "kuyruk verdict dali yok"
    assert 'elif d24.get("mean", 0) > 0 and d24["median"] < 0:' in src, \
        "kuyruk kosulu (ortalama>0 ve medyan<0) yok"
    assert 'if d24["median"] > b24["median"] + 0.2' in src, "olumlu karar medyana bagli degil"
    d = json.loads((ROOT / "reports" / "dip_study.json").read_text(encoding="utf-8"))
    v, h = d["verdict"], d["horizons"]["h24"]["dip"]
    if v == "kuyruk_piyangosu":
        assert h["median"] < 0, "kuyruk verdict'i ama medyan negatif degil"
        assert h["mean"] > 0, "kuyruk verdict'i ama ortalama pozitif degil"
    assert d.get("verdict_text"), "verdict_text bos"
    return f"verdict={v} · medyan {h['median']:+.2f}% · ortalama {h['mean']:+.2f}% · metin dolu"

# --- 3) paper akisi ---------------------------------------------------
paper.STORE = tmp / "paper.json"
import flow
_orig_get = flow._get


def fake_get(url, **kw):
    if "klines" in url:
        return [{"x": 0}] * 0  # bos -> price None? asagida gercek fiyat kullanilacak
    return _orig_get(url, **kw)


def t_paper():
    # gercek fiyat gerekir (BTCUSDT vadeli) — ag yoksa atla
    px = paper.price("BTCUSDT")
    assert px and px > 0, "fiyat alinamadi (ag?)"
    pos = paper.add("btcusdt", 400, note="test")
    assert pos["symbol"] == "BTCUSDT" and pos["status"] == "open"
    assert pos["stop_price"] < pos["entry_price"] < pos["target_price"]
    for bad in [("NOPE", 100), ("BTCUSDT", 5), ("BTCUSDT", 400)]:
        try:
            paper.add(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"reddedilmeliydi: {bad}")
    rep = paper.report()
    assert rep["open"] == 1 and rep["positions"][0]["pnl_pct"] is not None
    closed = paper.close(pos["id"])
    assert closed and closed["status"] == "closed_manual"
    assert paper.report()["closed"] == 1
    return f"giris {pos['entry_price']:.2f} · stop {pos['stop_pct']}% · hedef {pos['target_pct']}% · kapatildi"

# --- 4) notify: kuyruk + cooldown -------------------------------------
def t_notify():
    os.environ["NOTIFY_DRY_RUN"] = "1"      # gercek Telegram'a mesaj gitmesin
    notify.OUTBOX = tmp / "outbox.jsonl"
    notify.STATE = tmp / "state.json"
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    st = notify.status()
    assert st["configured"] is False
    r1 = notify.send("birinci", key="k1", cooldown_min=60)
    assert r1["reason"] in ("dry_run", "no_credentials") and r1["queued"] is True, r1
    n1 = sum(1 for _ in notify.OUTBOX.open(encoding="utf-8"))
    r2 = notify.send("ikinci", key="k1", cooldown_min=60)
    assert r2["reason"] == "cooldown", r2
    n2 = sum(1 for _ in notify.OUTBOX.open(encoding="utf-8"))
    assert n1 == n2 == 1, (n1, n2)
    return "token yok -> kuyruk; ayni anahtar cooldown ile bastirildi"

# --- 5) API ------------------------------------------------------------
import dashboard


def t_api():
    dashboard.paper.STORE = tmp / "paper_api.json"
    c = dashboard.app.test_client()
    assert c.get("/api/dip").status_code == 200
    assert c.get("/api/paper").status_code == 200
    r = c.post("/api/paper/add", json={"symbol": "NOPE", "notional_usd": 100})
    assert r.status_code == 400, r.status_code
    r = c.post("/api/paper/add", json={"symbol": "ETHUSDT", "notional_usd": 100},
               environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
    assert r.status_code == 403, f"uzak yazma acik: {r.status_code}"
    r = c.post("/api/paper/add", json={"symbol": "ETHUSDT", "notional_usd": 300})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    pid = r.get_json()["position"]["id"]
    r = c.post("/api/paper/close", json={"id": pid})
    assert r.status_code == 200 and r.get_json()["paper"]["closed"] == 1
    assert c.post("/api/paper/close", json={"id": pid}).status_code == 404
    assert c.get("/api/telegram").get_json()["configured"] in (True, False)
    return "dip/paper uclari + 400/403/404 + ac/kapat"

# --- 6) statik gizlilik ----------------------------------------------
def t_privacy():
    import export_static as ex
    orig = (ex.fm.scan, ex.analytics.analyze, ex.analytics.series_by_symbol,
            ex.flow.scan_smart_money, ex.screener.scan, ex.screener.recent_alerts,
            ex.carry.report, ex.paper.report)
    ex.fm.scan = lambda **k: pd.DataFrame()
    ex.analytics.analyze = lambda: {"scans": 0, "symbols": [], "insights": []}
    ex.analytics.series_by_symbol = lambda *a, **k: {"labels": [], "series": []}
    ex.flow.scan_smart_money = lambda **k: {"rows": []}
    ex.screener.scan = lambda **k: {"rows": []}
    ex.screener.recent_alerts = lambda n=30: []
    ex.carry.report = lambda: {"available": True, "positions": [], "totals": {},
                              "measured": {}, "assumptions": []}
    ex.paper.report = lambda alert=False: {
        "positions": [{"id": "a", "symbol": "BTCUSDT", "notional_usd": 999.0,
                       "pnl_usd": 5.0, "pnl_pct": 0.5}],
        "open": 1, "closed": 0, "realized_usd": 3.0, "unrealized_usd": 5.0}
    try:
        os.environ.pop("CARRY_PUBLIC_AMOUNTS", None)
        d = ex.build()["paper"]
        p = d["positions"][0]
        assert p["notional_usd"] is None and p["pnl_usd"] is None, p
        assert d["unrealized_usd"] is None and d["realized_usd"] is None, d
        assert p["pnl_pct"] == 0.5, "yuzde korunmali"
    finally:
        (ex.fm.scan, ex.analytics.analyze, ex.analytics.series_by_symbol,
         ex.flow.scan_smart_money, ex.screener.scan, ex.screener.recent_alerts,
         ex.carry.report, ex.paper.report) = orig
    return "kagit pozisyon USD alanlari gizli, yuzdeler acik"


def t_cloud_path():
    """Bulut/APK yolu (renderCloudPayload) dip ve paper'i ciziyor mu?

    Bu kontrol, 'telefonda dip sekmesi bos' hatasinin tekrarini engeller:
    veri data.json'da olsa bile ilgili sekme render edilmezse bos gorunur.
    """
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    a = html.index("function renderCloudPayload")
    b = html.index("function refresh(", a)
    body = html[a:b]
    for need, why in [("d.dip", "dip"), ("renderDip", "dip çizimi"),
                      ("d.paper", "paper"), ("renderPaper", "paper çizimi")]:
        assert need in body, f"bulut yolunda {why} eksik ({need})"
    return "renderCloudPayload: dip + paper çiziliyor"


for name, fn in [("dip: kosul hesabi", t_flags),
                 ("dip: medyan tabanli karar", t_verdict),
                 ("paper: ekle/degerlendir/kapat", t_paper),
                 ("notify: kuyruk + cooldown", t_notify),
                 ("api: dip/paper/telegram uclari", t_api),
                 ("export_static: paper gizliligi", t_privacy),
                 ("bulut yolu: dip/paper render", t_cloud_path)]:
    check(name, fn)

print(f"dip/paper smoke — {datetime.now(timezone.utc).isoformat()}\n")
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
sys.exit(1 if fails else 0)
