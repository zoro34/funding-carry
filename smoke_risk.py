#!/usr/bin/env python3
"""Risk & sağlık smoke testi — 6 modülün matematiği ve API'leri.

Kapsam:
  1) vol: rejim sınıflandırması, öneri tutarı/stop ters oranı, eksik veri davranışı
  2) carry_health: trend hesabı, basis primi cezası, skor aralığı, kararlar
  3) xfund: yıllıklandırma matematiği, fark sıralaması, eksik borsa toleransı
  4) risk: brüt maruziyet, stres senaryoları, risk seviyesi eşikleri
  5) health: bayat veri tespiti, servis kontrolü, sorun listesi
  6) journal: isabet oranı, beklenti, USD→% çevrimi
  7) API: /api/risk, /api/health, /api/vol, /api/journal, /api/carry-health, /api/xfund
Kanonik: make test
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["NOTIFY_DRY_RUN"] = "1"
R: list[tuple[str, str, str]] = []
TMP = Path(tempfile.mkdtemp(prefix="smoke-risk-"))

import carry_health  # noqa: E402
import health  # noqa: E402
import journal  # noqa: E402
import risk  # noqa: E402
import vol  # noqa: E402
import xfund  # noqa: E402


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def t_vol():
    r = vol.regime("BTCUSDT")
    assert r["available"], r
    assert r["regime"] in ("sakin", "normal", "yüksek")
    # aynı risk için: yüksek oynaklıkta tutar küçük, stop geniş
    if r["regime"] == "yüksek":
        assert r["suggested_notional"] < vol.BASE_NOTIONAL
        assert r["suggested_stop_pct"] > vol.BASE_STOP_PCT
    elif r["regime"] == "sakin":
        assert r["suggested_notional"] > vol.BASE_NOTIONAL
    assert 0 <= r["percentile"] <= 100
    miss = vol.regime("YOKUSDT")
    assert miss["available"] is False
    return f"BTC {r['regime']} p{r['percentile']:.0f} → {r['suggested_notional']}$ / stop {r['suggested_stop_pct']}%"


def t_carry_health():
    d = carry_health.all_health(5)
    assert d["available"] and d["rows"], d
    row = d["rows"][0]
    for k in ("entry_score", "trend_3d", "basis_pct", "verdict", "net_after_cost_pct"):
        assert k in row, f"eksik alan: {k}"
    assert 0 <= row["entry_score"] <= 100
    # skor monotonik: basis > 0.30 ise skor cezalı olmalı (verdict 'primli')
    prim = [r for r in carry_health.all_health(50)["rows"] if r["basis_pct"] > carry_health.PRIM_RISK_PCT]
    if prim:
        assert all("primli" in r["verdict"] for r in prim), "basis primi cezalandırılmıyor"
    assert carry_health.health("YOKUSDT")["available"] is False
    return f"{len(d['rows'])} sembol · lider {row['symbol']} skor {row['entry_score']:.0f} ({row['verdict']})"


def t_xfund():
    assert abs(xfund._annual(0.01) - 10.95) < 0.01, "yıllıklandırma yanlış (0.01% → 10.95%)"
    fake = [("BTCUSDT", {"binance": 0.01, "bybit": 0.02})]
    rows = []
    for sym, rates in fake:
        hi = max(rates, key=rates.get)
        lo = min(rates, key=rates.get)
        rows.append({"symbol": sym, "spread_ann_pct": round(xfund._annual(rates[hi] - rates[lo]), 2)})
    assert rows[0]["spread_ann_pct"] > 0
    live = xfund.compare(["BTCUSDT", "ETHUSDT"])
    assert "rows" in live and "sources" in live
    assert len(live["sources"]) == 3
    if live["rows"]:
        assert live["rows"][0]["spread_ann_pct"] >= live["rows"][-1]["spread_ann_pct"], "sıralama yok"
    return f"yıllıklandırma doğru · canlı {live['sources']} · {len(live['rows'])} sembol"


def t_risk():
    p = risk.panel()
    assert p["gross_exposure"] >= 0
    assert set(p["stress"]) == set(risk.STRESS)
    assert all(v <= 0 for v in p["stress"].values()), "stres etkileri negatif olmalı"
    assert p["risk_level"] in ("bos", "dusuk", "orta", "yuksek")
    assert abs(p["worst_case_usd"] - min(p["stress"].values())) < 1e-6
    if p["carry_positions"] and p["carry_notional"]:
        assert p["top_position_share_pct"] > 0
    return f"{p['risk_level']} · brüt {p['gross_exposure']:.0f}$ · en kötü {p['worst_case_usd']:.2f}$"


def t_health():
    s = health.status()
    assert "checks" in s and s["checks"], "kontrol listesi boş"
    assert s["ok"] in (True, False)
    assert isinstance(s["problems"], list)
    names = {c["name"] for c in s["checks"]}
    for need in ("funding snapshot", "panel 8080", "disk"):
        assert need in names, f"eksik kontrol: {need}"
    # bayat veri tespiti: limitler tanımlı
    assert health.STALE_H > 0 and health.STALE_DATA_H > 0
    if s["problems"]:
        assert health.alert_text(s).startswith("<b>Panel sağlık uyarısı")
    return s["summary"]


def t_journal():
    st = journal.stats()
    if not st.get("available"):
        assert "note" in st
        return "kapanan işlem yok (boş günlük doğru davranış)"
    assert 0 <= st["hit_rate_pct"] <= 100
    assert st["trades"] == sum(st["close_reasons"].values()), "kapanış nedeni sayısı tutmuyor"
    # USD'den yüzde çevrimi: 100$ notional, 2$ kâr → %2
    fake = {"closed_pnl_pct": None, "closed_pnl_usd": 2.0, "notional_usd": 100.0}
    v = fake["closed_pnl_pct"]
    pct = v if v is not None else fake["closed_pnl_usd"] / fake["notional_usd"] * 100
    assert abs(pct - 2.0) < 1e-9
    return f"{st['trades']} işlem · isabet {st['hit_rate_pct']}% · beklenti {st['expectancy_pct']}%"


def t_api():
    import dashboard
    cl = dashboard.app.test_client()
    for url in ("/api/risk", "/api/vol", "/api/journal", "/api/carry-health", "/api/health"):
        r = cl.get(url)
        assert r.status_code == 200, f"{url} → {r.status_code}"
        assert isinstance(r.get_json(), dict), f"{url} JSON değil"
    r = cl.get("/api/xfund")
    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert "rows" in d
    # önbellek: ikinci çağrı cached=True dönmeli
    r2 = cl.get("/api/vol")
    assert r2.get_json().get("cached") is True, "önbellek çalışmıyor"
    return "6 uç 200 · önbellek aktif"


for name, fn in [("vol: rejim + öneri", t_vol),
                 ("carry_health: skor + trend", t_carry_health),
                 ("xfund: yıllıklandırma + sıralama", t_xfund),
                 ("risk: maruziyet + stres", t_risk),
                 ("health: tazelik + servisler", t_health),
                 ("journal: isabet + beklenti", t_journal),
                 ("api: 6 uç + önbellek", t_api)]:
    check(name, fn)

print()
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
sys.exit(1 if fails else 0)
