#!/usr/bin/env python3
"""Carry + panel smoke testi — degisen davranisi dogrudan surer, agi stub'lar.

Kanik test paketi yok (pytest kurulu degil), bu yuzden kanonik komut bu:
    .venv/bin/python smoke_carry.py

Kapsam:
  1) carry.py: bos store, ekleme, gecersiz girdiler, cift sembol
  2) carry.py: gercek funding matematigi (bagimsiz toplamla karsilastirma) + delta-notr
  3) dashboard.py: /api/carry uclari, 400/404, agdan yazma 403, ekle-sil dongusu
  4) export_static.py: gizlilik donusumu (USD null / yuzde acik / opt-in)
  5) dashboard.html: satir ici JS -> node --check
Her adim PASS/FAIL yazar; cikis kodu FAIL varsa 1. Isolated store: gercek veri korunur.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        RESULTS.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hermes-verify-carry-"))
    import carry

    # 1) depolama, izole store --------------------------------------------
    carry.STORE = tmp / "positions.json"

    def t_empty():
        r = carry.report()
        assert r["totals"]["count"] == 0, r["totals"]
        return "bos store -> count=0"

    def t_add():
        opened = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        pos = carry.add_position("btcusdt", 1000, opened, "test")
        assert pos["symbol"] == "BTCUSDT" and carry.load_positions()[0]["id"] == pos["id"]
        for bad in [("NOPE", 100, None), ("BTCUSDT", 5, None), ("ETHUSDT", 100, "2030-01-01")]:
            try:
                carry.add_position(*bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"kabul edilmemeliydi: {bad}")
        try:
            carry.add_position("BTCUSDT", 100, None)
        except ValueError as e:
            assert "zaten" in str(e), e
        else:
            raise AssertionError("cift sembol kabul edildi")
        return "ekleme + 4 gecersiz girdi reddedildi"

    # 2) funding matematigi + delta-notr ----------------------------------
    def t_math():
        import flow
        pos = carry.load_positions()[0]
        opened = datetime.fromisoformat(pos["opened_at"])
        rep = carry.position_report(pos, {})
        hist = flow._get(f"{flow.FAPI}/fapi/v1/fundingRate", symbol=pos["symbol"],
                         startTime=int(opened.timestamp() * 1000), limit=1000)
        assert hist, "funding gecmisi alinamadi (ag?)"
        want = sum(float(h["fundingRate"]) for h in hist) * pos["notional_usd"]
        assert abs(rep["funding_usd"] - want) < 0.02, (rep["funding_usd"], want)
        assert rep["funding_periods"] == len(hist), "periyot sayisi uyusmuyor"
        assert rep["delta_neutral"] is True, "delta-notr degil"
        legs = abs(rep["spot_pnl"] + rep["perp_pnl"])
        assert legs < pos["notional_usd"] * 0.01, f"bacaklar netlesmiyor: {legs}"
        assert abs(rep["net_usd"] - (rep["funding_usd"] + rep["spot_pnl"]
                                     + rep["perp_pnl"])) < 0.02
        return (f"kira {rep['funding_usd']:+.2f}$ = bagimsiz {want:+.2f}$ | "
                f"periyot {rep['funding_periods']} | bacak neti {legs:.2f}$")

    # 3) HTTP uclari -------------------------------------------------------
    import dashboard

    def t_api():
        c = dashboard.app.test_client()
        d = c.get("/api/carry").get_json()
        assert d["available"] is True and d["totals"]["count"] == 1, d.get("totals")
        assert c.post("/api/carry/add", json={"symbol": "NOPE", "notional_usd": 100}).status_code == 400
        assert c.post("/api/carry/remove", json={"id": "yokboyle"}).status_code == 404
        r = c.post("/api/carry/add", json={"symbol": "ETHUSDT", "notional_usd": 100},
                   environ_overrides={"REMOTE_ADDR": "10.1.2.3"})
        assert r.status_code == 403, f"uzak yazma engellenmedi: {r.status_code}"
        opened = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        r = c.post("/api/carry/add", json={"symbol": "ETHUSDT", "notional_usd": 250,
                                           "opened_at": opened})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        pid = r.get_json()["position"]["id"]
        assert r.get_json()["carry"]["totals"]["count"] == 2
        r = c.post("/api/carry/remove", json={"id": pid})
        assert r.status_code == 200 and r.get_json()["carry"]["totals"]["count"] == 1
        return "GET + 400/404/403 + ekle/sil dongusu"

    # 4) statik gizlilik donusumu -----------------------------------------
    def t_privacy():
        import pandas as pd
        import export_static as ex

        orig = (ex.fm.scan, ex.analytics.analyze, ex.analytics.series_by_symbol,
                ex.flow.scan_smart_money, ex.screener.scan, ex.screener.recent_alerts,
                ex.carry.report)
        ex.fm.scan = lambda **k: pd.DataFrame()
        ex.analytics.analyze = lambda: {"scans": 0, "symbols": [], "insights": []}
        ex.analytics.series_by_symbol = lambda *a, **k: {"labels": [], "series": []}
        ex.flow.scan_smart_money = lambda **k: {"rows": []}
        ex.screener.scan = lambda **k: {"rows": []}
        ex.screener.recent_alerts = lambda n=30: []
        ex.carry.report = lambda: {
            "available": True, "generated_at": "x", "measured": {}, "assumptions": [],
            "positions": [{"id": "a", "symbol": "BTCUSDT", "notional_usd": 1234.0,
                           "funding_usd": 5.0, "spot_pnl": -1.0, "perp_pnl": 2.0,
                           "net_usd": 6.0, "funding_pct": 0.4}],
            "totals": {"count": 1, "notional_usd": 1234.0, "funding_usd": 5.0,
                       "spot_pnl": -1.0, "perp_pnl": 2.0, "net_usd": 6.0,
                       "funding_pct": 0.4, "net_pct": 0.5},
        }
        try:
            os.environ.pop("CARRY_PUBLIC_AMOUNTS", None)
            d = ex.build()["carry"]
            assert d.get("amounts_hidden") is True, "gizlilik bayragi yok"
            p, t = d["positions"][0], d["totals"]
            for k in ("notional_usd", "funding_usd", "spot_pnl", "perp_pnl", "net_usd"):
                assert p[k] is None, f"pozisyon {k} sizmis: {p[k]}"
                assert t[k] is None, f"toplam {k} sizmis: {t[k]}"
            assert p["funding_pct"] == 0.4, "yuzde kaybolmamali"
            os.environ["CARRY_PUBLIC_AMOUNTS"] = "1"
            d2 = ex.build()["carry"]
            assert not d2.get("amounts_hidden"), "opt-in calismadi"
            assert d2["positions"][0]["notional_usd"] == 1234.0
        finally:
            os.environ.pop("CARRY_PUBLIC_AMOUNTS", None)
            (ex.fm.scan, ex.analytics.analyze, ex.analytics.series_by_symbol,
             ex.flow.scan_smart_money, ex.screener.scan, ex.screener.recent_alerts,
             ex.carry.report) = orig
        return "tutarlar gizli + yuzdeler acik; CARRY_PUBLIC_AMOUNTS=1 ile aciliyor"

    # 5) JS sozdizimi ------------------------------------------------------
    def t_js():
        html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
        assert blocks, "satir ici script bulunamadi"
        f = tmp / "check.mjs"
        for i, b in enumerate(blocks):
            f.write_text(b, encoding="utf-8")
            p = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            assert p.returncode == 0, f"blok {i}: {p.stderr.strip()[:300]}"
        assert "KİRA GELİRİ" in html and "function renderCarry" in html, "carry UI yok"
        return f"{len(blocks)} script blogu sozdizimi gecerli"

    for name, fn in [("carry: bos store", t_empty),
                     ("carry: ekleme + validasyon", t_add),
                     ("carry: funding matematigi + delta-notr", t_math),
                     ("api: /api/carry uclari + yazma korumasi", t_api),
                     ("export_static: gizlilik donusumu", t_privacy),
                     ("ui: dashboard.html JS sozdizimi", t_js)]:
        check(name, fn)

    print(f"smoke — {datetime.now(timezone.utc).isoformat()}\n")
    for status, name, detail in RESULTS:
        print(f"[{'OK  ' if status == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} gecti")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
