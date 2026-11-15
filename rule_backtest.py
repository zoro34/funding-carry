#!/usr/bin/env python3
"""AL/SAT kuralini GECMIS veriyle test et — 1 hafta beklemeye gerek yok.

Yontem (look-ahead YOK):
  Her saat t icin SADECE t'ye kadar olan veriyle kurali calistir:
    - 24s fiyat degisimi
    - 24s net taker akisi (2*taker_buy_quote - quote_volume)
    - 24s balina izi (1h dilimlerde islem buyuklugunun ust %2'si)
    - OI 24s degisimi, buyuk oyuncu/retail long farki
  Sonra t+4s ve t+24s fiyat getirisine bak (bu, karar anindan SONRA).

KONTROL GRUBU (en onemli kisim):
  Ayni sembollerde rastgele zamanlarda giris yapmis gibi davranip ayni ileri
  getirileri olc. "AL sinyali" bu rastgele dagilimin %95'inden iyi degilse
  kuralin olculen bir ustunlugu yoktur.

Sinir: Binance gecmis OI/pozisyon verisini ~20 gun tutuyor (limit 500 x 1h).
"""
from __future__ import annotations

import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import flow
import screener

DATA = Path(__file__).parent / "data"
LOOKBACK = 24          # 24 saat
FORWARD = (4, 24)      # olculecek ileri ufuklar (saat)


def load_symbol(sym: str, limit: int = 500) -> pd.DataFrame | None:
    """1h klines + OI + pozisyon oranlarini tek tabloda birlestir."""
    kl = flow._get(f"{flow.FAPI}/fapi/v1/klines", symbol=sym, interval="1h", limit=limit)
    oi = flow._get(f"{flow.FAPI}/futures/data/openInterestHist",
                   symbol=sym, period="1h", limit=min(limit, 500))
    top = flow._get(f"{flow.FAPI}/futures/data/topLongShortPositionRatio",
                    symbol=sym, period="1h", limit=min(limit, 500))
    glob = flow._get(f"{flow.FAPI}/futures/data/globalLongShortAccountRatio",
                     symbol=sym, period="1h", limit=min(limit, 500))
    if not (kl and oi and top and glob):
        return None

    k = pd.DataFrame(kl, columns=["open_time", "open", "high", "low", "close", "volume",
                                  "close_time", "quote_volume", "trades",
                                  "taker_buy_base", "taker_buy_quote", "ignore"])
    for c in ("open", "close", "quote_volume", "taker_buy_quote"):
        k[c] = k[c].astype(float)
    k["trades"] = k["trades"].astype(int)
    k["ts"] = pd.to_datetime(k["open_time"], unit="ms", utc=True)
    k = k[["ts", "close", "quote_volume", "taker_buy_quote", "trades"]]

    def frame(rows, key, name, scale=1.0):
        df = pd.DataFrame([{"ts": pd.to_datetime(r["timestamp"], unit="ms", utc=True),
                            name: float(r[key]) * scale} for r in rows])
        return df

    d = k.merge(frame(oi, "sumOpenInterestValue", "oi", 1 / 1e6), on="ts", how="inner")
    d = d.merge(frame(top, "longAccount", "top_long", 100), on="ts", how="inner")
    d = d.merge(frame(glob, "longAccount", "retail_long", 100), on="ts", how="inner")
    return d.sort_values("ts").reset_index(drop=True) if len(d) > LOOKBACK + max(FORWARD) else None


def rule_at(d: pd.DataFrame, i: int) -> dict | None:
    """i. barda, SADECE gecmise bakarak kural girdilerini uret."""
    if i < LOOKBACK:
        return None
    w = d.iloc[i - LOOKBACK + 1:i + 1]          # son 24 bar (i dahil)
    prev = d.iloc[i - LOOKBACK]

    price_chg = (d["close"].iloc[i] / prev["close"] - 1) * 100
    net = (2 * w["taker_buy_quote"].sum() - w["quote_volume"].sum()) / 1e6

    avg = (w["quote_volume"] / w["trades"].replace(0, pd.NA)).fillna(0.0)
    thr = max(float(avg.quantile(0.98)), 1.05 * float(avg.median() or 1.0))
    mask = avg >= thr
    whale_net = float((2 * w.loc[mask, "taker_buy_quote"] - w.loc[mask, "quote_volume"]).sum()) / 1e6

    oi_chg = (d["oi"].iloc[i] / prev["oi"] - 1) * 100 if prev["oi"] else 0.0
    gap = d["top_long"].iloc[i] - d["retail_long"].iloc[i]
    vol24 = float(w["quote_volume"].sum()) / 1e6        # 24s hacim ($M)

    return {"price_chg": price_chg, "net_24h_musd": net, "whale_net_musd": whale_net,
            "oi_chg_24h": oi_chg, "top_retail_gap": gap, "vol_24h_musd": vol24,
            "deep": True}


def fwd_returns(d: pd.DataFrame, i: int) -> dict:
    out = {}
    for h in FORWARD:
        j = i + h
        out[h] = (d["close"].iloc[j] / d["close"].iloc[i] - 1) * 100 if j < len(d) else None
    return out


def main() -> int:
    random.seed(7)
    t0 = time.time()

    # likit semboller (universe onbelleginden)
    import json
    cache = json.loads((DATA / "universe_cache.json").read_text(encoding="utf-8"))
    syms = [s for s, v in cache["rows"] if v >= 300][:10]

    print(f"Kural testi — {len(syms)} sembol, son ~20 gun (1h)")
    print(f"Semboller: {', '.join(syms)}\n")

    data = {}
    for s in syms:
        d = load_symbol(s)
        if d is not None:
            data[s] = d
        time.sleep(0.2)
    print(f"veri yuklendi: {len(data)}/{len(syms)} sembol\n")
    if not data:
        print("Veri yok — cikiliyor.")
        return 1

    signals = {"AL": [], "SAT": [], "BEKLE": []}
    randoms = {h: [] for h in FORWARD}
    per_symbol = {}

    for sym, d in data.items():
        cnt = {"AL": 0, "SAT": 0, "BEKLE": 0}
        for i in range(LOOKBACK, len(d) - max(FORWARD)):
            r = rule_at(d, i)
            if r is None:
                continue
            # rastgele kontrol: her barda ileri getiriyi topla
            f = fwd_returns(d, i)
            for h in FORWARD:
                if f[h] is not None:
                    randoms[h].append(f[h])

            sig, cond, rea, score = screener.decide(r)
            cnt[sig] = cnt.get(sig, 0) + 1
            if sig in ("AL", "SAT") and all(f[h] is not None for h in FORWARD):
                signals[sig].append({"symbol": sym, **r, **{f"f{h}": f[h] for h in FORWARD}})
        per_symbol[sym] = cnt

    print(f"{'Sembol':<14}{'AL':>6}{'SAT':>6}{'BEKLE':>7}")
    print("-" * 33)
    for s, c in per_symbol.items():
        print(f"{s:<14}{c.get('AL',0):>6}{c.get('SAT',0):>6}{c.get('BEKLE',0):>7}")

    print("\n" + "=" * 74)
    print("ILERI GETIRILER — sinyal basina (karar anindan SONRA, % olarak)")
    print("=" * 74)
    rows = []
    for sig, items in signals.items():
        if not items:
            print(f"{sig:<6} sinyal yok")
            continue
        for h in FORWARD:
            vals = [x[f"f{h}"] for x in items]
            hit = sum(1 for v in vals if v > 0) / len(vals)
            rows.append((sig, h, len(vals), statistics.mean(vals),
                         statistics.median(vals), hit))
            print(f"{sig:<6} +{h:>2}h  n={len(vals):<4} ort {statistics.mean(vals):>+6.2f}%  "
                  f"medyan {statistics.median(vals):>+6.2f}%  yukari oran {hit:>5.1%}")

    print("\n" + "=" * 74)
    print("RASTGELE GIRIS KONTROLU (ayni semboller, ayni ufuklar)")
    print("=" * 74)
    for h in FORWARD:
        v = sorted(randoms[h])
        if not v:
            continue
        p5, p50, p95 = v[int(len(v) * .05)], v[len(v) // 2], v[int(len(v) * .95)]
        print(f"+{h:>2}h  rastgele ort {statistics.mean(v):>+6.2f}%  "
              f"p5 {p5:>+6.2f}%  medyan {p50:>+6.2f}%  p95 {p95:>+6.2f}%  n={len(v)}")

        for sig, items in signals.items():
            if not items:
                continue
            vals = [x[f"f{h}"] for x in items]
            m = statistics.mean(vals)
            pct = sum(1 for x in v if x < m) / len(v) * 100
            verdict = "RASTGELEDEN AYRI" if (pct >= 95 and len(vals) >= 30) else "gurultu"
            print(f"      {sig} ort {m:>+6.2f}%  -> rastgelelerin %{pct:.0f}'inden iyi   [{verdict}]")

    # rapor
    lines = ["# AL/SAT Kuralı — Geçmiş Veri Testi", "",
             f"- Tarih: {datetime.now(timezone.utc).isoformat()}",
             f"- Sembol: {len(data)} · Pencere: ~20 gün (1h) · Lookback: 24h",
             f"- Kural: `screener.decide` (aynı kod, aynı eşikler)",
             "", "## Sinyal sayıları", "",
             "| Sembol | AL | SAT | BEKLE |", "|---|---|---|---|"]
    for s, c in per_symbol.items():
        lines.append(f"| {s} | {c.get('AL',0)} | {c.get('SAT',0)} | {c.get('BEKLE',0)} |")
    lines += ["", "## İleri getiriler", "",
              "| Sinyal | Ufuk | n | Ortalama | Medyan | Yukarı oran |", "|---|---|---|---|---|---|"]
    for sig, h, n, mean, med, hit in rows:
        lines.append(f"| {sig} | +{h}h | {n} | {mean:+.2f}% | {med:+.2f}% | {hit:.1%} |")
    lines += ["", "## Rastgele kontrol", ""]
    for h in FORWARD:
        v = sorted(randoms[h])
        if not v:
            continue
        lines.append(f"- +{h}h rastgele: ort {statistics.mean(v):+.2f}%, "
                     f"p5 {v[int(len(v)*.05)]:+.2f}%, p95 {v[int(len(v)*.95)]:+.2f}%")
        for sig, items in signals.items():
            if not items:
                continue
            vals = [x[f"f{h}"] for x in items]
            m = statistics.mean(vals)
            pct = sum(1 for x in v if x < m) / len(v) * 100
            lines.append(f"  - {sig}: {m:+.2f}% → rastgelelerin %{pct:.0f}'inden iyi")

    out = DATA.parent / "reports" / "rule_backtest.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    # Panelin gostermesi icin makine-okunur ozet
    import json as _json
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": len(data),
        "signals": {sig: len(items) for sig, items in signals.items()},
        "forward": {},
        "random": {},
    }
    for sig, items in signals.items():
        if not items:
            continue
        summary["forward"][sig] = {}
        for h in FORWARD:
            vals = [x[f"f{h}"] for x in items]
            summary["forward"][sig][f"h{h}"] = {
                "n": len(vals),
                "mean": round(statistics.mean(vals), 3),
                "median": round(statistics.median(vals), 3),
                "up_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4),
            }
    for h in FORWARD:
        v = sorted(randoms[h])
        if not v:
            continue
        summary["random"][f"h{h}"] = {
            "n": len(v),
            "mean": round(statistics.mean(v), 3),
            "p5": round(v[int(len(v) * .05)], 3),
            "p95": round(v[int(len(v) * .95)], 3),
        }
        for sig, items in signals.items():
            if not items:
                continue
            vals = [x[f"f{h}"] for x in items]
            m = statistics.mean(vals)
            pct = sum(1 for x in v if x < m) / len(v) * 100
            summary["forward"][sig][f"h{h}"]["percentile"] = round(pct, 1)
            summary["forward"][sig][f"h{h}"]["beats_random"] = bool(pct >= 95 and len(vals) >= 30)

    (DATA.parent / "reports" / "rule_backtest.json").write_text(
        _json.dumps(summary, indent=1), encoding="utf-8")

    print(f"\nrapor: {out}")
    print(f"sure: {time.time() - t0:.0f}s · istek: {flow.STATS['requests']} "
          f"· retry: {flow.STATS['retries']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
