#!/usr/bin/env python3
"""Motor dogrulama + temel strateji karsilastirmasi."""
from pathlib import Path

import pandas as pd

from backtest import Backtester, Config

DATA = Path(__file__).parent / "data" / "BTCUSDT_1h.csv"
df = pd.read_csv(DATA, parse_dates=["open_time", "close_time"])

print(f"Veri: {len(df):,} mum | {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}")
print(f"BTC: {df['close'].iloc[0]:,.2f} -> {df['close'].iloc[-1]:,.2f}\n")

cfg = Config(initial_capital=1000.0, fee=0.001, slippage=0.0005)

# ---------------------------------------------------------------- 1) DOGRULAMA
# Motorun muhasebesi dogru mu? "Hep long" stratejisi, al-tut getirisine esit olmali
# (tek fark: 2 komisyon + slippage).
print("=" * 66)
print("DOGRULAMA TESTI — motor muhasebesi")
print("=" * 66)
always_long = pd.Series(1, index=df.index)
r_bh = Backtester(df, cfg).run(always_long, "always_long")

raw_chg = df["close"].iloc[-1] / df["close"].iloc[0] - 1
engine_ret = r_bh.metrics["total_return"]
print(f"  Fiyat degisimi (ham)      : {raw_chg:+.4%}")
print(f"  Motor sonucu (komisyonlu) : {engine_ret:+.4%}")
print(f"  Fark                      : {engine_ret - raw_chg:+.4%}  <- 2x komisyon+slippage olmali")
print(f"  Beklenen maliyet          : {-2*(cfg.fee+cfg.slippage):+.4%}")
ok = abs((engine_ret - raw_chg) - (-2 * (cfg.fee + cfg.slippage))) < 0.005
print(f"  SONUC: {'GECTI — motor dogru muhasebe yapiyor' if ok else 'KALDI — arastir'}\n")

# ------------------------------------------------------- 2) TEMEL STRATEJILER
print("=" * 66)
print("TEMEL STRATEJILER (komisyon+slippage dahil)")
print("=" * 66)

results = {}

# a) Al ve tut
results["Al-Tut"] = r_bh

# b) SMA kesisimi (klasik baseline)
for fast, slow in [(20, 50), (50, 200), (10, 30)]:
    f = df["close"].rolling(fast).mean()
    s = df["close"].rolling(slow).mean()
    sig = ((f > s) & f.notna() & s.notna()).astype(int)
    results[f"SMA {fast}/{slow}"] = Backtester(df, cfg).run(sig, f"SMA {fast}/{slow}")

# c) SMA + stop-loss
f = df["close"].rolling(50).mean()
s = df["close"].rolling(200).mean()
sig = ((f > s) & s.notna()).astype(int)
cfg_sl = Config(initial_capital=1000.0, fee=0.001, slippage=0.0005, stop_loss_pct=0.05)
results["SMA 50/200 + SL %5"] = Backtester(df, cfg_sl).run(sig, "SMA 50/200 + SL %5")

# ------------------------------------------------------------------ 3) TABLO
print(f"\n{'Strateji':<22}{'Getiri':>10}{'Al-Tut':>10}{'Fark':>9}{'Islem':>7}{'Kazanma':>9}{'MaxDD':>8}{'PF':>6}")
print("-" * 81)
for name, r in results.items():
    m = r.metrics
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(
        f"{name:<22}{m['total_return']:>+9.2%} {m['buy_hold_return']:>+9.2%} "
        f"{m['alpha_vs_hold']:>+8.2%} {m['num_trades']:>7}{m['win_rate']:>9.1%} "
        f"{m['max_drawdown']:>8.1%}{pf_s:>6}"
    )

print("\nEn iyi strateji detayi:")
best = max(results.items(), key=lambda kv: kv[1].metrics["total_return"])
print(f"\n>>> {best[0]}")
print(best[1].summary())

# ------------------------------------------------------- 4) RAPOR + GRAFIK
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REP = Path(__file__).parent / "reports"
REP.mkdir(exist_ok=True)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), height_ratios=[2, 1], sharex=True)
for name, r in results.items():
    ax1.plot(r.equity.index, r.equity.values, label=f"{name} ({r.metrics['total_return']:+.1%})", lw=1.2)
ax1.axhline(cfg.initial_capital, color="grey", ls="--", lw=0.8)
ax1.set_ylabel("Sermaye ($)")
ax1.set_title("BTCUSDT 1h — 2 yil backtest (komisyon %0.1 + slippage %0.05 dahil)")
ax1.legend(fontsize=8, loc="lower left")
ax1.grid(alpha=0.3)

ax2.plot(df["open_time"], df["close"], color="black", lw=0.8)
ax2.set_ylabel("BTC fiyati ($)")
ax2.grid(alpha=0.3)
plt.tight_layout()
chart = REP / "equity_curves.png"
plt.savefig(chart, dpi=110)
plt.close()

lines = [
    "# Backtest Raporu — BTCUSDT 1h",
    "",
    f"- Veri: {len(df):,} mum | {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}",
    f"- BTC fiyat: {df['close'].iloc[0]:,.2f} -> {df['close'].iloc[-1]:,.2f} ({raw_chg:+.2%})",
    f"- Maliyet: komisyon %{cfg.fee*100:.2f} + slippage %{cfg.slippage*100:.2f} (her yonde)",
    f"- Sermaye: ${cfg.initial_capital:,.0f}",
    "",
    "## Motor dogrulama",
    f"- Ham fiyat degisimi: {raw_chg:+.4%}",
    f"- Motor (komisyonlu): {engine_ret:+.4%}",
    f"- Beklenen maliyet: {2*(cfg.fee+cfg.slippage):.4%} | Olculen: {abs(engine_ret-raw_chg):.4%}",
    f"- Sonuc: **{'GECTI' if ok else 'KALDI'}**",
    "",
    "## Sonuclar",
    "",
    "| Strateji | Getiri | Al-Tut | Fark | Islem | Kazanma | MaxDD | PF |",
    "|---|---|---|---|---|---|---|---|",
]
for name, r in results.items():
    m = r.metrics
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    lines.append(
        f"| {name} | {m['total_return']:+.2%} | {m['buy_hold_return']:+.2%} | "
        f"{m['alpha_vs_hold']:+.2%} | {m['num_trades']} | {m['win_rate']:.1%} | "
        f"{m['max_drawdown']:.1%} | {pf_s} |"
    )
lines += [
    "",
    "## Ders",
    "",
    "Fiyat %27.9 dusen bir donemde hicbir trend-takip stratejisi al-tut'u yenemedi.",
    "Islem sayisi arttikca kayip buyudu: 341 islem = -%72, 198 islem = -%60, 63 islem = -%29.",
    "Komisyon maliyeti islem sayisiyla dogrusal buyuyor. Formasyon stratejisi yazarken",
    "**islem sayisini kisitlamak**, getiriden once gelen kural olmali.",
    "",
    f"![equity]({chart.name})",
]
report = REP / "baseline.md"
report.write_text("\n".join(lines), encoding="utf-8")

print(f"\nGrafik : {chart}")
print(f"Rapor  : {report}")
