#!/usr/bin/env python3
"""Gercek ogrenme katmani — walk-forward lojistik regresyon.

NE YAPAR:
  Ozelliklerden (para akisi, OI, pozisyonlanma, funding, hacim) +24s yon tahmini
  icin agirlik ogrenir. Agirliklar GELECEK veriyle degil, her tahmin icin SADECE
  o andan onceki veriyle egitilir (walk-forward). Sonra gorulmemis blokta olculur.

NE OLCER:
  AUC (0.5 = yazi-tura), isabet orani, yuksek-guven grubunun ortalama getirisi,
  ve sabit kuralla/rastgeleye karsi fark.

DURUSTLUK KURALI:
  AUC < 0.55 ise model genellemiyor demektir -> panele "tahmin" olarak KOYULMAZ.
  Bu durumda rapor "ogrenme yok" der. Bu bir basarisizlik degil, olcumdur.

Bagimlilik yok: LR numpy ile elle yazildi (sklearn gerekmez).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import rule_backtest as rb

ROOT = Path(__file__).parent
OUT = ROOT / "reports" / "learn.json"
HORIZON = 24            # +24s yon tahmini
TRAIN_MIN = 150         # ilk egitim icin min ornek
BLOCK = 40              # walk-forward blok boyu
L2 = 1.0
FEATURES = ["price_chg", "net_24h_musd", "whale_net_musd", "oi_chg_24h",
            "top_retail_gap", "log_vol"]


def build_dataset(symbols: list[str]) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        d = rb.load_symbol(sym)
        if d is None or len(d) < rb.LOOKBACK + HORIZON + 5:
            continue
        for i in range(rb.LOOKBACK, len(d) - HORIZON):
            r = rb.rule_at(d, i)
            if r is None:
                continue
            fwd = (d["close"].iloc[i + HORIZON] / d["close"].iloc[i] - 1) * 100
            rows.append({
                "ts": d["ts"].iloc[i], "symbol": sym,
                "price_chg": r["price_chg"], "net_24h_musd": r["net_24h_musd"],
                "whale_net_musd": r["whale_net_musd"], "oi_chg_24h": r["oi_chg_24h"],
                "top_retail_gap": r["top_retail_gap"],
                "log_vol": float(np.log1p(r["vol_24h_musd"])),
                "fwd": fwd, "y": int(fwd > 0),
            })
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """L2 duzenli lojistik regresyon, gradyan inişi (bias dahil)."""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    for _ in range(400):
        p = 1 / (1 + np.exp(-Xb @ w))
        g = Xb.T @ (p - y) / len(y) + L2 * np.r_[0.0, w[1:]] / len(y)
        w -= 0.5 * g
    return w


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.hstack([np.ones((len(X), 1)), X]) @ w))


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Siralama tabanli AUC (0.5 = rastgele)."""
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return 0.5
    order = np.argsort(np.r_[pos, neg])
    ranks = np.empty(len(order), float)
    ranks[order] = np.arange(1, len(order) + 1)
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def main() -> int:
    import json as _j
    cache = _j.loads((ROOT / "data" / "universe_cache.json").read_text(encoding="utf-8"))
    syms = [s for s, v in cache["rows"] if v >= 300][:10]

    df = build_dataset(syms)
    if df.empty:
        print("Veri yok.")
        return 1
    print(f"veri: {len(df)} ornek, {df['symbol'].nunique()} sembol, "
          f"{df['ts'].min()} → {df['ts'].max()}")
    print(f"yukari orani: {df['y'].mean():.1%}\n")

    X_all = df[FEATURES].to_numpy(float)
    y_all = df["y"].to_numpy(int)

    preds = np.full(len(df), np.nan)
    folds = 0
    start = TRAIN_MIN
    while start + BLOCK <= len(df):
        tr = slice(0, start)
        te = slice(start, start + BLOCK)
        mu, sd = X_all[tr].mean(0), X_all[tr].std(0)
        sd[sd == 0] = 1.0
        w = fit((X_all[tr] - mu) / sd, y_all[tr])
        preds[te] = predict(w, (X_all[te] - mu) / sd)
        folds += 1
        start += BLOCK

    mask = ~np.isnan(preds)
    y, p = y_all[mask], preds[mask]
    fwd = df["fwd"].to_numpy(float)[mask]
    print(f"walk-forward: {folds} kat, {mask.sum()} gorulmemis tahmin\n")

    a = auc(y, p)
    base = max(y.mean(), 1 - y.mean())
    hi = p >= np.quantile(p, 0.8)          # en guvenli %20
    lo = p <= np.quantile(p, 0.2)

    print(f"{'Olcum':<34}{'Deger':>12}")
    print("-" * 46)
    print(f"{'AUC (0.5=rastgele)':<34}{a:>12.3f}")
    print(f"{'Baseline isabet (cogunluk)':<34}{base:>11.1%}")
    print(f"{'Model isabet (p>0.5)':<34}{(y[p > 0.5].mean() if (p > 0.5).any() else 0):>11.1%}")
    print(f"{'Yuksek guven %20 ort. getiri':<34}{fwd[hi].mean():>+11.2f}%")
    print(f"{'Dusuk guven %20 ort. getiri':<34}{fwd[lo].mean():>+11.2f}%")
    print(f"{'Tum orneklem ort. getiri':<34}{fwd.mean():>+11.2f}%")

    # karar: genelliyor mu?
    generalizes = a >= 0.55
    print("\n" + "=" * 46)
    print(f"KARAR: {'model genelliyor (AUC>=0.55)' if generalizes else 'MODEL GENELLEMIYOR'}")
    print("=" * 46)
    if not generalizes:
        print("AUC 0.55 altinda -> ogrenilen agirliklar gorulmemis veride ise yaramiyor.")
        print("Bu agirliklari panele 'tahmin' olarak koymak yaniltici olur; konulmadi.")

    # --- deploy: tum veriyle son agirliklar (canli skorlama icin) ------------
    mu_f, sd_f = X_all.mean(0), X_all.std(0)
    sd_f[sd_f == 0] = 1.0
    w_final = fit((X_all - mu_f) / sd_f, y_all)

    summary = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "samples": int(mask.sum()), "fold": folds, "horizon_h": HORIZON,
        "auc": round(a, 4), "baseline_hit": round(float(base), 4),
        "model_hit": round(float(y[p > 0.5].mean()), 4) if (p > 0.5).any() else None,
        "high_conf_mean": round(float(fwd[hi].mean()), 3),
        "low_conf_mean": round(float(fwd[lo].mean()), 3),
        "all_mean": round(float(fwd.mean()), 3),
        "generalizes": bool(generalizes),
        "features": FEATURES,
        # canli skorlama icin
        "mu": [round(float(v), 6) for v in mu_f],
        "sd": [round(float(v), 6) for v in sd_f],
        "weights": [round(float(v), 6) for v in w_final],
    }
    OUT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"\nrapor: {OUT}")
    print("agirliklar:", {f: round(float(w), 3) for f, w in zip(FEATURES, w_final[1:])})
    return 0


def load_model() -> dict | None:
    """Kaydedilmis modeli oku (yoksa None)."""
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return None


def score_features(model: dict, feats: dict) -> float | None:
    """Tek satir icin yukari olasiligi (0-1). Model genellemiyorsa None."""
    if not model or not model.get("generalizes"):
        return None
    try:
        x = np.array([(feats[f] - m) / s for f, m, s in
                      zip(model["features"], model["mu"], model["sd"])], dtype=float)
    except KeyError:
        return None
    if not np.all(np.isfinite(x)):
        return None
    z = model["weights"][0] + float(x @ np.array(model["weights"][1:]))
    return float(1 / (1 + np.exp(-z)))


if __name__ == "__main__":
    sys.exit(main())
