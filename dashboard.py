#!/usr/bin/env python3
"""Funding carry dashboard — Flask API + tek sayfa arayuz.

Calistir:
    .venv/bin/python dashboard.py            # http://127.0.0.1:8080
    .venv/bin/python dashboard.py --host 0.0.0.0 --port 8080

Uclar:
    GET /                arayuz
    GET /api/analysis    birikmis veriden analiz + cikarimlar
    GET /api/live        canli tarama (60 sn onbellek)
    GET /api/series      grafik icin zaman serisi
    GET /api/all         hepsi tek istekte (arayuz bunu kullaniyor)
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import analytics
import carry
import carry_health
import flow
import funding_monitor as fm
import health
import journal
import notify
import paper
import risk
import vol
import xfund
import screener

ROOT = Path(__file__).parent
app = Flask(__name__, static_folder=None)

_live_cache: dict = {"ts": 0.0, "data": None}
_smart_cache: dict = {"ts": 0.0, "data": None}
_screen_cache: dict = {"ts": 0.0, "data": None}
LIVE_TTL = 60      # saniye
SMART_TTL = 120    # saniye — akilli para taramasi ~10-25 sn surer
SCREEN_TTL = 180   # saniye — huni taramasi ~12-30 sn surer


def live_data(save: bool = False) -> dict:
    now = time.time()
    if _live_cache["data"] is not None and now - _live_cache["ts"] < LIVE_TTL:
        return _live_cache["data"]

    df = fm.scan(min_ann=0.0, min_vol=50.0, top=40)
    rows = []
    if not df.empty:
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
        # ONEMLI: goruntuleme snapshot YAZMAZ. Aksi halde sayfayi her yenilemek
        # "tarama sayisini" sisirir ve zaman serisi anlamsizlasir.
        # Snapshot sadece gercek taramalarda (cron / 'scan' komutu) yazilir.
        if save:
            fm.save_snapshot(df)

    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "count": len(rows),
        "rows": rows,
    }
    _live_cache.update({"ts": now, "data": out})
    return out


def smart_data(force: bool = False, top: int = 12) -> dict:
    """Akilli para taramasi (vadeli akis + OI + buyuk oyuncu + balina izi)."""
    now = time.time()
    c = _smart_cache
    if not force and c["data"] is not None and now - c["ts"] < SMART_TTL:
        out = dict(c["data"])
        out["cached"] = True
        return out

    res = flow.scan_smart_money(min_vol_musd=100.0, top=top, workers=2)
    res["cached"] = False
    res["ttl"] = SMART_TTL
    if res.get("rows"):
        c.update({"ts": now, "data": res})
    return res


@app.get("/")
def index():
    return send_from_directory(ROOT, "dashboard.html")


@app.get("/manifest.json")
def manifest():
    return send_from_directory(ROOT, "manifest.json", mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # SW kapsamı kök olmalı; ayrıca önbelleğe alınmamalı
    r = send_from_directory(ROOT, "sw.js", mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/icons/<path:name>")
def icons(name):
    return send_from_directory(ROOT / "icons", name)


@app.get("/local.js")
def local_js():
    return send_from_directory(ROOT, "local.js", mimetype="application/javascript")


@app.get("/learn.json")
def learn_json():
    return send_from_directory(ROOT / "reports", "learn.json", mimetype="application/json")


@app.get("/api/analysis")
def api_analysis():
    return jsonify(analytics.analyze())


@app.get("/api/live")
def api_live():
    return jsonify(live_data())


@app.get("/api/series")
def api_series():
    return jsonify(analytics.series_by_symbol(analytics.load()))


@app.get("/api/symbol/<sym>")
def api_symbol(sym: str):
    """Tek sembol detayi (tiklama ile cagrilir, onbelleksiz)."""
    sym = sym.upper()
    if not sym.endswith(("USDT", "USDC", "BUSD")):
        return jsonify({"error": "gecersiz sembol"}), 400
    try:
        return jsonify(flow.symbol_detail(sym))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/smart")
def api_smart():
    return jsonify(smart_data())


@app.get("/api/smart/refresh")
def api_smart_refresh():
    return jsonify(smart_data(force=True))


def screen_data(force: bool = False) -> dict:
    """Tum sembol huni taramasi: AL / SAT / BEKLE + alarm listesi."""
    now = time.time()
    c = _screen_cache
    if not force and c["data"] is not None and now - c["ts"] < SCREEN_TTL:
        out = dict(c["data"])
        out["cached"] = True
        return out

    res = screener.scan(min_vol=20.0, stage2_n=60, stage3_n=15, workers=2)
    res["cached"] = False
    res["alerts"] = screener.recent_alerts(30)
    res["min_conditions"] = screener.MIN_CONDITIONS
    if res.get("rows"):
        c.update({"ts": now, "data": res})
    return res


@app.get("/api/screener")
def api_screener():
    return jsonify(screen_data())


@app.get("/api/screener/refresh")
def api_screener_refresh():
    """Taze tarama + yeni alarmlari yaz."""
    res = screen_data(force=True)
    n = screener.write_alerts(res.get("rows", []))
    res["new_alerts"] = n
    res["alerts"] = screener.recent_alerts(30)
    return jsonify(res)


@app.get("/api/model")
def api_model():
    """Ogrenilmis modelin ozeti + walk-forward olcumleri (learn.json)."""
    p = ROOT / "reports" / "learn.json"
    if not p.exists():
        return jsonify({"available": False, "hint": "python learn.py calistirilmadi"})
    try:
        import json
        d = json.loads(p.read_text(encoding="utf-8"))
        d["available"] = True
        return jsonify(d)
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@app.get("/api/rulecheck")
def api_rulecheck():
    """Kuralin gecmis veride olculen performansi (rule_backtest.json)."""
    import json
    p = ROOT / "reports" / "rule_backtest.json"
    if not p.exists():
        return jsonify({"available": False,
                        "hint": "python rule_backtest.py calistirilmadi"})
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d["available"] = True
        return jsonify(d)
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@app.get("/api/alerts")
def api_alerts():
    return jsonify({"alerts": screener.recent_alerts(60)})


# ------------------------------------------------------- carry pozisyonlari
_carry_cache: dict = {"ts": 0.0, "data": None}
CARRY_TTL = 60


def carry_data(force: bool = False) -> dict:
    now = time.time()
    if not force and _carry_cache["data"] is not None and now - _carry_cache["ts"] < CARRY_TTL:
        out = dict(_carry_cache["data"])
        out["cached"] = True
        return out
    out = carry.report()
    out["cached"] = False
    _carry_cache.update({"ts": now, "data": out})
    return out


def _write_allowed() -> bool:
    """Pozisyon yazma sadece yerel makineden. Sunucu 0.0.0.0'da dinlese bile
    agdan gelen biri pozisyon ekleyip silemesin."""
    if os.environ.get("CARRY_ALLOW_REMOTE") == "1":
        return True
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


@app.get("/api/carry")
def api_carry():
    return jsonify(carry_data())


@app.get("/api/carry/refresh")
def api_carry_refresh():
    return jsonify(carry_data(force=True))


@app.post("/api/carry/add")
def api_carry_add():
    if not _write_allowed():
        return jsonify({"error": "pozisyon yazma sadece yerel makineden"}), 403
    d = request.get_json(silent=True) or {}
    try:
        pos = carry.add_position(
            symbol=d.get("symbol", ""),
            notional_usd=d.get("notional_usd", 0),
            opened_at=d.get("opened_at") or None,
            note=d.get("note", ""),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _carry_cache["ts"] = 0.0
    return jsonify({"ok": True, "position": pos, "carry": carry_data(force=True)})


@app.post("/api/carry/remove")
def api_carry_remove():
    if not _write_allowed():
        return jsonify({"error": "pozisyon yazma sadece yerel makineden"}), 403
    d = request.get_json(silent=True) or {}
    pid = (d.get("id") or "").strip()
    if not pid or not carry.remove_position(pid):
        return jsonify({"error": "pozisyon bulunamadi"}), 404
    _carry_cache["ts"] = 0.0
    return jsonify({"ok": True, "carry": carry_data(force=True)})


# ------------------------------------------------- dip + kagit pozisyonlar
_dip_cache: dict = {"ts": 0.0, "data": None}
DIP_TTL = 600          # 10 dk — calisma agir (20 sembol x 1 istek)


def dip_data(force: bool = False) -> dict:
    now_ts = time.time()
    if not force and _dip_cache["data"] is not None and now_ts - _dip_cache["ts"] < DIP_TTL:
        out = dict(_dip_cache["data"])
        out["cached"] = True
        return out
    p = ROOT / "reports" / "dip_study.json"
    out = {"available": False, "rows": [], "verdict_text": "ölçüm yok — python dip.py"}
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out = {**d, "available": True}
        except Exception as e:  # noqa: BLE001
            out = {"available": False, "rows": [], "error": str(e)}
    out["cached"] = False
    _dip_cache.update({"ts": now_ts, "data": out})
    return out


_paper_cache: dict = {"ts": 0.0, "data": None}
PAPER_TTL = 30


def paper_data(force: bool = False, alert: bool = False) -> dict:
    now_ts = time.time()
    if not force and not alert and _paper_cache["data"] is not None \
            and now_ts - _paper_cache["ts"] < PAPER_TTL:
        out = dict(_paper_cache["data"])
        out["cached"] = True
        return out
    out = paper.report(alert=alert)
    out["cached"] = False
    _paper_cache.update({"ts": now_ts, "data": out})
    return out


@app.get("/api/dip")
def api_dip():
    return jsonify(dip_data())


@app.get("/api/dip/refresh")
def api_dip_refresh():
    return jsonify(dip_data(force=True))


@app.get("/api/paper")
def api_paper():
    return jsonify(paper_data(alert=True))


@app.post("/api/paper/add")
def api_paper_add():
    if not _write_allowed():
        return jsonify({"error": "pozisyon yazma sadece yerel makineden"}), 403
    d = request.get_json(silent=True) or {}
    try:
        pos = paper.add(d.get("symbol", ""), d.get("notional_usd", 0),
                        d.get("stop_pct") or None, d.get("target_pct") or None,
                        d.get("note", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "position": pos, "paper": paper_data(force=True)})


@app.post("/api/paper/close")
def api_paper_close():
    if not _write_allowed():
        return jsonify({"error": "pozisyon yazma sadece yerel makineden"}), 403
    pid = ((request.get_json(silent=True) or {}).get("id") or "").strip()
    try:
        pos = paper.close(pid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not pos:
        return jsonify({"error": "açık pozisyon bulunamadı"}), 404
    return jsonify({"ok": True, "position": pos, "paper": paper_data(force=True)})


@app.get("/api/telegram")
def api_telegram():
    return jsonify(notify.status())



# --------------------------------------------------------- risk & sağlık
_rcache: dict = {}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _rcache.get(key)
    if hit and now - hit[0] < ttl:
        out = dict(hit[1])
        out["cached"] = True
        return out
    out = fn()
    out["cached"] = False
    _rcache[key] = (now, out)
    return out


@app.get("/api/risk")
def api_risk():
    return jsonify(_cached("risk", 15, risk.panel))


@app.get("/api/health")
def api_health():
    return jsonify(_cached("health", 30, health.status))


@app.get("/api/vol")
def api_vol():
    return jsonify(_cached("vol", 300, vol.all_regimes))


@app.get("/api/journal")
def api_journal():
    return jsonify(_cached("journal", 30, journal.stats))


@app.get("/api/carry-health")
def api_carry_health():
    return jsonify(_cached("carry_health", 60, lambda: carry_health.all_health(20)))


@app.get("/api/xfund")
def api_xfund():
    return jsonify(_cached("xfund", 600, xfund.compare))


@app.post("/api/telegram/toggle")
def api_telegram_toggle():
    if not _write_allowed():
        return jsonify({"error": "sadece yerel makineden"}), 403
    d = request.get_json(silent=True) or {}
    on = bool(d.get("on", True))
    if not notify.set_enabled(on):
        return jsonify({"error": ".env bulunamadı"}), 500
    return jsonify({"ok": True, "enabled": on, "telegram": notify.status()})


@app.post("/api/telegram/test")
def api_telegram_test():
    if not _write_allowed():
        return jsonify({"error": "sadece yerel makineden"}), 403
    return jsonify({**notify.send_test(), "status": notify.status()})


@app.get("/api/all")
def api_all():
    """HIZLI uc: sadece yerel veriden analiz. Canli tarama YAPMAZ.

    Sayfa once bunu cizer (aninda), canli tablo /api/live ile sonra dolar.
    """
    a = analytics.analyze()
    return jsonify({
        "analysis": a,
        "series": analytics.series_by_symbol(analytics.load()),
        "server_time": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/refresh")
def api_refresh():
    """Onbellegi atlayip taze tarama yapar ve snapshot'a YAZAR (gercek tarama)."""
    _live_cache["ts"] = 0.0
    return jsonify(live_data(save=True))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    print(f"Dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
