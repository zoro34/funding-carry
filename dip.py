#!/usr/bin/env python3
"""Dip tarayicisi + OLCULMUS kisa vade beklentisi.

Ne yapar:
  1) Her sembolde "dip" tanimi uygular: fiyat son 7 gunun araliginin alt %10'unda
     VE 24s degisim <= -3%.
  2) Bu kosulu SAGLAYAN gecmis barlarin +4s / +24s getirisini olcer; ayni sembolun
     TUM barlarinin getirisiyle (kontrol) karsilastirir.
  3) Canli tarama: su an diplik skoru + olculmus beklenti araligi (vaat degil).

Cikti: reports/dip_study.json + reports/dip_study.md, canli satirlar (panel icin).

Neden boyle: "dipten al" bir inanc; olculebilir hale getirince ya kanit cikar ya
"rastgeleden farksiz" cikar. Ikisi de cevaptir, ikincisi daha sik cikar.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import flow

ROOT = Path(__file__).parent
OUT_JSON = ROOT / "reports" / "dip_study.json"
OUT_MD = ROOT / "reports" / "dip_study.md"

RANGE_H = 168          # 7 gun (1h bar)
LOW_PCT = 10.0         # araligin alt %10'u
DROP_24H = -3.0        # 24s degisim esigi
HORIZONS = (4, 24)
MIN_BARS = 400


def klines_1h(sym: str) -> pd.DataFrame | None:
    d = flow._get(f"{flow.FAPI}/fapi/v1/klines", symbol=sym, interval="1h", limit=1000)
    if not d or len(d) < MIN_BARS:
        return None
    df = pd.DataFrame(d, columns=["open_time", "open", "high", "low", "close", "volume",
                                  "close_time", "quote_volume", "trades",
                                  "taker_buy_base", "taker_buy_quote", "ignore"])
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["ts", "open", "high", "low", "close"]].sort_values("ts").reset_index(drop=True)


def dip_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Her bar icin: 7g aralik konumu (%) ve dip kosulu."""
    hi = df["high"].rolling(RANGE_H, min_periods=RANGE_H).max()
    lo = df["low"].rolling(RANGE_H, min_periods=RANGE_H).min()
    rng = (hi - lo).replace(0, float("nan"))
    df["pos_pct"] = (df["close"] - lo) / rng * 100          # 0 = dip, 100 = tepe
    df["chg_24h"] = (df["close"] / df["close"].shift(24) - 1) * 100
    df["dip"] = (df["pos_pct"] <= LOW_PCT) & (df["chg_24h"] <= DROP_24H)
    return df


def measure(symbols: list[str]) -> dict:
    dip_rets = {h: [] for h in HORIZONS}
    base_rets = {h: [] for h in HORIZONS}
    per_symbol: list[dict] = []
    used = 0

    for sym in symbols:
        df = klines_1h(sym)
        if df is None:
            continue
        used += 1
        df = dip_flags(df)
        rows = 0
        for h in HORIZONS:
            # karar bari kapanisindan SONRAKI bar acilisindan gir (look-ahead yok)
            fwd = (df["open"].shift(-(h + 1)) / df["open"].shift(-1) - 1) * 100
            m = df["dip"] & fwd.notna()
            dip_rets[h].extend(fwd[m].tolist())
            base_rets[h].extend(fwd[df["dip"].notna() & fwd.notna()].tolist())
            if h == HORIZONS[0]:
                rows = int(m.sum())
        last = df.iloc[-1]
        per_symbol.append({
            "symbol": sym,
            "pos_pct": round(float(last["pos_pct"]), 1) if pd.notna(last["pos_pct"]) else None,
            "chg_24h": round(float(last["chg_24h"]), 2) if pd.notna(last["chg_24h"]) else None,
            "is_dip": bool(last["dip"]) if pd.notna(last["pos_pct"]) else False,
            "dip_events_41d": rows,
        })

    def stats(xs: list[float]) -> dict:
        if not xs:
            return {"n": 0}
        s = sorted(xs)
        q = lambda p: s[min(len(s) - 1, int(p * len(s)))]      # noqa: E731
        # Kuyruk sisirmesi: ortalamanin ne kadari en iyi %1'den geliyor?
        cut = max(1, int(len(s) * 0.01))
        tail_sum = sum(s[-cut:])
        total = sum(s)
        tail_share = round(tail_sum / total * 100, 1) if total else None
        trimmed = s[cut:-cut] if len(s) > 2 * cut + 10 else s
        return {"n": len(s), "mean": round(statistics.mean(s), 3),
                "mean_trimmed": round(statistics.mean(trimmed), 3),
                "median": round(statistics.median(s), 3),
                "p25": round(q(0.25), 3), "p75": round(q(0.75), 3),
                "up_pct": round(sum(1 for v in s if v > 0) / len(s) * 100, 1),
                "tail_share_pct": tail_share,
                "worst": round(s[0], 2), "best": round(s[-1], 2)}

    def random_percentile(dip_xs: list[float], base_xs: list[float], rounds: int = 600) -> float | None:
        """Ayni buyuklukte rastgele orneklerin ortalamasina gore yuzdelik."""
        if not dip_xs or not base_xs:
            return None
        import random
        rnd = random.Random(7)
        n = min(len(dip_xs), len(base_xs))
        target = statistics.mean(dip_xs)
        means = [statistics.mean(rnd.sample(base_xs, n)) for _ in range(rounds)]
        return round(sum(1 for m in means if m < target) / rounds * 100, 1)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "definition": {"range_h": RANGE_H, "low_pct": LOW_PCT, "drop_24h_pct": DROP_24H,
                       "note": "fiyat 7g araliginin alt %10'u ve 24s <= -%3"},
        "symbols_used": used,
        "symbols_requested": len(symbols),
        "horizons": {},
        "verdict": None,
        "rows": sorted(per_symbol, key=lambda r: (r["pos_pct"] is None, r["pos_pct"] or 0)),
    }
    for h in HORIZONS:
        d, b = stats(dip_rets[h]), stats(base_rets[h])
        edge = round(d["mean"] - b["mean"], 3) if d.get("n") and b.get("n") else None
        med_edge = round(d["median"] - b["median"], 3) if d.get("n") and b.get("n") else None
        out["horizons"][f"h{h}"] = {
            "dip": d, "baseline": b,
            "edge_vs_baseline": edge,
            "median_edge": med_edge,
            "random_percentile": random_percentile(dip_rets[h], base_rets[h]),
        }

    h24 = out["horizons"]["h24"]
    d24, b24 = h24["dip"], h24["baseline"]
    pct = h24.get("random_percentile")
    # KARAR: medyan birincil olcu. Ortalama tek basina kanit degil (kuyruk sisirmesi).
    if d24.get("n", 0) < 30:
        out["verdict"] = "orneklem_yetersiz"
        out["verdict_text"] = f"Dip olayi {d24.get('n', 0)} tane — yorum yok."
    elif d24["median"] < 0 and d24["median"] > b24["median"] + 0.2 and d24["up_pct"] > b24["up_pct"] + 3:
        # dip, tabana gore daha iyi ama MUTLAK olarak hala eksi -> alim talimati degil
        out["verdict"] = "goreli_avantaj"
        out["verdict_text"] = (
            f"+24s medyan: dip {d24['median']:+.2f}% vs taban {b24['median']:+.2f}% "
            f"(yukari oran {d24['up_pct']:.0f}% vs {b24['up_pct']:.0f}%). "
            "Dipten almak piyasa ortalamasindan daha iyi ama MUTLAK medyan hala negatif — "
            "yani tipik islem yine zarar yaziyor. 'Daha az kotu' demek 'kar' demek degil.")
    elif d24["median"] > 0 and d24["median"] > b24["median"] + 0.2 and d24["up_pct"] > b24["up_pct"] + 3:
        out["verdict"] = "olumlu"
        out["verdict_text"] = (
            f"+24s medyan: dip {d24['median']:+.2f}% vs taban {b24['median']:+.2f}% "
            f"(yukari oran {d24['up_pct']:.0f}% vs {b24['up_pct']:.0f}%). "
            "Goreli fark pozitif ama MUTLAK medyan hala negatif olabilir — bu bir avantaj, kar garantisi degil.")
    elif d24.get("mean", 0) > 0 and d24["median"] < 0:
        # Ortalama farki istatistiksel olarak ayrissa bile tamami kuyruktan geliyorsa
        # bu "dipten al" degil, nadir sicrama piyangosudur.
        out["verdict"] = "kuyruk_piyangosu"
        out["verdict_text"] = (
            f"+24s medyan {d24['median']:+.2f}% (taban {b24['median']:+.2f}%) — tipik dip ALIMI "
            f"KAYBETTİRİYOR. Yukarı oran %{d24['up_pct']:.0f}, ilk çeyrek %{d24['p25']:+.2f}, "
            f"en kötü %{d24['worst']:+.1f}. Ortalama {d24['mean']:+.2f}% pozitif çıkıyor çünkü "
            f"nadir büyük sıçramalar toplamı tüm getirileri aşıyor (medyan negatif, uclari "
            f"kirpinca ortalama {d24['mean_trimmed']:+.2f}%). Ozet: bu bir 'nadir sicrama "
            "piyangosu' profili — kucuk pozisyon + stop olmadan kullanilamaz. Alim talimati degil.")
    else:
        out["verdict"] = "avantaj_yok"
        parts = [f"+24s medyan: dip {d24['median']:+.2f}% vs taban {b24['median']:+.2f}% "
                 f"(yukari oran {d24['up_pct']:.0f}% vs {b24['up_pct']:.0f}%)"]
        if d24.get("mean_trimmed") is not None:
            parts.append(f"uç değerler kırpılınca ortalama {d24['mean_trimmed']:+.2f}%")
        if pct is not None:
            parts.append(f"rastgele giriş ortalamalarının %{pct:.0f}'inde (eşik %95)")
        out["verdict_text"] = "Dipten almak ÖLÇÜLEN avantaj sağlamıyor. " + "; ".join(parts) + \
                              ". Durum göstergesi say, alım talimatı değil."
    return out


def write_reports(res: dict) -> None:
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        save_snapshot(res.get("rows") or [])
    except Exception as e:
        print(f"  [warn] dip snapshot yazilamadi: {e}")
    lines = ["# Dip Calismasi — olculmus kisa vade beklentisi", "",
             f"- Tarih: {res['generated_at']}",
             f"- Sembol: {res['symbols_used']}/{res['symbols_requested']} · Pencere: ~41 gun (1h)",
             f"- Tanim: {res['definition']['note']}", "",
             "| Ufuk | Dip n | Dip medyan | Dip ort | Ort (kirpilmis) | Yukari % | Taban medyan | Taban yukari % | Rastgele yuzdelik |",
             "|---|---|---|---|---|---|---|---|---|"]
    for h, v in res["horizons"].items():
        d, b = v["dip"], v["baseline"]
        if not d.get("n"):
            continue
        lines.append(f"| +{h[1:]}s | {d['n']} | {d['median']:+.2f}% | {d['mean']:+.2f}% | "
                     f"{d['mean_trimmed']:+.2f}% | {d['up_pct']:.0f}% | {b['median']:+.2f}% | "
                     f"{b['up_pct']:.0f}% | {v['random_percentile']} |")
    lines += ["", "**Ortalama tek başına kanıt değildir:** birkaç aşırı sıçrama ortalamayı yukarı "
              "çekerken medyan negatif kalabilir. Karar medyan + rastgele yüzdelik ile verilir.",
              "", f"**Karar:** {res['verdict_text']}"]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def universe() -> list[str]:
    try:
        return flow.universe(min_vol_musd=100.0, top=20)
    except Exception:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def save_snapshot(rows: list[dict]) -> None:
    """Her calismada TUM dip indikatorlerini CSV'ye ekle (hafiza -> ogrenme)."""
    import csv
    from datetime import datetime, timezone
    path = ROOT / "data" / "dip_snapshots.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["scanned_at", "symbol", "pos_pct", "chg_24h", "is_dip", "dip_events_41d"]
    new = not path.exists()
    ts = datetime.now(timezone.utc).isoformat()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({"scanned_at": ts, "symbol": r.get("symbol"),
                        "pos_pct": r.get("pos_pct"), "chg_24h": r.get("chg_24h"),
                        "is_dip": int(bool(r.get("is_dip"))),
                        "dip_events_41d": r.get("dip_events_41d")})


def main() -> int:
    syms = universe()
    print(f"dip calismasi — {len(syms)} sembol")
    res = measure(syms)
    write_reports(res)
    for h, v in res["horizons"].items():
        d, b = v["dip"], v["baseline"]
        if d.get("n"):
            print(f"  +{h[1:]:>2}s  dip n={d['n']:<4} ort {d['mean']:+.2f}%  yukari {d['up_pct']:.0f}%"
                  f"   | taban ort {b['mean']:+.2f}%  {b['up_pct']:.0f}%  fark {v['edge_vs_baseline']:+.2f}")
    print(f"  KARAR: {res['verdict_text']}")
    print(f"  rapor: {OUT_MD} · istek: {flow.STATS['requests']} · retry: {flow.STATS['retries']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
