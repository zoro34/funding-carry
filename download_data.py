#!/usr/bin/env python3
"""Binance spot kline indirici.

Sayfalama: Binance /api/v3/klines tek istekte en fazla 1000 mum verir.
startTime'i son mumun kapanis zamanina kaydirarak ilerliyoruz.
"""
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.binance.com/api/v3/klines"
DATA_DIR = Path(__file__).parent / "data"

COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int | None = None) -> pd.DataFrame:
    rows: list[list] = []
    cursor = start_ms
    session = requests.Session()
    session.headers.update({"User-Agent": "binance-backtest/1.0"})

    while True:
        params = {"symbol": symbol, "interval": interval, "limit": 1000, "startTime": cursor}
        if end_ms:
            params["endTime"] = end_ms

        for attempt in range(4):
            try:
                r = session.get(BASE, params=params, timeout=20)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 5))
                    print(f"  rate limit, {wait}s bekleniyor...", flush=True)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                batch = r.json()
                break
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                print(f"  hata ({e}), tekrar deneniyor...", flush=True)
                time.sleep(2 * (attempt + 1))
        else:
            break

        if not batch:
            break

        rows.extend(batch)
        last_close = batch[-1][6]
        print(f"  {len(rows):>6} mum  son: {pd.to_datetime(last_close, unit='ms')}", end="\r", flush=True)

        if len(batch) < 1000:
            break
        cursor = last_close + 1
        if end_ms and cursor >= end_ms:
            break
        time.sleep(0.15)  # weight limitine saygi

    print(flush=True)
    if not rows:
        return pd.DataFrame(columns=COLS)

    df = pd.DataFrame(rows, columns=COLS)
    df = df.drop(columns=["ignore"])
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[c] = df[c].astype(float)
    df["trades"] = df["trades"].astype(int)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--days", type=int, default=730)
    args = p.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    start_ms = int((pd.Timestamp.utcnow() - pd.Timedelta(days=args.days)).timestamp() * 1000)

    print(f"{args.symbol} {args.interval} — {args.days} gun indiriliyor")
    df = fetch_klines(args.symbol, args.interval, start_ms)

    if df.empty:
        print("VERI YOK — sembol/interval kontrol et")
        return 1

    out = DATA_DIR / f"{args.symbol}_{args.interval}.csv"
    df.to_csv(out, index=False)

    span_days = (df["open_time"].iloc[-1] - df["open_time"].iloc[0]).days
    print(f"KAYDEDILDI: {out}")
    print(f"  mum: {len(df)}  aralik: {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}  ({span_days} gun)")
    print(f"  fiyat: {df['close'].iloc[0]:.2f} -> {df['close'].iloc[-1]:.2f}  ({df['close'].iloc[-1]/df['close'].iloc[0]-1:+.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
