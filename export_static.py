#!/usr/bin/env python3
"""Statik veri disa aktarici — bulut/baska makinede calisan panel icin.

data.json uretir: analiz + zaman serisi + canli firsatlar.
GitHub Actions icinde calisip commit edilirse, GitHub Pages paneli
bilgisayar kapaliyken de guncel kalir.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import analytics
import carry
import carry_health
import flow
import funding_monitor as fm
import health as health_mod
import journal
import paper
import risk
import screener
import vol
import xfund

ROOT = Path(__file__).parent
OUT = ROOT / "data.json"

# Tarama büyüklüğü ortamdan ayarlanabilir: GitHub Actions gibi paylaşımlı IP'lerde
# 150+ istek/35 sn Binance tarafından 429/418 ile karşılanabiliyor. CI daha küçük
# ve daha yavaş tarar; yerelde varsayılanlar kullanılır.
MIN_VOL = float(os.environ.get("SCAN_MIN_VOL", 50))
TOP = int(os.environ.get("SCAN_TOP", 40))
SMART_MIN_VOL = float(os.environ.get("SMART_MIN_VOL", 100))
SMART_TOP = int(os.environ.get("SMART_TOP", 12))
STAGE2 = int(os.environ.get("SCREEN_STAGE2", 40))
STAGE3 = int(os.environ.get("SCREEN_STAGE3", 12))
WORKERS = int(os.environ.get("SCAN_WORKERS", 2))


def build() -> dict:
    # 1) canli tarama + snapshot (bu GERCEK bir tarama, yazmasi dogru)
    df = fm.scan(min_ann=0.0, min_vol=MIN_VOL, top=TOP)
    rows = []
    if not df.empty:
        fm.save_snapshot(df)
        for _, r in df.iterrows():
            rows.append({
                "symbol": r["symbol"],
                "interval_h": int(r["interval_h"]),
                "funding_now_pct": round(float(r["funding_now_pct"]), 5),
                "ann_now_pct": round(float(r["ann_now_pct"]), 2),
                "ann_30d_pct": round(float(r["ann_30d_pct"]), 2),
                "ann_p25_pct": round(float(r["ann_p25_pct"]), 2),
                "pos_share_pct": round(float(r["pos_share_pct"]), 1),
                "basis_pct": round(float(r["basis_pct"]), 4),
                "vol_musd": round(float(r["vol_musd"]), 0),
                "breakeven_days": round(float(r["breakeven_days"]), 1)
                                  if r["breakeven_days"] != float("inf") else None,
                "ok": bool(r["funding_now_pct"] >= 0 and r["basis_pct"] < 0.3
                           and r["ann_p25_pct"] > 0),
            })

    # 2) yerel birikmis veriden analiz
    a = analytics.analyze()
    series = analytics.series_by_symbol(analytics.load())

    # 3) akilli para taramasi — statik panelde de calissin diye data.json'a girer
    #    save=True: sinyaller gecmise yazilir (ileride dogrulama icin)
    try:
        smart = flow.scan_smart_money(min_vol_musd=SMART_MIN_VOL, top=SMART_TOP,
                                      workers=WORKERS, save=True)
    except Exception as e:
        smart = {"rows": [], "ok": 0, "requested": 0, "failed": 0,
                 "breadth": {}, "fetched_at": None, "error": str(e)}

    # 4) AL/SAT hunisi — statik panelde TARAYICI sekmesi de dolsun
    try:
        s = screener.scan(min_vol=MIN_VOL / 2, stage2_n=STAGE2, stage3_n=STAGE3,
                          workers=WORKERS)
        screen = {**s, "alerts": screener.recent_alerts(30)}
    except Exception as e:
        screen = {"rows": [], "scanned_total": 0, "stage1": 0, "stage2": 0,
                  "elapsed_s": 0, "stats": {}, "model": None, "alerts": [],
                  "error": str(e)}

    # 5) carry pozisyonlari — statik/APK panelde KIRA sekmesi okunur (salt okuma).
    #    Pozisyon ekleme/silme sunucu ister; bulut surumu yalnizca gosterir.
    #
    #    GIZLILIK: data.json PUBLIC bir repoya gidiyor (telefon oradan okuyor).
    #    Varsayilan olarak USD tutarlari gizlenir; yuzdeler kalir. Tutarlarin da
    #    gorunmesini istersen CARRY_PUBLIC_AMOUNTS=1 (herkes gorebilir!).
    try:
        carry_rep = carry.report()
        if os.environ.get("CARRY_PUBLIC_AMOUNTS") != "1":
            carry_rep["amounts_hidden"] = True
            for p in carry_rep.get("positions", []):
                for k in ("notional_usd", "funding_usd", "spot_pnl", "perp_pnl", "net_usd"):
                    p[k] = None
            t = carry_rep.get("totals", {})
            for k in ("notional_usd", "funding_usd", "spot_pnl", "perp_pnl", "net_usd"):
                t[k] = None
    except Exception as e:
        carry_rep = {"available": False, "positions": [], "totals": {}, "error": str(e)}

    # 6) dip calismasi + kagit pozisyonlar (statik panelde salt okuma)
    #    Papier tutarlar da public dosyaya giriyor -> USD alanlari gizlenir.
    try:
        dip_rep = json.loads((ROOT / "reports" / "dip_study.json").read_text(encoding="utf-8"))
        dip_rep["available"] = True
    except Exception as e:
        dip_rep = {"available": False, "rows": [], "error": str(e)}
    try:
        paper_rep = paper.report(alert=False)
        paper_rep["static"] = True
        if os.environ.get("CARRY_PUBLIC_AMOUNTS") != "1":
            paper_rep["amounts_hidden"] = True
            for p in paper_rep.get("positions", []):
                for k in ("notional_usd", "pnl_usd", "closed_pnl_usd"):
                    p[k] = None
            for k in ("unrealized_usd", "realized_usd"):
                paper_rep[k] = None
    except Exception as e:
        paper_rep = {"positions": [], "open": 0, "closed": 0, "error": str(e)}

    # 6b) fiyat haritasi — telefonda pozisyon ac/kapa icin (tek istek, herkese acik fiyat)
    try:
        import urllib.request
        syms = {r.get("symbol") for r in (dip_rep.get("rows") or []) if r.get("symbol")}
        syms |= {r.get("symbol") for r in rows if r.get("symbol")}
        syms |= {x.get("symbol") for x in (paper_rep.get("positions") or []) if x.get("symbol")}
        with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/ticker/price",
                                    timeout=15) as r:
            live_prices = {t["symbol"]: float(t["price"]) for t in json.loads(r.read())}
        paper_rep["prices"] = {s: live_prices[s] for s in syms if s in live_prices}
        paper_rep["prices_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        paper_rep["prices"] = {}
        paper_rep["prices_error"] = str(e)


    # 7) risk & sağlık (public: USD tutarlar gizli, oranlar açık)
    try:
        rp = risk.panel()
        base = (rp.get("carry_notional") or 0) + (rp.get("paper_notional") or 0) or 1.0
        risk_rep = {"available": True, "risk_level": rp["risk_level"], "verdict": rp["verdict"],
                    "carry_positions": rp["carry_positions"],
                    "paper_positions": rp["paper_positions"],
                    "top_position_share_pct": rp["top_position_share_pct"],
                    "btc_eth_share_pct": rp["btc_eth_share_pct"],
                    "stress_pct": {k: round(v / base * 100, 2) for k, v in rp["stress"].items()},
                    "worst_case_pct": round(rp["worst_case_usd"] / base * 100, 2),
                    "amounts_hidden": True, "note": rp["net_delta_note"]}
    except Exception as e:
        risk_rep = {"available": False, "reason": str(e)}

    try:
        vol_rep = vol.all_regimes()
    except Exception as e:
        vol_rep = {"rows": [], "reason": str(e)}

    try:
        ch_rep = carry_health.all_health(20)
    except Exception as e:
        ch_rep = {"available": False, "rows": [], "reason": str(e)}

    try:
        xf_rep = xfund.compare()
    except Exception as e:
        xf_rep = {"rows": [], "reason": str(e)}

    try:
        jr_rep = journal.stats()
    except Exception as e:
        jr_rep = {"available": False, "note": str(e)}

    try:
        hs = health_mod.status()
        health_rep = {"ok": hs["ok"], "summary": hs["summary"],
                      "problems": hs["problems"],
                      "checks": [{"name": c["name"], "ok": c["ok"], "detail": c.get("detail", "")}
                                 for c in hs["checks"]
                                 if not c["name"].startswith("bulut verisi")]}
    except Exception as e:
        health_rep = {"ok": False, "summary": str(e), "problems": [str(e)], "checks": []}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "static",
        "analysis": a,
        "series": series,
        "live": {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "count": len(rows),
            "rows": rows,
            "cached": False,
        },
        "smart": smart,
        "screen": screen,
        "carry": carry_rep,
        "dip": dip_rep,
        "paper": paper_rep,
        "risk": risk_rep,
        "vol": vol_rep,
        "carry_health": ch_rep,
        "xfund": xf_rep,
        "journal": jr_rep,
        "health": health_rep,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ok = sum(1 for r in data["live"]["rows"] if r["ok"])
    print(f"data.json yazildi: {OUT}")
    print(f"  tarama: {data['analysis']['scans']} | sembol: {len(data['analysis']['symbols'])} "
          f"| uygun: {n_ok}/{data['live']['count']}")
    for ins in data["analysis"]["insights"]:
        print(f"  [{ins['level']}] {ins['text']}")
