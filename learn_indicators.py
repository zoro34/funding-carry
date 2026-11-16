#!/usr/bin/env python3
"""İndikatör hafızası → öğrenme.

1) Tüm taramalardaki TÜM indikatörleri (funding_now, ann_now, ann_30d, p25, pozisyon
   payı, basis, hacim, örnek sayısı, başabaş günü, skor) bir panel olarak okur.
2) Soru: bu indikatörler bir sonraki taramada funding'in POZİTİF kalacağını
   (yani carry'nin devam edeceğini) öngörüyor mu?
3) Zaman sıralı bölme (geçmiş → gelecek), lojistik regresyon, AUC.
4) Dürüst karar: AUC ~0.5 ise "indikatörlerin öngörü gücü yok" der.

Kanonik: .venv/bin/python learn_indicators.py   (make learn)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
SNAP = ROOT / "data" / "funding_snapshots.csv"
OUT = ROOT / "reports" / "indicator_model.json"
FEATURES = ["funding_now_pct", "ann_now_pct", "ann_30d_pct", "ann_p25_pct",
            "pos_share_pct", "basis_pct", "vol_musd", "samples",
            "breakeven_days", "score", "interval_h"]


def design(X, mu, sd):
    return np.hstack([np.ones((len(X), 1)), (X - mu) / sd])


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit(X, y, epochs=4000, lr=0.05, l2=1e-3):
    n, d = X.shape
    w = np.zeros(d + 1)
    Xb = np.hstack([np.ones((n, 1)), X])
    for _ in range(epochs):
        p = sigmoid(Xb @ w)
        g = Xb.T @ (p - y) / n
        g[1:] += l2 * w[1:]
        w -= lr * g
    return w


def auc(y, p):
    order = np.argsort(p)
    y = np.asarray(y)[order]
    ranks = np.arange(1, len(y) + 1)
    pos = y.sum()
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def main():
    if not SNAP.exists():
        print("snapshot yok:", SNAP)
        return 1
    df = pd.read_csv(SNAP)
    df["scanned_at"] = pd.to_datetime(df["scanned_at"], errors="coerce", utc=True)
    df = df.dropna(subset=["scanned_at"]).sort_values(["symbol", "scanned_at"])
    scans = df["scanned_at"].nunique()
    print(f"snapshot: {len(df)} satır · {scans} tarama · {df.symbol.nunique()} sembol")

    # hedef: bir sonraki taramada ann_30d, o taramanın MEDYANININ uzerinde mi?
    # (mutlak >0 hedefi dejenere oluyor: funding neredeyse hep pozitif)
    df["next_ann"] = df.groupby("symbol")["ann_30d_pct"].shift(-1)
    df["next_ts"] = df.groupby("symbol")["scanned_at"].shift(-1)
    df = df.dropna(subset=["next_ann", "next_ts"])
    med = df.groupby("next_ts")["next_ann"].transform("median")
    df["y"] = (df["next_ann"] > med).astype(int)
    print(f"hedef taban orani (medyan ustu): {df['y'].mean():.1%}")

    have = [f for f in FEATURES if f in df.columns]
    data = df.dropna(subset=have + ["y"])
    if len(data) < 200:
        print(f"yetersiz örnek: {len(data)} (en az 200 gerekli)")
        return 1
    # zaman sirali bolme
    cut = data["scanned_at"].quantile(0.7)
    tr = data[data["scanned_at"] <= cut]
    te = data[data["scanned_at"] > cut]
    if len(tr) < 100 or len(te) < 50:
        print(f"bolme yetersiz: train {len(tr)} / test {len(te)}")
        return 1

    Xtr = tr[have].to_numpy(float)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    w = fit((Xtr - mu) / sd, tr["y"].to_numpy(float))
    p_tr = sigmoid(design(Xtr, mu, sd) @ w)
    Xte = te[have].to_numpy(float)
    p_te = sigmoid(design(Xte, mu, sd) @ w)

    a_tr, a_te = auc(tr["y"], p_tr), auc(te["y"], p_te)
    # naif taban: "ayni sembol ayni yerde kalir" — sadece bugunku ann_30d ile siralama
    a_naive = auc(te["y"], te["ann_30d_pct"].to_numpy(float))
    base = float(te["y"].mean())

    # yuksek guven diliminde gercek oran
    hi = p_te >= np.quantile(p_te, 0.8)
    lo = p_te <= np.quantile(p_te, 0.2)
    hi_rate = float(te["y"].to_numpy()[hi].mean()) if hi.any() else float("nan")
    lo_rate = float(te["y"].to_numpy()[lo].mean()) if lo.any() else float("nan")

    weights = {f: round(float(v), 3) for f, v in zip(have, w[1:])}
    ranked = sorted(weights.items(), key=lambda kv: -abs(kv[1]))

    if a_te - a_naive < 0.02:
        verdict = ("model, basit 'ayni sembol ayni yerde kalir' kuralindan iyi degil "
                   "— yuksek AUC sadece funding kaliciligi (autokorelasyon)")
    elif a_te >= 0.60:
        verdict = "indikatörler öngörü sağlıyor (naif kuralı geçiyor)"
    elif a_te < 0.55:
        verdict = "zayıf/öngörü yok — bu indikatörlerle carry seçimi yapılamaz"
    else:
        verdict = "sınırda; canlı veri biriktikçe tekrar bak"

    out = {"generated_at": pd.Timestamp.now("UTC").isoformat(), "rows": int(len(data)),
           "scans": int(scans), "symbols": int(data.symbol.nunique()),
           "target": "sonraki taramada ann_30d, o taramanın medyanının üstünde mi",
           "features": have, "weights": weights, "top": ranked[:5],
           "auc_train": round(a_tr, 3), "auc_test": round(a_te, 3),
           "auc_naive_persistence": round(a_naive, 3),
           "model_katki": round(a_te - a_naive, 3),
           "base_rate_test": round(base, 3),
           "high_conf_rate": round(hi_rate, 3), "low_conf_rate": round(lo_rate, 3),
           "verdict": verdict}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"eğitim AUC {a_tr:.3f} · test AUC {a_te:.3f} · taban oran {base:.1%}")
    print(f"naif kalıcılık AUC {a_naive:.3f} · model katkısı {a_te - a_naive:+.3f}")
    print(f"yüksek güven dilimi: %{hi_rate*100:.0f} · düşük güven: %{lo_rate*100:.0f}")
    print("en etkili indikatörler:", ", ".join(f"{k} {v:+.3f}" for k, v in ranked[:5]))
    print("karar:", verdict)
    print("kayıt:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
