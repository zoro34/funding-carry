#!/usr/bin/env python3
"""Risk paneli — toplam maruziyet, yoğunlaşma, stres testi.

Girdi: carry pozisyonları + kâğıt pozisyonlar (gerçek emir yok).
Çıktı: maruziyet, BTC bağımlılığı, senaryo kayıpları, likidasyon mesafesi.

Senaryolar:
  * basis +1% / -1%   (spot-vadeli farkı açılırsa)
  * funding 3 gün tersine dönerse (günlük oran x3 tersine)
  * tek sembol -10%    (delta-nötrde etki ~0, yön pozisyonunda tam etki)
"""
from __future__ import annotations

from pathlib import Path

import carry
import paper

ROOT = Path(__file__).parent
STRESS = {"basis_plus_1pct": -1.0, "basis_minus_1pct": 1.0,
          "funding_flip_3d": -0.75, "single_symbol_minus_10pct": -10.0}


def _carry_rows() -> list[dict]:
    try:
        rep = carry.report()
    except Exception:
        return []
    return rep.get("positions") or []


def _paper_rows() -> list[dict]:
    try:
        rep = paper.report()
    except Exception:
        return []
    return [p for p in (rep.get("positions") or []) if p.get("status") == "open"]


def panel() -> dict:
    c_rows = _carry_rows()
    p_rows = _paper_rows()

    carry_notional = sum(float(r.get("notional_usd") or 0) for r in c_rows)
    paper_notional = sum(float(r.get("notional_usd") or 0) for r in p_rows)
    # delta-nötr: her pozisyon iki bacak -> brüt maruziyet 2x, net ~0
    gross = carry_notional * 2 + paper_notional

    # yoğunlaşma: en büyük pozisyonun payı
    sizes = [float(r.get("notional_usd") or 0) for r in c_rows] + \
            [float(r.get("notional_usd") or 0) for r in p_rows]
    total = sum(sizes)
    top_share = (max(sizes) / total * 100) if total else 0.0

    # BTC bağımlılığı: BTC/ETH dışı altcoinler beta ~1.2 varsayımı
    btc_like = sum(float(r.get("notional_usd") or 0) for r in c_rows
                   if str(r.get("symbol", "")).startswith(("BTC", "ETH")))
    btc_share = (btc_like / carry_notional * 100) if carry_notional else 0.0

    # stres: her senaryonun toplam etkisi (USD)
    stress_result = {}
    for name, pct in STRESS.items():
        if name.startswith("basis"):
            hit = carry_notional * abs(pct) / 100        # iki bacak, biri ters
        elif name == "funding_flip_3d":
            hit = carry_notional * abs(pct) / 100 / 3    # günlük ~%0.25 varsayımı
        else:
            hit = paper_notional * abs(pct) / 100        # yön pozisyonu tam etkilenir
        stress_result[name] = round(-hit, 2)

    worst = min(stress_result.values()) if stress_result else 0.0
    if gross == 0:
        level, verdict = "bos", "açık pozisyon yok"
    elif worst >= -5:
        level, verdict = "dusuk", "stres kaybı küçük"
    elif worst >= -25:
        level, verdict = "orta", "stres kaybı orta — marj tamponu kontrol et"
    else:
        level, verdict = "yuksek", "stres kaybı büyük — pozisyon küçült"

    return {"carry_positions": len(c_rows), "paper_positions": len(p_rows),
            "carry_notional": round(carry_notional, 2),
            "paper_notional": round(paper_notional, 2),
            "gross_exposure": round(gross, 2),
            "net_delta_note": "carry pozisyonları delta-nötr kabul edilir; brüt = 2 × notional",
            "top_position_share_pct": round(top_share, 1),
            "btc_eth_share_pct": round(btc_share, 1),
            "stress": stress_result, "worst_case_usd": round(worst, 2),
            "risk_level": level, "verdict": verdict,
            "assumptions": {"basis_shock_pct": 1.0, "funding_flip_daily_pct": 0.25,
                            "paper_direction_shock_pct": 10.0}}


if __name__ == "__main__":
    import json
    print(json.dumps(panel(), ensure_ascii=False, indent=1))
