#!/usr/bin/env python3
"""Tum perpetual sembolleri izleyen huni tarayici + AL/SAT/BEKLE kurali.

NEDEN HUNI: 565 perpetual sembol var. Her birine 4-5 istek atmak 2500+ istek
eder; Binance bunu bloklar (bu projede 100 istekte 403 goruldu). Bu yuzden:

  Asama 0  (3 istek, TUM semboller)  : hacim, funding, basis, 24s fiyat degisimi
  Asama 1  (1 istek/sembol, ~60)     : 5m mumlar -> net taker akisi + balina izi
  Asama 2  (2 istek/sembol, ~15)     : acik pozisyon + buyuk oyuncu/retail orani
  Karar                              : AL / SAT / BEKLE + kac kosul saglandi

ONEMLI: "AL/SAT" bir TAVSIYE degil, onceki asamalarda KANITLANMAMIS kurallarin
mekanik ciktisidir. Kazanma orani iddia edilmez. Sinyaller alerts.csv'ye yazilir
ki 1-2 ay sonra "gercekten ise yaradi mi" olculebilsin.
"""
from __future__ import annotations

import csv
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import flow
import learn

DATA = Path(__file__).parent / "data"
ALERTS = DATA / "alerts.csv"
ALERT_COLS = ["ts", "symbol", "signal", "conditions", "score", "price_chg",
              "net_24h_musd", "oi_24h", "gap", "whale_net_musd",
              "funding_now_pct", "ann_funding_pct", "reasons"]

MIN_CONDITIONS = 3          # alarm icin en az kac kosul saglanmali


# --------------------------------------------------------------- Asama 0: bulk
def bulk() -> list[dict]:
    """TUM perpetual semboller icin ucuz veri: 3 istek."""
    prem = flow._get(f"{flow.FAPI}/fapi/v1/premiumIndex") or []
    tick = flow._get(f"{flow.FAPI}/fapi/v1/ticker/24hr") or []
    info = flow._get(f"{flow.FAPI}/fapi/v1/exchangeInfo") or {}

    live = {s["symbol"] for s in info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"}
    tmap = {t["symbol"]: t for t in tick}
    pmap = {p["symbol"]: p for p in prem}

    out = []
    for sym in live & set(tmap) & set(pmap):
        t, p = tmap[sym], pmap[sym]
        mark = float(p.get("markPrice") or 0)
        idx = float(p.get("indexPrice") or 0)
        out.append({
            "symbol": sym,
            "vol_musd": float(t.get("quoteVolume") or 0) / 1e6,
            "price_chg_24h": float(t.get("priceChangePercent") or 0),
            "funding_now_pct": float(p.get("lastFundingRate") or 0) * 100,
            "basis_pct": ((mark / idx - 1) * 100) if idx else 0.0,
        })
    return out


# --------------------------------------------------- Asama 1: akis (1 istek/sym)
def stage_flow(sym: str) -> dict | None:
    df = flow.klines_5m(sym, 288)
    if df.empty or len(df) < 50:
        return None
    w1 = flow._window_stats(df, flow.BARS_PER_HOUR)
    w4 = flow._window_stats(df, flow.BARS_PER_HOUR * 4)
    w24 = flow._window_stats(df, flow.BARS_PER_HOUR * 24)
    wh = flow.whale_activity(df)
    chg = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100 if len(df) > 1 else 0.0
    step = max(1, len(df) // 60)
    return {
        "symbol": sym,
        "price_chg": round(chg, 2),
        "spark": [round(float(v), 8) for v in df["close"].iloc[::step].tolist()],
        "net_1h_musd": round(w1["net_musd"], 2),
        "net_4h_musd": round(w4["net_musd"], 2),
        "net_24h_musd": round(w24["net_musd"], 2),
        "vol_24h_musd": round(w24["vol_musd"], 1),
        "buy_ratio_24h": round(w24["buy_ratio"] * 100, 1),
        "whale_net_musd": round(wh["whale_net_musd"], 2),
        "whale_share": round(wh["whale_share"] * 100, 1),
        "whale_minutes": wh["whale_minutes"],
    }


# --------------------------------------- Asama 2: pozisyonlanma (2 istek/sym)
def stage_deep(row: dict) -> dict:
    oi = flow.open_interest(row["symbol"])
    pos = flow.positioning(row["symbol"])
    row = dict(row)
    row.update({
        "oi_now_musd": round(oi["oi_now_musd"], 1),
        "oi_chg_1h": round(oi["oi_chg_1h"], 2),
        "oi_chg_24h": round(oi["oi_chg_24h"], 2),
        "top_long_pct": round(pos["top_long_pct"], 1),
        "top_long_chg": round(pos["top_long_chg"], 2),
        "retail_long_pct": round(pos["retail_long_pct"], 1),
        "top_retail_gap": round(pos["top_retail_gap"], 1),
        "deep": True,
    })
    return row


# ------------------------------------------------------------------- karar
def decide(row: dict) -> tuple[str, int, list[str], float]:
    """AL / SAT / BEKLE + saglanan kosul sayisi + gerekceler + skor.

    Kosullar YON + AKIS + POZISYONLANMA ucgenini ariyor (confluence).
    Bu bir olasilik degil; kac kosulun ayni yonde oldugunun sayimidir.
    """
    deep = row.get("deep", False)
    chg = row["price_chg"]
    net = row["net_24h_musd"]
    oi = row.get("oi_chg_24h", 0.0)
    gap = row.get("top_retail_gap", 0.0)
    whale = row["whale_net_musd"]

    al, sat, rea_al, rea_sat = 0, 0, [], []
    if net > 0:
        al += 1; rea_al.append("para girisi")
    else:
        sat += 1; rea_sat.append("para cikisi")
    if whale > 0:
        al += 1; rea_al.append("balina alici")
    else:
        sat += 1; rea_sat.append("balina satici")
    if deep:
        if oi > 0 and chg > 0:
            al += 1; rea_al.append("OI artiyor")
        if oi > 0 and chg < 0:
            sat += 1; rea_sat.append("OI artiyor (short)")
        if gap > 0:
            al += 1; rea_al.append("buyuk>kucuk long")
        else:
            sat += 1; rea_sat.append("buyuk<kucuk long")
    if chg > 0:
        al += 1; rea_al.append("fiyat yukari")
    else:
        sat += 1; rea_sat.append("fiyat asagi")

    score = max(0.0, min(100.0, 50 + net / 4 + whale / 2 + gap + oi / 2 + chg * 2))
    if not deep:
        return "—", max(al, sat), ["on eleme"], round(score, 1)

    # KORUMA: 24 saatte asiri uzamis coin ne AL ne SAT. +%45 yukselmis bir
    # coinde "para girisi var" demek dogru ama giris yapmak tuzak; -%30 dusmus
    # coinde "SAT" demek de dip satisi olur. Ikisi de "gec kaldin" bolgesi.
    if abs(chg) > 15:
        return ("BEKLE", max(al, sat),
                [f"asiri uzamis ({chg:+.0f}%) — kovalama"], round(score, 1))

    if al >= MIN_CONDITIONS and al > sat:
        return "AL", al, rea_al, round(score, 1)
    if sat >= MIN_CONDITIONS and sat > al:
        return "SAT", sat, rea_sat, round(score, 1)
    return "BEKLE", max(al, sat), ["celiskili sinyal"], round(score, 1)


# ------------------------------------------------------------------- tarama
def scan(min_vol: float = 20.0, stage2_n: int = 60, stage3_n: int = 15,
         workers: int = 2) -> dict:
    t0 = time.time()
    flow.STATS.update({"requests": 0, "retries": 0, "failed": 0})

    b = [r for r in bulk() if r["vol_musd"] >= min_vol]
    b.sort(key=lambda x: -x["vol_musd"])
    bmap = {r["symbol"]: r for r in b}

    # Asama 1 adaylari: en likit N + funding anomalileri (mutlak yillik)
    liquid = [r["symbol"] for r in b[:stage2_n]]
    anomaly = sorted(b, key=lambda x: -abs(x["funding_now_pct"] * 3 * 365))[:15]
    cand = list(dict.fromkeys(liquid + [r["symbol"] for r in anomaly]))[: stage2_n + 15]

    def work(sym):
        time.sleep(random.uniform(0, 0.35))
        try:
            return stage_flow(sym)
        except Exception:
            return None

    s1 = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(work, cand):
            if r:
                s1.append(r)

    # Asama 2: akis skoruna gore en iyi N
    def pre(r):
        return r["net_24h_musd"] + r["whale_net_musd"] * 2

    s1.sort(key=lambda r: -pre(r))
    top = s1[:stage3_n]

    s2 = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(stage_deep, top):
            if r:
                s2.append(r)

    rows = []
    for r in s2:
        r = dict(r)
        r.update({k: bmap[r["symbol"]][k] for k in
                  ("vol_musd", "funding_now_pct", "basis_pct") if r["symbol"] in bmap})
        r["ann_funding_pct"] = round(r["funding_now_pct"] * 3 * 365, 1)
        sig, cond, rea, score = decide(r)
        r.update({"signal": sig, "conditions": cond, "reasons": rea, "score": score})
        rows.append(r)

    # ogrenilmis model (walk-forward AUC >= 0.55 ise) -> yukari olasiligi
    model = learn.load_model()
    if model and model.get("generalizes"):
        for r in rows:
            feats = {k: r.get(k) for k in model["features"] if k != "log_vol"}
            feats["log_vol"] = math.log1p(max(0.0, float(r.get("vol_24h_musd") or 0.0)))
            r["prob"] = learn.score_features(model, feats)
    for r in rows:
        r.setdefault("prob", None)

    order = {"AL": 0, "SAT": 1, "BEKLE": 2, "—": 3}
    rows.sort(key=lambda r: (order.get(r["signal"], 9), -r["conditions"], -r["score"]))

    return {
        "rows": rows,
        "scanned_total": len(b),
        "stage1": len(s1),
        "stage2": len(s2),
        "elapsed_s": round(time.time() - t0, 1),
        "stats": dict(flow.STATS),
        "model": ({k: model.get(k) for k in
                   ("auc", "generalizes", "samples", "high_conf_mean", "low_conf_mean")}
                  if model else None),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "min_vol": min_vol,
    }


# ------------------------------------------------------------------- alarmlar
def write_alerts(rows: list[dict], cooldown_h: float = 6.0) -> int:
    """AL/SAT sinyallerini alerts.csv'ye yaz. Ayni sembol+sinyal tekrarini
    cooldown_h boyunca bastir ki ayni alarm her taramada tekrarlanmasin."""
    if not rows:
        return 0
    DATA.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    recent = {(a["symbol"], a["signal"]) for a in recent_alerts(200)
              if (now - datetime.fromisoformat(a["ts"])).total_seconds() < cooldown_h * 3600}

    new = not ALERTS.exists()
    n = 0
    with ALERTS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ALERT_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            if r["signal"] not in ("AL", "SAT") or r["conditions"] < MIN_CONDITIONS:
                continue
            if (r["symbol"], r["signal"]) in recent:
                continue
            w.writerow({
                "ts": now.isoformat(), "symbol": r["symbol"], "signal": r["signal"],
                "conditions": r["conditions"], "score": r["score"],
                "price_chg": r["price_chg"], "net_24h_musd": r["net_24h_musd"],
                "oi_24h": r.get("oi_chg_24h"), "gap": r.get("top_retail_gap"),
                "whale_net_musd": r["whale_net_musd"],
                "funding_now_pct": r.get("funding_now_pct"),
                "ann_funding_pct": r.get("ann_funding_pct"),
                "reasons": "; ".join(r["reasons"]),
            })
            n += 1
    return n


def recent_alerts(limit: int = 50) -> list[dict]:
    if not ALERTS.exists():
        return []
    with ALERTS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:][::-1]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Tum sembol tarayici + AL/SAT")
    ap.add_argument("--min-vol", type=float, default=20.0)
    ap.add_argument("--stage2", type=int, default=60)
    ap.add_argument("--stage3", type=int, default=15)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--alert", action="store_true", help="alarmlari alerts.csv'ye yaz")
    a = ap.parse_args()

    res = scan(a.min_vol, a.stage2, a.stage3, a.workers)
    print(f"taranan sembol: {res['scanned_total']} (>= {res['min_vol']}M$)  "
          f"akis: {res['stage1']}  derin: {res['stage2']}  "
          f"istek: {res['stats']['requests']}  retry: {res['stats']['retries']}  "
          f"sure: {res['elapsed_s']}s\n")
    print(f"{'Sembol':<14}{'SINYAL':>7}{'Kosul':>7}{'Skor':>6}{'Fiyat%':>8}"
          f"{'Net24$M':>10}{'OI24%':>8}{'Gap':>7}{'Balina$M':>10}  Gerekce")
    print("-" * 108)
    for r in res["rows"]:
        print(f"{r['symbol']:<14}{r['signal']:>7}{r['conditions']:>7}{r['score']:>6.1f}"
              f"{r['price_chg']:>+8.2f}{r['net_24h_musd']:>+10.1f}"
              f"{r.get('oi_chg_24h', 0):>+8.2f}{r.get('top_retail_gap', 0):>+7.1f}"
              f"{r['whale_net_musd']:>+10.2f}  {', '.join(r['reasons'][:3])}")

    if a.alert:
        n = write_alerts(res["rows"])
        print(f"\nyeni alarm: {n}  -> {ALERTS}")
