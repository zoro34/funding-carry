#!/usr/bin/env python3
"""Formasyon dedektorlerini calistir ve her birini ayri ayri backtest et."""
from pathlib import Path

import pandas as pd

import patterns as P
from backtest import Backtester, Config

DATA = Path(__file__).parent / "data" / "BTCUSDT_1h.csv"
df = pd.read_csv(DATA, parse_dates=["open_time", "close_time"])

print(f"Veri: {len(df):,} mum | {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}\n")
print("FORMASYON TESPITI")
print("-" * 55)
found = P.detect_all(df, order=5)

BEARISH = found["double_top"] + found["head_shoulders"]


def state_for(entries, max_hold: int, cooldown: int = 12) -> pd.Series:
    """Giris yap, max_hold bar sonra veya ayi formasyonunda cik."""
    state = pd.Series(0, index=df.index)
    entry_bars = sorted({s.bar for s in entries})
    exit_bars = sorted({s.bar for s in BEARISH})
    exit_set = set(exit_bars)
    pos, last = 0, -10**9
    held = 0
    for i in range(len(df)):
        if pos == 1:
            held += 1
            if i in exit_set or held >= max_hold:
                pos = 0
        if pos == 0 and i in set(entry_bars) and (i - last) >= cooldown:
            pos, last, held = 1, i, 0
        state.iloc[i] = pos
    return state


print("\n" + "=" * 78)
print("HER FORMASYON AYRI — komisyon %0.1 + slippage %0.05 dahil")
print("=" * 78)

rows = []
results = {}

# referans
r_bh = Backtester(df, Config()).run(pd.Series(1, index=df.index), "Al-Tut")
results["Al-Tut"] = r_bh
bh = r_bh.metrics["total_return"]

bullish_order = ["double_bottom", "inverse_hs", "ascending_triangle", "bull_flag"]

for name in bullish_order:
    sigs = found[name]
    if not sigs:
        print(f"{name:<22} sinyal yok — atlandi")
        continue
    for tag, max_hold, cfg in [
        ("hold48", 48, Config()),
        ("hold96+SL5/TP10", 96, Config(stop_loss_pct=0.05, take_profit_pct=0.10)),
    ]:
        st = state_for(sigs, max_hold)
        r = Backtester(df, cfg).run(st, f"{name} {tag}")
        m = r.metrics
        results[f"{name} {tag}"] = r
        rows.append((f"{name} [{tag}]", m))

print(f"\n{'Strateji':<40}{'Getiri':>10}{'Fark':>9}{'Islem':>7}{'Kazanma':>9}{'MaxDD':>8}")
print("-" * 83)
print(f"{'Al-Tut':<40}{bh:>+9.2%} {'-':>9} {'1':>7}{'-':>9}{-0.537:>8.1%}")
for label, m in rows:
    print(f"{label:<40}{m['total_return']:>+9.2%} {m['alpha_vs_hold']:>+8.2%} "
          f"{m['num_trades']:>7}{m['win_rate']:>9.1%}{m['max_drawdown']:>8.1%}")

print("\n" + "=" * 78)
print("EN IYI 3 (al-tut'a gore fark)")
print("=" * 78)
ranked = sorted([(l, m) for l, m in rows], key=lambda x: -x[1]["alpha_vs_hold"])[:3]
for label, m in ranked:
    print(f"\n>>> {label}")
    print(f"    getiri {m['total_return']:+.2%} | al-tut {m['buy_hold_return']:+.2%} | "
          f"islem {m['num_trades']} | kazanma {m['win_rate']:.0%} | maxDD {m['max_drawdown']:.1%} | "
          f"komisyon ${m['total_fees']:.2f}")

print("\n" + "=" * 78)
print("DURUM")
print("=" * 78)
beat = [l for l, m in rows if m["alpha_vs_hold"] > 0]
print(f"Al-tut'u gecen strateji sayisi: {len(beat)} / {len(rows)}")
if not beat:
    print("Hicbiri. Formasyonlar bu parametrelerle edge uretmedi.")
else:
    for b in beat:
        print(f"  gecen: {b}")
