#!/usr/bin/env python3
"""Carry sağlığı — funding trendi, basis geçmişi ve giriş skoru.

Sorular:
  * Bu sembolün funding'i son 3 günde bozuluyor mu? (30 gün ortalamasına göre)
  * Basis primli mi? (primli girmek getiriyi yer)
  * Maliyet sonrası net carry ne kadar, kaç günde kapanıyor?
  * Giriş skoru: iyi bir carry girişi mi, yoksa "geç kalmış" mı?

Kaynak: data/funding_snapshots.csv (her taramada tüm indikatörler yazılıyor)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
SNAP = ROOT / "data" / "funding_snapshots.csv"
ROUND_TRIP_COST_PCT = 0.30     # giriş+çıkış maliyeti
PRIM_RISK_PCT = 0.30           # basis bunun üstündeyse "primli"


@lru_cache(maxsize=1)
def _df() -> pd.DataFrame:
    if not SNAP.exists():
        return pd.DataFrame()
    df = pd.read_csv(SNAP)
    df["scanned_at"] = pd.to_datetime(df["scanned_at"], errors="coerce", utc=True)
    return df.dropna(subset=["scanned_at"]).sort_values("scanned_at")


def health(symbol: str) -> dict:
    df = _df()
    if df.empty:
        return {"symbol": symbol, "available": False, "reason": "snapshot yok"}
    d = df[df.symbol == symbol]
    if d.empty:
        return {"symbol": symbol, "available": False, "reason": "sembol snapshot'ta yok"}
    last = d.iloc[-1]
    ann = float(last.get("ann_30d_pct") or 0)
    ann_now = float(last.get("ann_now_pct") or 0)
    basis = float(last.get("basis_pct") or 0)
    p25 = float(last.get("ann_p25_pct") or 0)
    breakeven = float(last.get("breakeven_days") or 0)
    scans = d["scanned_at"].dt.floor("D").nunique()
    days_span = max((d["scanned_at"].max() - d["scanned_at"].min()).total_seconds() / 86400, 0)

    # trend: son 3 gün ortalaması vs tüm geçmiş ortalaması
    cut = d["scanned_at"].max() - pd.Timedelta(days=3)
    recent = d[d["scanned_at"] >= cut]["ann_30d_pct"].astype(float)
    trend = float(recent.mean() - ann) if len(recent) >= 2 else 0.0

    # giriş skoru (0-100): temkinli getiri + trend - basis cezası - başabaş cezası
    score = 50.0
    score += min(max(ann, -20), 40) * 0.8          # yıllık temkinli getiri
    score += min(max(ann_now, -20), 20) * 0.5      # anlık funding
    score += max(min(trend, 10), -10) * 1.5        # trend
    if basis > PRIM_RISK_PCT:
        score -= 25
    elif basis < 0.05:
        score += 10
    if breakeven > 60:
        score -= 15
    score = float(np.clip(score, 0, 100))

    if basis > PRIM_RISK_PCT:
        verdict = "primli — giriş için kötü"
    elif ann < ROUND_TRIP_COST_PCT:
        verdict = "getiri maliyeti karşılamıyor"
    elif trend < -5:
        verdict = "funding bozuluyor (son 3 gün düşüş)"
    elif score >= 65:
        verdict = "giriş için uygun"
    elif score >= 50:
        verdict = "sınırda — küçük pozisyon"
    else:
        verdict = "zayıf"

    return {"symbol": symbol, "available": True, "ann_30d_pct": round(ann, 2),
            "ann_now_pct": round(ann_now, 2), "ann_p25_pct": round(p25, 2),
            "basis_pct": round(basis, 3), "breakeven_days": breakeven,
            "trend_3d": round(trend, 2), "days_covered": round(days_span, 1),
            "scans": int(scans), "entry_score": round(score, 0), "verdict": verdict,
            "net_after_cost_pct": round(ann - ROUND_TRIP_COST_PCT, 2)}


def all_health(limit: int | None = None) -> dict:
    df = _df()
    if df.empty:
        return {"available": False, "rows": [], "reason": "snapshot yok"}
    latest = df[df["scanned_at"] == df["scanned_at"].max()]
    syms = list(latest["symbol"])
    rows = [health(s) for s in syms]
    rows = [r for r in rows if r.get("available")]
    rows.sort(key=lambda r: -r["entry_score"])
    if limit:
        rows = rows[:limit]
    return {"available": True, "generated_at": str(df["scanned_at"].max()),
            "rows": rows,
            "note": "Skor: temkinli getiri + anlık funding + 3 günlük trend − basis primi − uzun başabaş cezası."}


if __name__ == "__main__":
    d = all_health(12)
    print(f"carry sağlığı · {d.get('generated_at')}")
    for r in d["rows"]:
        print(f"{r['symbol']:<11} skor {r['entry_score']:>4.0f} · yıllık {r['ann_30d_pct']:+.2f}% "
              f"· trend {r['trend_3d']:+.1f} · basis {r['basis_pct']:.3f} · {r['verdict']}")
