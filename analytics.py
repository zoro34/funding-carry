#!/usr/bin/env python3
"""Biriken funding snapshot'larini analiz eden motor.

NE YAPAR (durust tanim):
  * Sembol bazinda istatistik cikarir (ortalama, 25. persentil, pozitif oran,
    oynaklik, basis)
  * Izlemeye basladigindan beri ELDE EDILMIS tahmini carry'yi hesaplar
  * Bilesik skor uretir (0-100)
  * Rejim degisikligi ve risk bayraklari uretir
  * Insan okur 'cikarim' listesi yazar

NE YAPMAZ:
  * Gelecek tahmini yapmaz
  * "Ogrenme" iddiasinda degildir — bu istatistiksel skorlama + kanit birikimi.
    Gercek ML icin yuzlerce gozlem ve bir hedef degisken gerekir; veri biriktikce
    eklenebilir.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
SNAP = DATA / "funding_snapshots.csv"
ROUND_TRIP_COST = 2 * 0.001 + 2 * 0.0005


def load() -> pd.DataFrame:
    if not SNAP.exists():
        return pd.DataFrame()
    df = pd.read_csv(SNAP, parse_dates=["scanned_at", "next_funding"])
    # eski semali satirlar olabilir
    for col in ["ann_p25_pct", "pos_share_pct", "breakeven_days"]:
        if col not in df.columns:
            df[col] = np.nan
    return df.drop_duplicates(subset=["scanned_at", "symbol"]).sort_values("scanned_at")


def realized_carry(g: pd.DataFrame) -> tuple[float, float]:
    """Izlemeye basladigindan beri tahmini birikmis carry (%).

    Her ardisik snapshot arasinda anlik funding oranini zamanla entegre eder:
        getiri = ann_now% / 100 * (dt_gun / 365)
    Yaklasimdir: anlik oranin aralik boyunca sabit kaldigini varsayar.
    """
    g = g.sort_values("scanned_at")
    if len(g) < 2:
        return 0.0, 0.0
    total = 0.0
    for (_, a), (_, b) in zip(g.iloc[:-1].iterrows(), g.iloc[1:].iterrows()):
        days = (b["scanned_at"] - a["scanned_at"]).total_seconds() / 86400
        if days <= 0:
            continue
        total += (float(a["ann_now_pct"]) / 100) * (days / 365)
    days_total = (g["scanned_at"].iloc[-1] - g["scanned_at"].iloc[0]).total_seconds() / 86400
    return total * 100, days_total


def per_symbol(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    out = []
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("scanned_at")
        last = g.iloc[-1]
        carry_pct, days = realized_carry(g)

        p25 = g["ann_p25_pct"].dropna()
        ann = g["ann_30d_pct"].dropna()
        pos = g["pos_share_pct"].dropna()
        bas = g["basis_pct"].dropna()

        out.append({
            "symbol": sym,
            "scans": len(g),
            "days_tracked": round(days, 2),
            "now_pct": float(last["funding_now_pct"]),
            "ann_now": float(last["ann_now_pct"]),
            "ann_30d_mean": float(ann.mean()) if len(ann) else 0.0,
            "p25_mean": float(p25.mean()) if len(p25) else 0.0,
            "p25_std": float(p25.std()) if len(p25) > 1 else 0.0,
            "pos_mean": float(pos.mean()) if len(pos) else 0.0,
            "basis_mean": float(bas.mean()) if len(bas) else 0.0,
            "basis_now": float(last["basis_pct"]),
            "vol_now": float(last["vol_musd"]),
            "breakeven_days": float(last["breakeven_days"]) if pd.notna(last["breakeven_days"]) else None,
            "realized_carry_pct": round(carry_pct, 4),
            "net_after_cost_pct": round(carry_pct - ROUND_TRIP_COST * 100, 4),
            "entry_ok": bool(last["funding_now_pct"] >= 0 and last["basis_pct"] < 0.3 and (p25.mean() if len(p25) else 0) > 0),
        })
    return out


def score(stats: list[dict]) -> list[dict]:
    """Bilesik skor 0-100.

    Yuksek = funding kalici + basis temiz + oynaklik dusuk + SU AN odemiyor.
    Anlik negatif funding agir cezalidir: gecmis ne olursa olsun, su an
    girersen para kaybediyorsun.
    """
    if not stats:
        return stats
    raw = []
    for s in stats:
        v = (
            s["p25_mean"] * 1.0
            + s["pos_mean"] * 0.08
            - max(s["basis_mean"], 0) * 25
            - s["p25_std"] * 0.6
        )
        # anlik rejim cezasi
        if s["now_pct"] < 0:
            v -= 30
            v -= min(abs(s["ann_now"]), 300) * 0.1
        if not s["entry_ok"]:
            v -= 10
        raw.append(v)
    lo, hi = min(raw), max(raw)
    for s, v in zip(stats, raw):
        s["score"] = round(100 * (v - lo) / (hi - lo), 1) if hi > lo else 50.0
    return sorted(stats, key=lambda x: -x["score"])


def insights(stats: list[dict], df: pd.DataFrame) -> list[dict]:
    """Insan okur cikarimlar. Her biri {level, text}."""
    out: list[dict] = []
    if not stats:
        out.append({"level": "info", "text": "Henuz snapshot yok. Izleme basladiginda veri birikir."})
        return out

    n_scans = df["scanned_at"].nunique()
    out.append({
        "level": "info",
        "text": f"{n_scans} tarama, {len(stats)} sembol izleniyor. "
                f"Guvenilir istatistik icin 20+ tarama (yaklasik 5 gun) gerekir.",
    })

    best = stats[0]
    out.append({
        "level": "good" if best["p25_mean"] > 0 else "warn",
        "text": f"En yuksek skor: {best['symbol']} — temkinli {best['p25_mean']:+.2f}%/yil, "
                f"pozitif oran {best['pos_mean']:.0f}%, basis {best['basis_now']:+.3f}%.",
    })

    neg = [s for s in stats if s["now_pct"] < 0]
    if neg:
        names = ", ".join(s["symbol"] for s in neg[:5])
        out.append({
            "level": "warn",
            "text": f"{len(neg)} sembolde anlik funding negatif ({names}). "
                    f"Bu sembollerde su an girersen odeme yaparsin.",
        })

    rich = [s for s in stats if s["basis_now"] > 0.3]
    if rich:
        names = ", ".join(s["symbol"] for s in rich[:5])
        out.append({
            "level": "warn",
            "text": f"{len(rich)} sembolde perp primli (basis > %0.3): {names}. "
                    f"Primli girmek getiriyi yer.",
        })

    close_be = [s for s in stats if s["breakeven_days"] and s["breakeven_days"] > 60]
    if close_be:
        out.append({
            "level": "warn",
            "text": f"{len(close_be)} sembolde maliyeti kapatmak 60 gunden uzun suruyor — "
                    f"kisa vadeli giris buralarda zarar yazar.",
        })

    best_carry = max(stats, key=lambda x: x["realized_carry_pct"])
    if best_carry["days_tracked"] > 0:
        out.append({
            "level": "info",
            "text": f"Izleme boyunca en iyi tahmini carry: {best_carry['symbol']} "
                    f"%{best_carry['realized_carry_pct']:+.4f} ({best_carry['days_tracked']:.1f} gunde). "
                    f"Net (maliyet dusulmus): %{best_carry['net_after_cost_pct']:+.4f}.",
        })

    if n_scans < 20:
        out.append({
            "level": "info",
            "text": "Orneklem kucuk: bu ekrandaki skorlar henuz karar vermek icin yeterli degil. "
                    "Veri biriktikce otomatik olarak daha anlamli hale gelir.",
        })
    return out


def analyze() -> dict:
    df = load()
    stats = score(per_symbol(df))
    return {
        "scans": int(df["scanned_at"].nunique()) if not df.empty else 0,
        "first_scan": df["scanned_at"].min().isoformat() if not df.empty else None,
        "last_scan": df["scanned_at"].max().isoformat() if not df.empty else None,
        "symbols": stats,
        "insights": insights(stats, df),
        "round_trip_cost_pct": ROUND_TRIP_COST * 100,
    }


def series_by_symbol(df: pd.DataFrame, top: int = 5) -> dict:
    """Grafik icin: en cok izlenen sembollerin ann_30d zaman serisi."""
    if df.empty:
        return {"labels": [], "series": []}
    counts = df["symbol"].value_counts().head(top).index.tolist()
    labels = sorted(df["scanned_at"].dt.strftime("%m-%d %H:%M").unique().tolist())
    series = []
    for sym in counts:
        g = df[df["symbol"] == sym].sort_values("scanned_at")
        pts = dict(zip(g["scanned_at"].dt.strftime("%m-%d %H:%M"), g["ann_30d_pct"]))
        series.append({"name": sym, "data": [pts.get(l) for l in labels]})
    return {"labels": labels, "series": series}


if __name__ == "__main__":
    import json

    res = analyze()
    print(json.dumps(res, indent=2, ensure_ascii=False)[:3000])
