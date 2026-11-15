#!/usr/bin/env python3
"""Formasyon edge'i gercek mi, sans mi? Uc test.

  1. RASTGELE GIRIS KONTROLU
     Ayni islem sayisi, ayni tutma suresi, ayni SL/TP ile 1000 rastgele
     strateji uret. Formasyon sonucu bu dagilimin neresinde?
     Eger %90'lik dilimde degilse, formasyonun kattigi bir sey yok.

  2. CAPRAZ SEMBOL (out-of-sample)
     Parametreler BTC'de secildi. Ayni parametreler ETH/SOL/BNB'de
     calisiyor mu? Calismiyorsa asiri uyum (overfitting).

  3. ISLEM SAYISI
     Istatistik icin kac islem lazim? 30'un altinda sonuc gurultudur.
"""
from pathlib import Path

import numpy as np
import pandas as pd

import patterns as P
from backtest import Backtester, Config

DATA = Path(__file__).parent / "data"
CFG = Config(stop_loss_pct=0.05, take_profit_pct=0.10)
MAX_HOLD = 96
COOLDOWN = 12


def load(sym: str) -> pd.DataFrame:
    return pd.read_csv(DATA / f"{sym}_1h.csv", parse_dates=["open_time", "close_time"])


def state_from_entries(n: int, entry_bars, exit_set, max_hold=MAX_HOLD, cooldown=COOLDOWN) -> pd.Series:
    st = np.zeros(n, dtype=np.int8)
    es = set(entry_bars)
    pos, last, held = 0, -10**9, 0
    for i in range(n):
        if pos == 1:
            held += 1
            if i in exit_set or held >= max_hold:
                pos = 0
        if pos == 0 and i in es and (i - last) >= cooldown:
            pos, last, held = 1, i, 0
        st[i] = pos
    return pd.Series(st)


print("=" * 78)
print("TEST 1 — RASTGELE GIRIS KONTROLU (BTCUSDT)")
print("=" * 78)
df = load("BTCUSDT")
n = len(df)
found = P.detect_all(df, order=5)
bearish_exits = {s.bar for s in found["double_top"] + found["head_shoulders"]}

target = "ascending_triangle"
entries = [s.bar for s in found[target]]
real = Backtester(df, CFG).run(state_from_entries(n, entries, bearish_exits), target)
real_ret = real.metrics["total_return"]
n_trades_real = real.metrics["num_trades"]

print(f"\n{target}: {n_trades_real} islem, getiri {real_ret:+.2%}, "
      f"kazanma {real.metrics['win_rate']:.0%}")

# ayni islem sayisiyle rastgele girisler
rng = np.random.default_rng(42)
N_RAND = 1000
rand_rets = []
k = max(n_trades_real, 1)
for _ in range(N_RAND):
    bars = np.sort(rng.choice(np.arange(COOLDOWN + 1, n - MAX_HOLD - 1), size=k, replace=False))
    st = state_from_entries(n, list(bars), bearish_exits)
    r = Backtester(df, CFG).run(st, "rand")
    rand_rets.append(r.metrics["total_return"])

rand_rets = np.array(rand_rets)
pct = (rand_rets < real_ret).mean() * 100
print(f"\nRastgele {N_RAND} strateji ({k} islem, ayni SL/TP/tutma):")
print(f"  ortalama        : {rand_rets.mean():+.2%}")
print(f"  medyan          : {np.median(rand_rets):+.2%}")
print(f"  en iyi %5       : {np.percentile(rand_rets, 95):+.2%}")
print(f"  en kotu %5      : {np.percentile(rand_rets, 5):+.2%}")
print(f"  EN IYI rastgele : {rand_rets.max():+.2%}")
print(f"\n  Formasyon sonucu: {real_ret:+.2%}")
print(f"  Rastgelelerin %{pct:.0f}'inden daha iyi")
verdict = "GURULTU — rastgele girislerle ayirt edilemiyor" if pct < 90 else "anlamli sinyal olabilir"
print(f"  YARGI: {verdict}")

print("\n" + "=" * 78)
print("TEST 2 — CAPRAZ SEMBOL (parametreler BTC'de secildi, dokunulmadi)")
print("=" * 78)
print(f"\n{'Sembol':<10}{'Formasyon':<22}{'Getiri':>10}{'Al-Tut':>10}{'Fark':>9}{'Islem':>7}{'Kazanma':>9}")
print("-" * 77)
cross = {}
for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
    d = load(sym)
    f = P.detect_all(d, order=5) if sym != "BTCUSDT" else found
    ex = {s.bar for s in f["double_top"] + f["head_shoulders"]}
    for pat in ["ascending_triangle", "inverse_hs", "bull_flag"]:
        ents = [s.bar for s in f[pat]]
        if len(ents) < 2:
            print(f"{sym:<10}{pat:<22}{'cok az sinyal':>10}")
            continue
        r = Backtester(d, CFG).run(state_from_entries(len(d), ents, ex), pat)
        m = r.metrics
        cross[(sym, pat)] = m
        print(f"{sym:<10}{pat:<22}{m['total_return']:>+9.2%} {m['buy_hold_return']:>+9.2%} "
              f"{m['alpha_vs_hold']:>+8.2%} {m['num_trades']:>7}{m['win_rate']:>9.0%}")

print("\n--- ozet ---")
for pat in ["ascending_triangle", "inverse_hs", "bull_flag"]:
    vals = [m["alpha_vs_hold"] for (s, p), m in cross.items() if p == pat]
    if vals:
        wins = sum(1 for v in vals if v > 0)
        print(f"{pat:<22} {wins}/{len(vals)} sembolde al-tut'u gecti | "
              f"ort. fark {np.mean(vals):+.1%} | en kotu {min(vals):+.1%}")

print("\n" + "=" * 78)
print("TEST 3 — ISTATISTIK YETERLI MI?")
print("=" * 78)
print(f"\n{target}: {n_trades_real} islem")
if n_trades_real < 30:
    print("  UYARI: 30 islemin altinda kazanma orani guvenilir degil.")
    print("  %36 kazanma orani, 11 islemle: gercek oran %10-%70 arasinda olabilir (95% guven).")
    print("  Bu veriyle 'edge var' demek mumkun degil.")
else:
    print("  Islem sayisi sinirda kabul edilebilir.")

print("\n" + "=" * 78)
print("SONUC")
print("=" * 78)
if pct < 90 and sum(1 for v in [m['alpha_vs_hold'] for (s, p), m in cross.items() if p == target] if v > 0) <= 2:
    print("Formasyonlar bu haliyle KANITLANMIS edge uretmiyor.")
    print("Al-tut'u gecen sonuclar, ayni sayida rastgele girisle de elde edilebiliyor.")
else:
    print("Bazi sonuclar rastgeleden ayriliyor — ama orneklem kucuk, guven yok.")
