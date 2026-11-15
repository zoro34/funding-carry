#!/usr/bin/env python3
"""Basit, seffaf backtest motoru — spot, long-only.

TASARIM KARARLARI (bunlar onemli):
  * Sinyal bar i KAPANISINDA uretilir, emir bar i+1 ACILISINDA dolar.
    Bu, "gelecegi gorme" (look-ahead bias) hatasini engeller.
  * Her islemde komisyon (varsayilan %0.1 = Binance spot) + slippage uygulanir.
  * Sadece LONG (spot hesapta aciga satis yok). Short icin vadeli gerekir.
  * Stop-loss / take-profit bar ici (intrabar) high/low ile kontrol edilir.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Config:
    initial_capital: float = 1000.0
    fee: float = 0.001          # %0.1 — Binance spot taker
    slippage: float = 0.0005    # %0.05 — piyasa emrinde gerceklesen kayma
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    qty: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"  Baslangic sermaye : {m['initial_capital']:>12,.2f}",
            f"  Bitis degeri      : {m['final_equity']:>12,.2f}",
            f"  Net getiri        : {m['total_return']:>+11.2%}   (komisyon+slippage dahil)",
            f"  Al-tut (benchmark): {m['buy_hold_return']:>+11.2%}",
            f"  Fark              : {m['alpha_vs_hold']:>+11.2%}",
            "",
            f"  Islem sayisi      : {m['num_trades']:>12,}",
            f"  Kazanma orani     : {m['win_rate']:>11.1%}",
            f"  Kar faktoru       : {m['profit_factor']:>12.2f}   (>1.5 iyi, <1 kayip)",
            f"  Ortalama kazanc   : {m['avg_win']:>+11.2%}",
            f"  Ortalama kayip    : {m['avg_loss']:>+11.2%}",
            f"  Max drawdown      : {m['max_drawdown']:>11.1%}",
            f"  Yillik getiri     : {m['cagr']:>+11.2%}",
            f"  Sharpe (gunluk)   : {m['sharpe']:>12.2f}",
            f"  Piyasada kalma    : {m['exposure']:>11.1%}",
            f"  Toplam komisyon   : {m['total_fees']:>12,.2f}",
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(self, df: pd.DataFrame, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.df = df.reset_index(drop=True).copy()
        for c in ("open", "high", "low", "close"):
            if c not in self.df.columns:
                raise ValueError(f"kolon eksik: {c}")
        self.df["time"] = pd.to_datetime(self.df["open_time"], utc=True)

    def run(self, target: pd.Series, name: str = "strategy") -> Result:
        """target: 0/1 serisi. 1 = uzun pozisyonda ol, 0 = nakit.

        Sinyal i barinda uretilen karar i+1 acilisinda uygulanir.
        """
        target = target.fillna(0).astype(int).reset_index(drop=True)
        if len(target) != len(self.df):
            raise ValueError("sinyal uzunlugu veri ile ayni olmali")

        cfg = self.cfg
        o = self.df["open"].to_numpy()
        h = self.df["high"].to_numpy()
        lo = self.df["low"].to_numpy()
        cl = self.df["close"].to_numpy()
        ts = self.df["time"].to_numpy()
        tgt = np.concatenate([[0], target.to_numpy()[:-1]])  # i-1 sinyali -> i barinda islem

        cash = cfg.initial_capital
        qty = 0.0
        fees_paid = 0.0
        trades: list[Trade] = []
        open_trade: Trade | None = None
        equity = np.zeros(len(self.df))
        entry_price = 0.0
        bars_in = 0

        def sell(price: float, i: int, reason: str) -> None:
            nonlocal cash, qty, fees_paid, open_trade
            if qty <= 0:
                return
            fill = price * (1 - cfg.slippage)
            gross = qty * fill
            fee = gross * cfg.fee
            cash += gross - fee
            fees_paid += fee
            if open_trade:
                open_trade.exit_time = pd.Timestamp(ts[i])
                open_trade.exit_price = fill
                open_trade.pnl = (gross - fee) - (open_trade.entry_price * open_trade.qty)
                open_trade.pnl_pct = fill / open_trade.entry_price - 1
                open_trade.exit_reason = reason
                trades.append(open_trade)
                open_trade = None
            qty = 0.0

        def buy(price: float, i: int) -> None:
            nonlocal cash, qty, fees_paid, open_trade, entry_price
            if cash <= 0 or qty > 0:
                return
            fill = price * (1 + cfg.slippage)
            spendable = cash
            fee_est = spendable * cfg.fee
            q = (spendable - fee_est) / fill
            if q <= 0:
                return
            qty = q
            fees_paid += fee_est
            cash = 0.0
            entry_price = fill
            open_trade = Trade(entry_time=pd.Timestamp(ts[i]), entry_price=fill, qty=q)

        for i in range(len(self.df)):
            if qty > 0:
                bars_in += 1
                # 1) intrabar stop / hedef kontrolu (once kotu senaryo: stop)
                if cfg.stop_loss_pct:
                    stop_px = entry_price * (1 - cfg.stop_loss_pct)
                    if lo[i] <= stop_px:
                        sell(stop_px, i, "stop-loss")
                if qty > 0 and cfg.take_profit_pct:
                    tp_px = entry_price * (1 + cfg.take_profit_pct)
                    if h[i] >= tp_px:
                        sell(tp_px, i, "take-profit")

            # 2) sinyal uygula (onceki barin karari) — acilis fiyatindan
            if tgt[i] == 1 and qty == 0:
                buy(o[i], i)
            elif tgt[i] == 0 and qty > 0:
                sell(o[i], i, "signal")

            equity[i] = cash + qty * cl[i]

        if qty > 0:  # acik pozisyonu kapat (raporlama icin)
            sell(cl[-1], len(self.df) - 1, "end-of-data")
            equity[-1] = cash

        eq = pd.Series(equity, index=self.df["time"])
        return Result(equity=eq, trades=trades, metrics=self._metrics(eq, trades, fees_paid, bars_in, name))

    def _metrics(self, eq: pd.Series, trades: list[Trade], fees: float, bars_in: int, name: str) -> dict:
        cfg = self.cfg
        n = len(eq)
        final = float(eq.iloc[-1])
        total_ret = final / cfg.initial_capital - 1

        days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400, 1)
        years = days / 365.25
        cagr = (final / cfg.initial_capital) ** (1 / years) - 1 if final > 0 and years > 0 else -1.0

        roll_max = eq.cummax()
        dd = eq / roll_max - 1
        max_dd = float(dd.min())

        wins = [t.pnl_pct for t in trades if t.pnl > 0]
        losses = [t.pnl_pct for t in trades if t.pnl <= 0]
        gross_win = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))

        daily = eq.resample("1D").last().dropna().pct_change().dropna()
        sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if len(daily) > 2 and daily.std() > 0 else 0.0

        bh = float(self.df["close"].iloc[-1] / self.df["close"].iloc[0] - 1)

        return {
            "name": name,
            "initial_capital": cfg.initial_capital,
            "final_equity": final,
            "total_return": total_ret,
            "buy_hold_return": bh,
            "alpha_vs_hold": total_ret - bh,
            "cagr": cagr,
            "max_drawdown": max_dd,
            "num_trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
            "avg_win": float(np.mean(wins)) if wins else 0.0,
            "avg_loss": float(np.mean(losses)) if losses else 0.0,
            "sharpe": sharpe,
            "exposure": bars_in / n if n else 0.0,
            "total_fees": fees,
            "days": days,
        }
