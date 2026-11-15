#!/usr/bin/env python3
"""Formasyon dedektörleri — pivot tabanlı, look-ahead bias YOK.

TEMEL KURAL
    Bir pivot (tepe/dip), kendisinden `order` bar SONRA kesinleşir.
    Yani bar i'deki tepe, ancak bar i+order'da "bilinebilir" hale gelir.
    Her formasyonun `confirm` indeksi = kullanılan tüm pivotların
    en geç kesinleşme zamanı. Sinyal ancak bundan sonra üretilebilir.

YÖN
    Motor spot + long-only. Bu yüzden:
      * BOGa formasyonlari  -> GIRIS sinyali
      * AYI formasyonlari   -> CIKIS sinyali
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Signal:
    bar: int              # sinyalin uretildigi bar (kapanis)
    direction: str        # "long_entry" | "exit"
    pattern: str
    price: float          # kirilma kapanisi
    detail: str = ""


# --------------------------------------------------------------------------
# PIVOT TESPITI
# --------------------------------------------------------------------------
def find_pivots(df: pd.DataFrame, order: int = 5) -> tuple[list[dict], list[dict]]:
    """Tepe ve dip pivotlari.

    Donen her pivot: {"idx": bar, "price": fiyat, "confirm": idx + order}
    confirm = bu pivotun kesinlestigi (bilinebildigi) bar.
    """
    w = 2 * order + 1
    hi = df["high"]
    lo = df["low"]

    roll_max = hi.rolling(w, center=True).max()
    roll_min = lo.rolling(w, center=True).min()

    highs, lows = [], []
    for i in range(order, len(df) - order):
        if hi.iloc[i] == roll_max.iloc[i]:
            highs.append({"idx": i, "price": float(hi.iloc[i]), "confirm": i + order})
        if lo.iloc[i] == roll_min.iloc[i]:
            lows.append({"idx": i, "price": float(lo.iloc[i]), "confirm": i + order})

    return highs, lows


def _pivots_between(pivots: list[dict], start: int, end: int) -> list[dict]:
    return [p for p in pivots if start < p["idx"] < end]


def _breakout(df: pd.DataFrame, start: int, level: float, up: bool,
              max_wait: int = 30) -> int | None:
    """start barindan sonra seviyeyi kapatan ilk bari bul."""
    close = df["close"].to_numpy()
    end = min(start + max_wait, len(df))
    for i in range(start, end):
        if up and close[i] > level:
            return i
        if not up and close[i] < level:
            return i
    return None


# --------------------------------------------------------------------------
# FORMASYONLAR — BOGa (giris)
# --------------------------------------------------------------------------
def double_bottom(df, pivots_h, pivots_l, order=5, tol=0.03, min_depth=0.03,
                  min_gap=10, max_gap=120, max_wait=30) -> list[Signal]:
    """Ikiz dip: iki benzer dip, arada tepe = neckline. Kirilma = alis."""
    out = []
    lows = pivots_l
    for a, b in zip(lows, lows[1:]):
        gap = b["idx"] - a["idx"]
        if not (min_gap <= gap <= max_gap):
            continue
        if abs(b["price"] - a["price"]) / a["price"] > tol:
            continue
        between = _pivots_between(pivots_h, a["idx"], b["idx"])
        if not between:
            continue
        peak = max(between, key=lambda p: p["price"])
        neckline = peak["price"]
        base = min(a["price"], b["price"])
        if (neckline - base) / neckline < min_depth:
            continue
        confirm = max(a["confirm"], b["confirm"], peak["confirm"])
        k = _breakout(df, confirm, neckline, up=True, max_wait=max_wait)
        if k is None:
            continue
        out.append(Signal(k, "long_entry", "double_bottom", float(df["close"].iloc[k]),
                          f"neck={neckline:.0f} dipler={a['price']:.0f}/{b['price']:.0f}"))
    return out


def inverse_hs(df, pivots_h, pivots_l, order=5, tol=0.05, min_depth=0.03,
               min_gap=8, max_gap=80, max_wait=30) -> list[Signal]:
    """Ters omuz-bas-omuz: L1 > L2 < L3, L2 en dusuk. Neckline = tepelerin alti."""
    out = []
    lows = pivots_l
    for i in range(len(lows) - 2):
        a, head, c = lows[i], lows[i + 1], lows[i + 2]
        if not (min_gap <= head["idx"] - a["idx"] <= max_gap):
            continue
        if not (min_gap <= c["idx"] - head["idx"] <= max_gap):
            continue
        if not (head["price"] < a["price"] and head["price"] < c["price"]):
            continue
        if abs(c["price"] - a["price"]) / a["price"] > tol:
            continue
        left_peak = _pivots_between(pivots_h, a["idx"], head["idx"])
        right_peak = _pivots_between(pivots_h, head["idx"], c["idx"])
        if not left_peak or not right_peak:
            continue
        n1 = max(left_peak, key=lambda p: p["price"])
        n2 = max(right_peak, key=lambda p: p["price"])
        neckline = min(n1["price"], n2["price"])
        if (neckline - head["price"]) / neckline < min_depth:
            continue
        confirm = max(a["confirm"], head["confirm"], c["confirm"], n1["confirm"], n2["confirm"])
        k = _breakout(df, confirm, neckline, up=True, max_wait=max_wait)
        if k is None:
            continue
        out.append(Signal(k, "long_entry", "inverse_hs", float(df["close"].iloc[k]),
                          f"neck={neckline:.0f} head={head['price']:.0f}"))
    return out


def ascending_triangle(df, pivots_h, pivots_l, tol=0.025, min_pts=2, max_wait=25) -> list[Signal]:
    """Yukselen ucgen: tepeler yatay (direnc), dipler yukseliyor. Direnc kirilimi = alis."""
    out = []
    highs = [p for p in pivots_h]
    lows = [p for p in pivots_l]
    for i in range(len(highs) - min_pts + 1):
        hs = highs[i:i + min_pts]
        if len({p["idx"] for p in hs}) < min_pts:
            continue
        resist = float(np.mean([p["price"] for p in hs]))
        if max(abs(p["price"] - resist) / resist for p in hs) > tol:
            continue
        span_lo = hs[0]["idx"]
        span_hi = hs[-1]["idx"]
        if span_hi - span_lo < 10:
            continue
        ls = _pivots_between(lows, span_lo, span_hi)
        if len(ls) < 2:
            continue
        if not all(ls[j + 1]["price"] > ls[j]["price"] for j in range(len(ls) - 1)):
            continue
        rise = (ls[-1]["price"] - ls[0]["price"]) / ls[0]["price"]
        if rise < 0.02:
            continue
        confirm = max(p["confirm"] for p in hs)
        k = _breakout(df, confirm, resist, up=True, max_wait=max_wait)
        if k is None:
            continue
        out.append(Signal(k, "long_entry", "ascending_triangle", float(df["close"].iloc[k]),
                          f"resist={resist:.0f} dip_artis={rise:+.1%}"))
    return out


def bull_flag(df, impulse_pct=0.08, impulse_bars=15, flag_bars=10,
              max_range=0.5, max_wait=15) -> list[Signal]:
    """Boga bayragi: sert yukselis + dar konsolidasyon, konsolidasyon tepesi kirilir."""
    out = []
    close = df["close"]
    high = df["high"]
    n = len(df)
    for i in range(impulse_bars + flag_bars, n - 1):
        imp_start = i - flag_bars - impulse_bars
        if imp_start < 0:
            continue
        imp = close.iloc[i - flag_bars] / close.iloc[imp_start] - 1
        if imp < impulse_pct:
            continue
        flag_high = high.iloc[i - flag_bars:i].max()
        flag_low = df["low"].iloc[i - flag_bars:i].min()
        if flag_low <= 0:
            continue
        rng = (flag_high - flag_low) / flag_low
        impulse_abs = close.iloc[i - flag_bars] - close.iloc[imp_start]
        if impulse_abs <= 0 or rng > max_range * (impulse_abs / close.iloc[imp_start]):
            continue
        k = _breakout(df, i, flag_high, up=True, max_wait=max_wait)
        if k is None:
            continue
        out.append(Signal(k, "long_entry", "bull_flag", float(close.iloc[k]),
                          f"impuls={imp:+.1%} konsolidasyon={rng:.1%}"))
    return out


# --------------------------------------------------------------------------
# FORMASYONLAR — AYI (cikis)
# --------------------------------------------------------------------------
def double_top(df, pivots_h, pivots_l, order=5, tol=0.03, min_depth=0.03,
               min_gap=10, max_gap=120, max_wait=30) -> list[Signal]:
    """Ikiz tepe: neckline (aradaki dip) kirilirsa cikis sinyali."""
    out = []
    for a, b in zip(pivots_h, pivots_h[1:]):
        gap = b["idx"] - a["idx"]
        if not (min_gap <= gap <= max_gap):
            continue
        if abs(b["price"] - a["price"]) / a["price"] > tol:
            continue
        between = _pivots_between(pivots_l, a["idx"], b["idx"])
        if not between:
            continue
        trough = min(between, key=lambda p: p["price"])
        neckline = trough["price"]
        base = max(a["price"], b["price"])
        if (base - neckline) / base < min_depth:
            continue
        confirm = max(a["confirm"], b["confirm"], trough["confirm"])
        k = _breakout(df, confirm, neckline, up=False, max_wait=max_wait)
        if k is None:
            continue
        out.append(Signal(k, "exit", "double_top", float(df["close"].iloc[k]),
                          f"neck={neckline:.0f}"))
    return out


def head_shoulders(df, pivots_h, pivots_l, order=5, tol=0.05, min_depth=0.03,
                   min_gap=8, max_gap=80, max_wait=30) -> list[Signal]:
    """Omuz-bas-omuz: tepeler H1 < H2 > H3. Neckline kirilirsa cikis."""
    out = []
    for i in range(len(pivots_h) - 2):
        a, head, c = pivots_h[i], pivots_h[i + 1], pivots_h[i + 2]
        if not (min_gap <= head["idx"] - a["idx"] <= max_gap):
            continue
        if not (min_gap <= c["idx"] - head["idx"] <= max_gap):
            continue
        if not (head["price"] > a["price"] and head["price"] > c["price"]):
            continue
        if abs(c["price"] - a["price"]) / a["price"] > tol:
            continue
        t1s = _pivots_between(pivots_l, a["idx"], head["idx"])
        t2s = _pivots_between(pivots_l, head["idx"], c["idx"])
        if not t1s or not t2s:
            continue
        n1 = min(t1s, key=lambda p: p["price"])
        n2 = min(t2s, key=lambda p: p["price"])
        neckline = max(n1["price"], n2["price"])
        if (head["price"] - neckline) / head["price"] < min_depth:
            continue
        confirm = max(a["confirm"], head["confirm"], c["confirm"], n1["confirm"], n2["confirm"])
        k = _breakout(df, confirm, neckline, up=False, max_wait=max_wait)
        if k is None:
            continue
        out.append(Signal(k, "exit", "head_shoulders", float(df["close"].iloc[k]),
                          f"neck={neckline:.0f} head={head['price']:.0f}"))
    return out


# --------------------------------------------------------------------------
def detect_all(df: pd.DataFrame, order: int = 5) -> dict[str, list[Signal]]:
    ph, pl = find_pivots(df, order=order)
    print(f"  pivot: {len(ph)} tepe, {len(pl)} dip (order={order})")
    found = {
        "double_bottom": double_bottom(df, ph, pl, order=order),
        "inverse_hs": inverse_hs(df, ph, pl, order=order),
        "ascending_triangle": ascending_triangle(df, ph, pl),
        "bull_flag": bull_flag(df),
        "double_top": double_top(df, ph, pl, order=order),
        "head_shoulders": head_shoulders(df, ph, pl, order=order),
    }
    for name, sigs in found.items():
        print(f"  {name:<20} {len(sigs):>4} sinyal")
    return found


def build_signal_series(df: pd.DataFrame, signals: list[Signal],
                        cooldown: int = 24, exit_on_bearish: list[Signal] | None = None) -> pd.Series:
    """Sinyalleri 0/1 serisine cevir.

    long_entry -> 1 (giris), exit / bearish formasyon -> 0 (cikis)
    cooldown: giriisten sonra en az kac bar yeni giris yok (komisyon kontrolu)
    """
    state = pd.Series(0, index=df.index)
    entries = sorted([s for s in signals if s.direction == "long_entry"], key=lambda s: s.bar)
    exits = sorted([s for s in signals if s.direction == "exit"], key=lambda s: s.bar)
    if exit_on_bearish:
        exits = sorted(exits + [s for s in exit_on_bearish if s.direction == "exit"], key=lambda s: s.bar)

    entry_bars = {s.bar: s for s in entries}
    exit_bars = {s.bar: s for s in exits}

    pos = 0
    last_entry = -10**9
    for i in range(len(df)):
        if pos == 1 and i in exit_bars:
            pos = 0
        if pos == 0 and i in entry_bars and (i - last_entry) >= cooldown:
            pos = 1
            last_entry = i
        state.iloc[i] = pos
    return state
