#!/usr/bin/env python3
"""Funding carry firsat tarayicisi — SADECE PUBLIC VERI, API ANAHTARI YOK.

Ne yapar:
  * Binance vadeli tum sembollerin guncel funding oranini okur
  * Son 30 gunun ortalama funding'ini cikarir (tek seferlik spike'a aldanmamak icin)
  * Spot-perp basis'i hesaplar (primli girmek getiriyi yer)
  * Yillik getiri tahmini + uygunluk skoru uretir
  * Her calismada CSV'ye snapshot yazar (1-2 ay sonra gercek verin olur)

Kullanim:
  .venv/bin/python funding_monitor.py scan
  .venv/bin/python funding_monitor.py scan --min-ann 8 --min-vol 50
  .venv/bin/python funding_monitor.py watch --interval 600 --min-ann 10
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
DATA = Path(__file__).parent / "data"
SNAP = DATA / "funding_snapshots.csv"

# Tur maliyeti: iki bacak, giris + cikis (spot %0.1 x2, vadeli %0.05 x2)
ROUND_TRIP_COST = 2 * 0.001 + 2 * 0.0005

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "funding-monitor/1.0"})


def get(path: str, base: str = FAPI, **params):
    for attempt in range(3):
        try:
            r = SESSION.get(base + path, params=params or None, timeout=15)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return None


def funding_intervals() -> dict[str, int]:
    """Sembol -> funding periyodu (saat). Bazi semboller 4 saatlik."""
    info = get("/fapi/v1/exchangeInfo")
    out = {}
    for s in info.get("symbols", []):
        if s.get("contractType") != "PERPETUAL" or s.get("status") != "TRADING":
            continue
        hrs = s.get("fundingIntervalHours")
        if not hrs:
            # bazi yanitlarda yok; fundingInfo'dan tamamlanir
            hrs = 8
        out[s["symbol"]] = int(hrs)
    try:
        for fi in get("/fapi/v1/fundingInfo") or []:
            if fi.get("fundingIntervalHours"):
                out[fi["symbol"]] = int(fi["fundingIntervalHours"])
    except Exception:
        pass
    return out


def recent_avg_funding(symbol: str, interval_h: int, days: int = 30) -> dict:
    """Son N gunun funding istatistigi.

    Ortalama tek bir spike ile sisirilebilir; bu yuzden 25. persentil de
    dondurulur — 'temkinli' senaryo, yani kotu periyotlarin da hesaba katildigi
    surdurulebilir getiri.
    """
    need = int(days * 24 / interval_h) + 5
    rows = get("/fapi/v1/fundingRate", symbol=symbol, limit=min(need, 1000))
    if not rows:
        return {"mean": 0.0, "n": 0, "pos": 0.0, "p25": 0.0}
    rates = [float(r["fundingRate"]) for r in rows[-need:]]
    s = pd.Series(rates)
    return {
        "mean": float(s.mean()),
        "n": len(rates),
        "pos": float((s > 0).mean()),
        "p25": float(s.quantile(0.25)),
    }


def scan(min_ann: float = 0.0, min_vol: float = 20.0, top: int = 25) -> pd.DataFrame:
    intervals = funding_intervals()
    prem = get("/fapi/v1/premiumIndex") or []
    tickers = {t["symbol"]: t for t in (get("/fapi/v1/ticker/24hr") or [])}

    # 1) hacim filtresini gecen adaylari topla
    cands = []
    for p in prem:
        sym = p["symbol"]
        if sym not in intervals:
            continue
        vol = float(tickers.get(sym, {}).get("quoteVolume", 0) or 0) / 1e6
        if vol < min_vol:
            continue
        cands.append((p, vol))

    # 2) 30 gunluk funding istatistiklerini PARALEL cek (sirali ~26s -> ~4s)
    from concurrent.futures import ThreadPoolExecutor

    def _stats(item):
        p, vol = item
        return item, recent_avg_funding(p["symbol"], intervals[p["symbol"]], days=30)

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_stats, cands))

    # 3) satirlari kur
    rows = []
    for (p, vol), st in results:
        sym = p["symbol"]
        iv = intervals[sym]
        per_year = 365 * 24 / iv
        cur = float(p["lastFundingRate"])
        mark = float(p.get("markPrice", 0) or 0)
        idx = float(p.get("indexPrice", 0) or 0)

        # basis: perp primli mi? (mark > index ise primli)
        basis = (mark / idx - 1) if idx > 0 else 0.0

        ann_cur = cur * per_year
        ann_avg = st["mean"] * per_year
        ann_p25 = st["p25"] * per_year

        # Skor: TEMKINLI yillik getiri (25. persentil) eksi basis primi cezasi.
        # Primli girmek, cikista o primi geri verir -> 2x ceza.
        score = ann_p25 * 100 - max(basis, 0) * 100 * 2
        if ann_avg * 100 < min_ann:
            continue

        # Maliyeti kapatma suresi: tur maliyeti / gunluk getiri (temkinli senaryo)
        daily = ann_p25 if ann_p25 > 0 else ann_avg
        breakeven_days = (ROUND_TRIP_COST / (daily / 365)) if daily > 0 else float("inf")

        rows.append({
            "symbol": sym,
            "interval_h": iv,
            "funding_now_pct": cur * 100,
            "ann_now_pct": ann_cur * 100,
            "ann_30d_pct": ann_avg * 100,
            "ann_p25_pct": ann_p25 * 100,
            "pos_share_pct": st["pos"] * 100,
            "basis_pct": basis * 100,
            "vol_musd": vol,
            "samples": st["n"],
            "breakeven_days": breakeven_days,
            "score": score,
            "next_funding": datetime.fromtimestamp(p.get("nextFundingTime", 0) / 1000, tz=timezone.utc),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("score", ascending=False).head(top).reset_index(drop=True)


def save_snapshot(df: pd.DataFrame) -> None:
    if df.empty:
        return
    DATA.mkdir(exist_ok=True)
    new = not SNAP.exists()
    d = df.copy()
    d.insert(0, "scanned_at", datetime.now(timezone.utc).isoformat())
    with SNAP.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(d.columns))
        if new:
            w.writeheader()
        for rec in d.to_dict("records"):
            w.writerow(rec)


def print_table(df: pd.DataFrame, min_ann: float) -> None:
    if df.empty:
        print("Uygun firsat yok (filtreleri gevset: --min-ann / --min-vol)")
        return

    print(f"{'Sembol':<13}{'Per':>4}{'Suan%':>8}{'Ort.yil%':>10}{'Temkinli%':>11}"
          f"{'Pozitif%':>9}{'Basis%':>8}{'HacimM$':>9}{'Gun':>6}")
    print("-" * 78)
    for _, r in df.iterrows():
        # GIRIS UYGUNLUGU: gecmis iyi + anlik funding negatif degil + basis primli degil
        ok = r["ann_p25_pct"] > 0 and r["basis_pct"] < 0.3 and r["funding_now_pct"] >= 0
        flag = "  OK" if ok else ("  anlik-negatif" if r["funding_now_pct"] < 0 else "")
        print(f"{r['symbol']:<13}{r['interval_h']:>3}h{r['funding_now_pct']:>+8.4f}"
              f"{r['ann_30d_pct']:>+10.2f}{r['ann_p25_pct']:>+11.2f}"
              f"{r['pos_share_pct']:>9.0f}{r['basis_pct']:>+8.3f}"
              f"{r['vol_musd']:>9.0f}{r['breakeven_days']:>6.1f}{flag}")

    print("\nKolonlar:")
    print("  Ort.yil%    : son 30 gunun ortalama funding'i, yilliga cevrilmis")
    print("  Temkinli%   : 25. persentil — kotu periyotlar dahil, surdurulebilir getiri")
    print("  Pozitif%    : son 30 gunde funding'in pozitif oldugu periyot orani")
    print("  Basis%      : pozitifse perp primli -> primli girmek getiriyi yer")
    print("  Gun         : tur maliyetini (%0.30) kapatmak icin gereken gun (temkinli)")
    print("\nUYARI: cok yuksek funding = asiri kalabalik long pozisyon = dusus riski.")
    print("       Funding yuksekken girmek, coin cokerken ters pozisyonda yakalanmak demek.")


def one_scan(args) -> pd.DataFrame:
    print(f"Tarama: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    df = scan(min_ann=args.min_ann, min_vol=args.min_vol, top=args.top)
    print_table(df, args.min_ann)
    save_snapshot(df)
    print(f"\nSnapshot: {SNAP}")
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="tek tarama")
    s.add_argument("--min-ann", type=float, default=0.0, help="min 30g yillik %%")
    s.add_argument("--min-vol", type=float, default=20.0, help="min 24s hacim (milyon USDT)")
    s.add_argument("--top", type=int, default=25)

    w = sub.add_parser("watch", help="periyodik izleme + alarm")
    w.add_argument("--interval", type=int, default=600, help="saniye")
    w.add_argument("--min-ann", type=float, default=10.0)
    w.add_argument("--min-vol", type=float, default=20.0)
    w.add_argument("--top", type=int, default=25)
    w.add_argument("--once", action="store_true", help="tek tur calis ve cik")

    args = ap.parse_args()

    if args.cmd == "scan":
        one_scan(args)
        return 0

    print(f"Izleme modu: her {args.interval}s, esik %{args.min_ann}/yil. Ctrl+C ile cik.")
    seen: set[str] = set()
    try:
        while True:
            df = scan(min_ann=args.min_ann, min_vol=args.min_vol, top=args.top)
            save_snapshot(df)
            good = df[(df["ann_p25_pct"] >= args.min_ann) & (df["basis_pct"] < 0.3)] if not df.empty else df
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            if good is not None and not good.empty:
                fresh = [r["symbol"] for _, r in good.iterrows() if r["symbol"] not in seen]
                print(f"[{ts}] {len(good)} firsat" + (f" | YENI: {', '.join(fresh)}" if fresh else ""))
                for _, r in good.head(5).iterrows():
                    print(f"        {r['symbol']:<12} temkinli {r['ann_p25_pct']:>+6.2f}%/yil  "
                          f"basis {r['basis_pct']:+.3f}%  hacim {r['vol_musd']:.0f}M$  "
                          f"maliyet kapanma {r['breakeven_days']:.1f} gun")
                seen.update(r["symbol"] for _, r in good.iterrows())
            else:
                print(f"[{ts}] uygun firsat yok")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nDurduruldu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
