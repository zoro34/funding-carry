#!/usr/bin/env python3
"""Oynaklık rejimi — ölçülmüş tek işe yarar sinyal (24s büyük hareket AUC ~0.61).

Ne yapar:
  * data/*_1h.csv dosyalarından sembol başına son 24s / 168s oynaklığı hesaplar
  * rejim: sakin / normal / yüksek (168s dağılımına göre yüzdelik)
  * öneri: yüksek oynaklıkta daha küçük tutar, daha geniş stop (aynı risk için)

Kullanim: vol.regime("BTCUSDT") -> dict | vol.all_regimes() -> {sym: dict}
Kanonik test: make test (smoke_risk.py)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
BASE_NOTIONAL = 100.0      # sakin rejimde önerilen tutar
BASE_STOP_PCT = 4.2        # ölçülen çeyrek


@lru_cache(maxsize=64)
def _series(symbol: str) -> pd.Series | None:
    p = DATA / f"{symbol}_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, usecols=["open_time", "close"], parse_dates=["open_time"])
    df = df.set_index("open_time").sort_index()
    return np.log(df["close"]).diff().dropna()


def _pct_rank(values: pd.Series, x: float) -> float:
    if len(values) == 0:
        return 50.0
    return float((values <= x).mean() * 100)


def regime(symbol: str) -> dict:
    """Tek sembol için oynaklık rejimi + pratik öneriler."""
    r = _series(symbol)
    if r is None or len(r) < 200:
        return {"symbol": symbol, "available": False, "reason": "1h veri yok"}
    vol_24 = float(r.tail(24).std() * np.sqrt(24) * 100)      # günlük %
    vol_168 = float(r.tail(168).std() * np.sqrt(24) * 100)
    hist = r.rolling(24).std().dropna() * np.sqrt(24) * 100
    pct = _pct_rank(hist, vol_24)
    if pct >= 75:
        label, factor = "yüksek", 0.6
    elif pct <= 25:
        label, factor = "sakin", 1.3
    else:
        label, factor = "normal", 1.0
    return {"symbol": symbol, "available": True, "vol_24h_pct": round(vol_24, 2),
            "vol_7d_pct": round(vol_168, 2), "percentile": round(pct, 0),
            "regime": label, "size_factor": factor,
            "suggested_notional": round(BASE_NOTIONAL * factor),
            "suggested_stop_pct": round(BASE_STOP_PCT / factor, 2)}


def all_regimes(symbols: list[str] | None = None) -> dict:
    if symbols is None:
        symbols = sorted(p.stem.replace("_1h", "") for p in DATA.glob("*_1h.csv"))
    rows = [regime(s) for s in symbols]
    return {"rows": [r for r in rows if r.get("available")],
            "missing": [r["symbol"] for r in rows if not r.get("available")],
            "note": "Rejim 168s dağılımına göre yüzdelikle belirlenir. Yüksek oynaklıkta tutar küçültülür, stop genişletilir."}


def suggest(symbol: str) -> dict | None:
    r = regime(symbol)
    return r if r.get("available") else None


if __name__ == "__main__":
    d = all_regimes()
    for r in d["rows"]:
        print(f"{r['symbol']:<11} {r['regime']:<7} p{r['percentile']:.0f} "
              f"vol24 {r['vol_24h_pct']:.2f}% · öneri {r['suggested_notional']}$ "
              f"stop {r['suggested_stop_pct']}%")
