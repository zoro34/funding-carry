#!/usr/bin/env python3
"""Gercek alarmlarin ileri getirisini olc.

alerts.csv'deki her alarm icin, alarm anindan SONRAKI fiyat hareketini hesaplar.
Amac: "AL/SAT etiketleri gercekten ise yariyor mu" sorusunu zamanla kendi
verimizle cevaplamak. Bugun 3 alarm varsa 3'unu olcer; 300 olunca 300'unu.

Yontem:
  - Alarm zamani = ts, referans fiyat = o saatteki 1h kapanis
  - +1h / +4h / +24h ileri getiri
  - Kontrol: ayni sembolun TUM saatlerindeki ortalama ileri getirisi
    (piyasa suruklemesini ayirmak icin)

Uyari: orneklem kucukken sonuc anlamsizdir. Rapor bunu acikca soyler.
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import flow

ROOT = Path(__file__).parent
ALERTS = ROOT / "data" / "alerts.csv"
OUT_MD = ROOT / "reports" / "alerts_eval.md"
OUT_JSON = ROOT / "reports" / "alerts_eval.json"
HORIZONS = (1, 4, 24)
MIN_SAMPLE = 20          # altinda "yorum yok"


def load_alerts() -> list[dict]:
    if not ALERTS.exists():
        return []
    with ALERTS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            r["ts_dt"] = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        out.append(r)
    return out


def klines_1h(sym: str) -> pd.DataFrame | None:
    d = flow._get(f"{flow.FAPI}/fapi/v1/klines", symbol=sym, interval="1h", limit=1000)
    if not d:
        return None
    df = pd.DataFrame(d, columns=["open_time", "open", "high", "low", "close", "volume",
                                  "close_time", "quote_volume", "trades",
                                  "taker_buy_base", "taker_buy_quote", "ignore"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    return df[["ts", "close"]].sort_values("ts").reset_index(drop=True)


def price_at(df: pd.DataFrame, when: datetime) -> float | None:
    """when anindaki veya ondan onceki en yakin barin kapanisi."""
    sub = df[df["ts"] <= when]
    return float(sub["close"].iloc[-1]) if len(sub) else None


def main() -> int:
    alerts = load_alerts()
    now = datetime.now(timezone.utc)
    print(f"alarm kaydi: {len(alerts)}")

    if not alerts:
        OUT_MD.write_text("# Alarm Değerlendirmesi\n\nHenüz alarm yok.\n", encoding="utf-8")
        print("Henüz alarm yok — değerlendirilecek veri yok.")
        return 0

    cache: dict[str, pd.DataFrame] = {}
    results = {h: {"AL": [], "SAT": []} for h in HORIZONS}
    controls = {h: [] for h in HORIZONS}
    matured = {h: 0 for h in HORIZONS}

    for a in alerts:
        sym, sig = a["symbol"], a["signal"]
        if sym not in cache:
            df = klines_1h(sym)
            if df is not None:
                cache[sym] = df
            flow_stats = None
        df = cache.get(sym)
        if df is None:
            continue

        entry = price_at(df, a["ts_dt"])
        if not entry:
            continue

        for h in HORIZONS:
            target = a["ts_dt"] + timedelta(hours=h)
            if target > now:                     # henuz olgunlasmadi
                continue
            exitp = price_at(df, target)
            if not exitp:
                continue
            ret = (exitp / entry - 1) * 100
            results[h][sig].append(ret)
            matured[h] += 1

        # kontrol: sembolun tum saatlik ileri getirileri (ayni pencere)
        for h in HORIZONS:
            idx = df.index[df["ts"] <= a["ts_dt"]]
            if not len(idx):
                continue
            i = int(idx[-1])
            if i + h < len(df):
                controls[h].append((df["close"].iloc[i + h] / df["close"].iloc[i] - 1) * 100)

    lines = ["# Alarm Değerlendirmesi (gerçek sinyaller)", "",
             f"- Tarih: {now.isoformat()}",
             f"- Toplam alarm: {len(alerts)}",
             "", "## Olgunlaşan sinyaller", "",
             "| Ufuk | Olgun | AL n | AL ort | SAT n | SAT ort |", "|---|---|---|---|---|---|"]
    summary = {"generated_at": now.isoformat(), "total_alerts": len(alerts), "horizons": {}}

    print(f"\n{'Ufuk':<6}{'olgun':>7}{'AL n':>6}{'AL ort':>9}{'SAT n':>7}{'SAT ort':>10}")
    print("-" * 47)
    for h in HORIZONS:
        al, sa = results[h]["AL"], results[h]["SAT"]
        am = statistics.mean(al) if al else None
        sm = statistics.mean(sa) if sa else None
        print(f"+{h:>2}h{matured[h]:>7}{len(al):>6}"
              f"{(f'{am:+.2f}%' if am is not None else '—'):>9}"
              f"{len(sa):>7}{(f'{sm:+.2f}%' if sm is not None else '—'):>10}")
        lines.append(f"| +{h}h | {matured[h]} | {len(al)} | "
                     f"{f'{am:+.2f}%' if am is not None else '—'} | {len(sa)} | "
                     f"{f'{sm:+.2f}%' if sm is not None else '—'} |")
        summary["horizons"][f"h{h}"] = {
            "matured": matured[h],
            "AL": {"n": len(al), "mean": round(am, 3) if am is not None else None},
            "SAT": {"n": len(sa), "mean": round(sm, 3) if sm is not None else None},
            "control_mean": round(statistics.mean(controls[h]), 3) if controls[h] else None,
        }

    lines += ["", "## Kontrol (aynı sembollerin ortalama ileri getirisi)", ""]
    print("\nKontrol (piyasa sürüklemesi):")
    for h in HORIZONS:
        if controls[h]:
            m = statistics.mean(controls[h])
            print(f"  +{h:>2}h  {m:+.3f}%  (n={len(controls[h])})")
            lines.append(f"- +{h}h: {m:+.3f}% (n={len(controls[h])})")

    enough = any(matured[h] >= MIN_SAMPLE for h in HORIZONS)
    if not enough:
        msg = (f"\n**Yorum yok:** en az {MIN_SAMPLE} olgun sinyal gerekiyor "
               f"(şu an en fazla {max(matured.values())}). "
               f"30 dakikada bir çalışan tarama ile bu sayı birkaç günde dolar.")
        print(msg)
        lines += ["", msg]

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"\nrapor: {OUT_MD}")
    print(f"istek: {flow.STATS['requests']} · retry: {flow.STATS['retries']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
