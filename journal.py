#!/usr/bin/env python3
"""İşlem günlüğü — kendi davranışını ölç (genel modellerden daha değerli olabilir).

Her kapanan kâğıt pozisyon için: kapanış nedeni, not, PnL.
İstatistik: kendi isabet oranın, ortalama kazanç/kayıp, R çarpanı dağılımı,
en sık kapanış nedeni, sembol bazlı performans.

Kaynak: data/paper_positions.json (paper.py)
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import paper

ROOT = Path(__file__).parent


def _closed() -> list[dict]:
    try:
        rep = paper.report()
    except Exception:
        return []
    return [p for p in (rep.get("positions") or []) if p.get("status") != "open"]


def stats() -> dict:
    rows = _closed()
    if not rows:
        return {"available": False, "trades": 0, "note": "kapanan pozisyon yok — günlük boş"}

    def pct_of(r: dict) -> float:
        v = r.get("closed_pnl_pct")
        if v is not None:
            return float(v)
        usd, notional = r.get("closed_pnl_usd"), r.get("notional_usd")
        if usd is not None and notional:
            return float(usd) / float(notional) * 100
        return 0.0

    pnls = [pct_of(r) for r in rows]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    reasons = Counter((r.get("close_reason") or r.get("status") or "?") for r in rows)
    by_symbol: dict[str, list[float]] = {}
    for r, x in zip(rows, pnls):
        by_symbol.setdefault(r.get("symbol", "?"), []).append(x)

    def avg(v):
        return round(sum(v) / len(v), 2) if v else 0.0

    best = max(((r.get("symbol"), pct_of(r)) for r in rows),
               key=lambda t: t[1], default=("-", 0))
    worst = min(((r.get("symbol"), pct_of(r)) for r in rows),
                key=lambda t: t[1], default=("-", 0))

    return {"available": True, "trades": len(rows),
            "hit_rate_pct": round(len(wins) / len(rows) * 100, 1),
            "avg_win_pct": avg(wins), "avg_loss_pct": avg(losses),
            "expectancy_pct": round(sum(pnls) / len(pnls), 2),
            "total_pnl_pct": round(sum(pnls), 2),
            "best": {"symbol": best[0], "pnl_pct": round(best[1], 2)},
            "worst": {"symbol": worst[0], "pnl_pct": round(worst[1], 2)},
            "close_reasons": dict(reasons),
            "by_symbol": {k: {"n": len(v), "avg_pct": avg(v)} for k, v in
                          sorted(by_symbol.items(), key=lambda kv: -len(kv[1]))[:10]},
            "note": "Kendi işlemlerinin istatistiği; örneklem küçükse yorum yapma."}


if __name__ == "__main__":
    import json
    print(json.dumps(stats(), ensure_ascii=False, indent=1))
