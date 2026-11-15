#!/usr/bin/env python3
"""Funding-rate arbitrajini gercek Binance verisiyle olc.

MANTIK (delta-notr carry):
  Spot BTC al  +  ayni miktar vadeli BTC short ac
  -> fiyat yonunden notr (biri yukselirse digeri duser)
  -> funding POZITIF oldugunda long'lar short'lara oder, yani SEN alirsin.
  Bu bir yon tahmini degil, bir "kira" geliri.

MALIYETLER (giris+cikis, iki bacak):
  spot taker %0.1 x2  +  vadeli taker %0.05 x2  = ~%0.30 tur

Bu, 2 yillik gercek funding verisiyle olculuyor.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import requests

FAPI = "https://fapi.binance.com/fapi/v1/fundingRate"
DATA = Path(__file__).parent / "data"


def fetch_funding(symbol: str, days: int = 730) -> pd.DataFrame:
    start = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    rows, cursor = [], start
    s = requests.Session()
    while True:
        r = s.get(FAPI, params={"symbol": symbol, "startTime": cursor, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        cursor = batch[-1]["fundingTime"] + 1
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["rate"] = df["fundingRate"].astype(float)
    return df[["time", "rate"]].sort_values("time").reset_index(drop=True)


SPOT_FEE = 0.001      # x2 bacak (giris + cikis)
FUT_FEE = 0.0005      # x2 bacak
ROUND_TRIP = 2 * SPOT_FEE + 2 * FUT_FEE

print("=" * 78)
print("FUNDING-RATE ARBITRAJI — gercek veri")
print("=" * 78)
print(f"Tur maliyeti (giris+cikis, iki bacak): {ROUND_TRIP:.3%}\n")

summary = {}
for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
    try:
        df = fetch_funding(sym)
    except Exception as e:
        print(f"{sym}: veri alinamadi ({e})")
        continue
    if df.empty:
        print(f"{sym}: veri yok")
        continue

    n = len(df)
    days = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 86400
    per_year = n / (days / 365.25)

    cum = df["rate"].sum()                      # toplam funding getirisi (notr)
    ann_simple = cum / (days / 365.25)          # yillik basit
    mean_bps = df["rate"].mean() * 1e4
    pos_share = (df["rate"] > 0).mean()

    # negatif funding serileri: short perp'in cebinden cikan donemler
    neg = df[df["rate"] < 0]
    worst = df["rate"].min()

    # stratejik varyant: sadece son 3 periyot ortalamasi pozitifken gir
    trail = df["rate"].rolling(3).mean()
    filt = df["rate"].where(trail > 0, 0.0)
    cum_filt = filt.sum()
    ann_filt = cum_filt / (days / 365.25)

    summary[sym] = dict(n=n, days=days, cum=cum, ann=ann_simple, mean_bps=mean_bps,
                        pos=pos_share, ann_filt=ann_filt, worst=worst,
                        neg_n=len(neg), per_year=per_year)

    print(f"--- {sym} ---")
    print(f"  periyot           : {n:,} ({per_year:.0f}/yil, 8 saatlik)  {days:.0f} gun")
    print(f"  pozitif orani     : {pos_share:.1%}")
    print(f"  ort. funding      : {mean_bps:+.3f} bps / 8s")
    print(f"  KUMULATIF funding : {cum:+.2%}  (notr pozisyonun 2 yillik getirisi)")
    print(f"  YILLIK (basit)    : {ann_simple:+.2%}")
    print(f"  en kotu periyot   : {worst:+.4%}   (negatif periyot: {len(neg):,})")
    print(f"  filtreli varyant  : {ann_filt:+.2%}/yil (sadece trend pozitifken gir)")
    print(f"  tur maliyeti      : {ROUND_TRIP:.2%}  -> tek giris-cikis maliyeti")
    print(f"  NET (tek giris/cikis, 2 yil elde tutma): {cum - ROUND_TRIP:+.2%}")
    print()

print("=" * 78)
print("KARSILASTIRMA — strateji aileleri (ayni 2 yil, gercek veri)")
print("=" * 78)
print(f"{'Yaklasim':<34}{'Yillik':>10}{'Yon tahmini':>14}{'Durum':>12}")
print("-" * 70)
print(f"{'Al-tut (BTC)':<34}{-0.1519:>+9.2%}{'gerekmez':>14}{'kayip':>12}")
print(f"{'Formasyon botu (en iyi)':<34}{0.0444/2:>+9.2%}{'gerekir':>14}{'kanit yok':>12}")
for sym, s in summary.items():
    print(f"{f'Funding carry ({sym})':<34}{s['ann']:>+9.2%}{'gerekmez':>14}{'pozitif':>12}")

if summary:
    print("\n" + "=" * 78)
    print("DEGERLENDIRME")
    print("=" * 78)
    best = max(summary.items(), key=lambda kv: kv[1]["ann"])
    print(f"\nEn yuksek: {best[0]} — yillik {best[1]['ann']:+.2%}")
    print(f"Tum sembollerde funding getirisi pozitif mi: "
          f"{all(s['ann'] > 0 for s in summary.values())}")
    print(f"\nNotr pozisyonun gercek riskleri:")
    print(f"  - Borsa riski (karşı taraf), teminat yonetimi, likidasyon (kaldiracli ise)")
    print(f"  - Funding NEGATIFE donebilir: {sum(s['neg_n'] for s in summary.values()):,} kez oldu")
    print(f"  - Getiri dusuk: yillik %5-15 bandi. Buyuk sermaye gerektirir ki anlamli olsun.")
    print(f"  - Kaldiracla buyutulurse likidasyon riski -> 'notr' olmaktan cikar")
