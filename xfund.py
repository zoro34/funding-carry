#!/usr/bin/env python3
"""Borsalar arası funding karşılaştırması (Binance / Bybit / OKX — public API).

Mantık: aynı sembolde funding oranları borsaya göre farklıdır. Fark büyükse
"bir borsada long spot + diğerinde short perp" gibi gerçek fiyat farkı fırsatı doğar.
Bu modül yalnızca VERİ TOPLAR ve farkı sıralar; emir göndermez.

API'ler (anahtar gerekmez):
  Binance  GET https://fapi.binance.com/fapi/v1/premiumIndex   -> lastFundingRate (8s)
  Bybit    GET https://api.bybit.com/v5/market/tickers?category=linear -> fundingRate (8s)
  OKX      GET https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP (8s)

Funding oranları 8 saatliktir; yıllıklandırma = oran * 3 * 365 * 100.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
TIMEOUT = 12
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
           "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT"]


def _get(url: str, params: dict | None = None) -> dict | list:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "funding-carry/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _annual(rate_pct_8h: float) -> float:
    """8 saatlik oran (%) → yıllık (%)"""
    return rate_pct_8h * 3 * 365


def binance() -> dict[str, float]:
    out = {}
    try:
        for row in _get("https://fapi.binance.com/fapi/v1/premiumIndex"):
            sym = row.get("symbol", "")
            if sym in SYMBOLS:
                out[sym] = float(row.get("lastFundingRate") or 0) * 100
    except Exception:
        pass
    return out


def bybit() -> dict[str, float]:
    out = {}
    try:
        data = _get("https://api.bybit.com/v5/market/tickers", {"category": "linear"})
        for row in (data.get("result") or {}).get("list") or []:
            sym = row.get("symbol", "")
            if sym in SYMBOLS:
                out[sym] = float(row.get("fundingRate") or 0) * 100
    except Exception:
        pass
    return out


def okx(symbols: list[str] | None = None) -> dict[str, float]:
    out = {}
    for sym in (symbols or SYMBOLS):
        inst = sym[:-4] + "-USDT-SWAP"
        try:
            data = _get("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})
            row = (data.get("data") or [{}])[0]
            rate = row.get("fundingRate")
            if rate is not None:
                out[sym] = float(rate) * 100
        except Exception:
            continue
    return out


def compare(symbols: list[str] | None = None) -> dict:
    """Üç borsanın funding'ini karşılaştır, farkı yıllıklandırarak sırala."""
    syms = symbols or SYMBOLS
    b, y, o = binance(), bybit(), okx(syms)
    rows = []
    for s in syms:
        vals = {"binance": b.get(s), "bybit": y.get(s), "okx": o.get(s)}
        have = {k: v for k, v in vals.items() if v is not None}
        if len(have) < 2:
            continue
        hi_k = max(have, key=have.get)
        lo_k = min(have, key=have.get)
        spread_8h = have[hi_k] - have[lo_k]
        rows.append({"symbol": s,
                     "rates_pct_8h": {k: round(v, 5) for k, v in have.items()},
                     "ann_pct": {k: round(_annual(v), 2) for k, v in have.items()},
                     "spread_ann_pct": round(_annual(spread_8h), 2),
                     "long_on": lo_k, "short_on": hi_k})
    rows.sort(key=lambda r: -r["spread_ann_pct"])
    return {"rows": rows, "sources": {"binance": len(b), "bybit": len(y), "okx": len(o)},
            "note": "Fark yıllıklandırılmıştır. Gerçek arbitraj için transfer/marj maliyeti, "
                    "likidite ve borsa riski ayrıca değerlendirilmelidir."}


if __name__ == "__main__":
    d = compare()
    print("kaynak:", d["sources"])
    for r in d["rows"][:10]:
        print(f"{r['symbol']:<11} fark {r['spread_ann_pct']:>+7.2f}%/yıl · "
              f"long {r['long_on']:<8} short {r['short_on']}")
