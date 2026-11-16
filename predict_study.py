#!/usr/bin/env python3
"""Tahmin gücü çalışması — "daha doğru tahmin mümkün mü?" sorusunu ÖLÇEREK yanıtlar.

Yöntem (look-ahead yok):
  * 1h mumlardan yalnızca geçmişe bakan özellikler (getiri, oynaklık, hacim z-skoru,
    RSI, EMA mesafesi, 24s aralık konumu, saat döngüsü, BTC rejimi)
  * Hedef: 24 saat sonra getiri > 0 (yön) ve |getiri| > %1 (büyük hareket)
  * Genişleyen pencere walk-forward (6 dilim), her dilimde yeniden eğitim
  * Naif tabanlar: "hep yukarı", momentum (24s yön), mean-reversion (24s tersi)
  * İşlem maliyeti %0.30 tur (giriş+çıkış) düşülerek net beklenti

Çıktı: reports/predict_study.json + konsol özeti.
Kanonik: .venv/bin/python predict_study.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
HORIZON = int(os.environ.get("HZ", "24"))   # saat: 24 = 1 gün, 168 = 7 gün
OUT = ROOT / "reports" / f"predict_study_{HORIZON}h.json"
COST_PCT = 0.30          # tur maliyeti (%)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logit(X, y, epochs=1500, lr=0.08, l2=1e-3):
    n, d = X.shape
    w = np.zeros(d + 1)
    Xb = np.hstack([np.ones((n, 1)), X])
    for _ in range(epochs):
        p = sigmoid(Xb @ w)
        g = Xb.T @ (p - y) / n
        g[1:] += l2 * w[1:]
        w -= lr * g
    return w


def predict_logit(w, X):
    return sigmoid(np.hstack([np.ones((len(X), 1)), X]) @ w)


def auc(y, p):
    y = np.asarray(y)
    order = np.argsort(p)
    y = y[order]
    pos, neg = y.sum(), len(y) - y.sum()
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = np.arange(1, len(y) + 1)
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))


def build(sym_files: dict[str, Path]) -> pd.DataFrame:
    frames = []
    for sym, path in sym_files.items():
        df = pd.read_csv(path, parse_dates=["open_time"])
        df = df.set_index("open_time").sort_index()
        c, h, l, v = df["close"], df["high"], df["low"], df["quote_volume"]
        x = pd.DataFrame(index=df.index)
        x["symbol"] = sym
        x["close"] = c
        for k in (1, 4, 24, 72):
            x[f"ret_{k}"] = np.log(c / c.shift(k))
        r1 = x["ret_1"]
        x["vol_24"] = r1.rolling(24).std()
        x["vol_168"] = r1.rolling(168).std()
        x["vol_ratio"] = x["vol_24"] / (x["vol_168"] + 1e-12)
        x["range_pos"] = (c - l.rolling(24).min()) / (h.rolling(24).max() - l.rolling(24).min() + 1e-12)
        x["vol_z"] = (v - v.rolling(168).mean()) / (v.rolling(168).std() + 1e-12)
        x["rsi_14"] = rsi(c)
        x["ema_dist"] = c / c.ewm(span=50, adjust=False).mean() - 1
        x["atr_pct"] = ((h - l) / c).rolling(24).mean() * 100
        x["hour_sin"] = np.sin(2 * np.pi * x.index.hour / 24)
        x["hour_cos"] = np.cos(2 * np.pi * x.index.hour / 24)
        x["fwd"] = c.shift(-HORIZON) / c - 1
        frames.append(x)
    panel = pd.concat(frames).dropna()

    # BTC rejimi (aynı zaman ekseni)
    btc = panel[panel.symbol == "BTCUSDT"][["ret_24", "vol_24"]]
    btc.columns = ["btc_ret_24", "btc_vol_24"]
    panel = panel.join(btc, on="open_time" if "open_time" in panel.columns else panel.index.name)
    return panel.dropna()


FEATURES = ["ret_1", "ret_4", "ret_24", "ret_72", "vol_24", "vol_ratio", "range_pos",
            "vol_z", "rsi_14", "ema_dist", "atr_pct", "hour_sin", "hour_cos",
            "btc_ret_24", "btc_vol_24"]


def walk_forward(panel: pd.DataFrame, target: str, threshold_pct: float | None = None):
    p = panel.sort_values(panel.index.name)
    y = (p["fwd"] > 0).astype(int).to_numpy() if threshold_pct is None else \
        (p["fwd"].abs() > threshold_pct / 100).astype(int).to_numpy()
    ts = p.index
    folds = 6
    cut_points = [int(len(p) * (0.4 + 0.1 * i)) for i in range(folds + 1)]
    rows = []
    for i in range(folds):
        tr_end, te_end = cut_points[i], cut_points[i + 1]
        tr = slice(0, tr_end)
        te = slice(tr_end, te_end)
        Xtr = p.iloc[tr][FEATURES].to_numpy(float)
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
        w = fit_logit((Xtr - mu) / sd, y[tr])
        p_te = predict_logit(w, (p.iloc[te][FEATURES].to_numpy(float) - mu) / sd)
        y_te = y[te]
        fwd_te = p.iloc[te]["fwd"].to_numpy(float)

        # model: olasılık > 0.5 ise al
        take = p_te > 0.5
        hit_model = float((fwd_te[take] > 0).mean()) if take.any() else float("nan")
        net_model = float((fwd_te[take] * 100 - COST_PCT).mean()) if take.any() else float("nan")

        # naif: momentum (ret_24 > 0 → al)
        mom = p.iloc[te]["ret_24"].to_numpy(float) > 0
        hit_mom = float((fwd_te[mom] > 0).mean()) if mom.any() else float("nan")
        net_mom = float((fwd_te[mom] * 100 - COST_PCT).mean()) if mom.any() else float("nan")

        # naif: hep al
        hit_all = float((fwd_te > 0).mean())
        net_all = float((fwd_te * 100 - COST_PCT).mean())

        rows.append({"fold": i + 1, "n_test": int(te_end - tr_end),
                     "auc": round(auc(y_te, p_te), 3),
                     "model_hit": round(hit_model, 3), "model_net_pct": round(net_model, 3),
                     "model_trades": int(take.sum()),
                     "mom_hit": round(hit_mom, 3), "mom_net_pct": round(net_mom, 3),
                     "all_hit": round(hit_all, 3), "all_net_pct": round(net_all, 3)})
    return rows


def main() -> int:
    files = {p.stem.replace("_1h", ""): p for p in sorted((ROOT / "data").glob("*_1h.csv"))}
    if len(files) < 4:
        print("yetersiz veri:", len(files), "sembol")
        return 1
    print(f"{len(files)} sembol: {', '.join(files)}")
    panel = build(files)
    print(f"panel: {len(panel)} satır · {panel.index.min()} → {panel.index.max()}")

    dir_rows = walk_forward(panel, "yön")
    big_rows = walk_forward(panel, "büyük hareket", threshold_pct=1.0)

    def summ(rows):
        m = pd.DataFrame(rows)
        return {"auc_ort": round(float(m.auc.mean()), 3),
                "model_hit": round(float(m.model_hit.mean()), 3),
                "model_net_pct": round(float(m.model_net_pct.mean()), 3),
                "mom_hit": round(float(m.mom_hit.mean()), 3),
                "mom_net_pct": round(float(m.mom_net_pct.mean()), 3),
                "all_hit": round(float(m.all_hit.mean()), 3),
                "all_net_pct": round(float(m.all_net_pct.mean()), 3)}

    out = {"generated_at": pd.Timestamp.now("UTC").isoformat(),
           "symbols": list(files), "rows": int(len(panel)),
           "period": [str(panel.index.min()), str(panel.index.max())],
           "features": FEATURES, "horizon_h": HORIZON, "cost_pct": COST_PCT,
           "yon": {"folds": dir_rows, "ozet": summ(dir_rows)},
           "buyuk_hareket": {"folds": big_rows, "ozet": summ(big_rows)}}

    s = out["yon"]["ozet"]
    best_naive = max(s["mom_net_pct"], s["all_net_pct"])
    if s["model_net_pct"] <= 0:
        verdict = ("maliyet sonrasi net getiri NEGATIF — tahmin guclu olsa bile islem yapilamaz "
                   f"(model {s['model_net_pct']:+.3f}%, naif {best_naive:+.3f}%)")
    elif s["model_net_pct"] < best_naive + 0.10:
        verdict = ("model naif kurallardan anlamli farkla iyi degil (kazanc < 0.10 puan) "
                   f"(model {s['model_net_pct']:+.3f}%, naif {best_naive:+.3f}%)")
    else:
        verdict = "model naif kurallari GECIYOR (net pozitif ve anlamli fark)"
    out["verdict"] = verdict
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nYÖN (24s) — {HORIZON}s ufuk, maliyet %{COST_PCT}")
    print(f"  model:  isabet {s['model_hit']:.1%} · net {s['model_net_pct']:+.3f}% · AUC {s['auc_ort']}")
    print(f"  momentum: isabet {s['mom_hit']:.1%} · net {s['mom_net_pct']:+.3f}%")
    print(f"  hep al:   isabet {s['all_hit']:.1%} · net {s['all_net_pct']:+.3f}%")
    b = out["buyuk_hareket"]["ozet"]
    print(f"BÜYÜK HAREKET (|24s| > %1): model AUC {b['auc_ort']} · isabet {b['model_hit']:.1%}")
    print("karar:", verdict)
    print("kayıt:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
