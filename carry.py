#!/usr/bin/env python3
"""Funding carry pozisyon takibi — gerceklesen kira geliri.

Ne yapar:
  Kullanicinin actigi GERCEK pozisyonlari (spot long + vadeli short) tutar ve
  her biri icin Binance'in GERCEK funding gecmisinden birikmis odemeyi toplar.

Neden boyle:
  Panel simdiye kadar "firsat" gosteriyordu (yillik funding tahmini). Bu modul
  "ben bunu actim, gercekte ne kazandim" sorusunu cevaplar. Tahmin degil, gercek
  funding olaylarinin toplami.

Varsayimlar (rapora da yazilir):
  - Notional sabit tutulur (bilesik faiz yok) -> muhafazakar.
  - Giriş fiyati: acilis saatindeki ilk 1h barin ACILISI (gerceklesen emir degil).
  - Funding odemesi = notional x fundingRate, her funding aninda.
  - Spot bacak yoksa (sembol spot'ta islem gormuyorsa) pozisyon delta-notr
    DEGILDIR; yon riski vardir ve bu uyari olarak doner.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import flow

ROOT = Path(__file__).parent
STORE = ROOT / "data" / "carry_positions.json"

MAX_POSITIONS = 12

# Panelde gosterilen olculmus referans (reports/funding_carry.md, 730 gun, komisyon dahil).
# Tahmin degil; ayni stratejinin gecmiste ne yaptigi.
MEASURED = {
    "window": "2 yıl (730 gün, 2.190 funding periyodu)",
    "rows": [
        {"symbol": "BTCUSDT", "ann_pct": 4.98, "pos_share": 83.1},
        {"symbol": "ETHUSDT", "ann_pct": 4.46, "pos_share": 79.3},
        {"symbol": "SOLUSDT", "ann_pct": 0.90, "pos_share": 60.0},
    ],
    "cost_pct": 0.30,
    "note": "Tek giriş/çıkış maliyeti 0.30% (iki bacak). Sürekli tutulan pozisyonda "
            "2 yılda bir kez ödenir.",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------- depolama
def load_positions() -> list[dict]:
    if not STORE.exists():
        return []
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_positions(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def add_position(symbol: str, notional_usd: float, opened_at: str | None = None,
                 note: str = "") -> dict:
    """Pozisyon ekle. Gecersiz girdi -> ValueError (sunucu 400 doner)."""
    sym = (symbol or "").strip().upper()
    if not sym or not sym.endswith(("USDT", "USDC", "BUSD")):
        raise ValueError("sembol USDT/USDC ile bitmeli (or. BTCUSDT)")
    try:
        notional = float(notional_usd)
    except Exception:
        raise ValueError("notional sayi olmali (USD)")
    if not (10 <= notional <= 10_000_000):
        raise ValueError("notional 10 ile 10.000.000 USD arasinda olmali")

    if opened_at:
        try:
            ts = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            raise ValueError("tarih ISO formatinda olmali (2026-11-10 veya 2026-11-10T14:00:00Z)")
    else:
        ts = now()
    if ts > now():
        raise ValueError("acilis tarihi gelecekte olamaz")

    rows = load_positions()
    if len(rows) >= MAX_POSITIONS:
        raise ValueError(f"en fazla {MAX_POSITIONS} pozisyon tutulur")
    if any(r["symbol"] == sym for r in rows):
        raise ValueError(f"{sym} zaten kayitli — once sil")

    pos = {
        "id": uuid.uuid4().hex[:8],
        "symbol": sym,
        "notional_usd": round(notional, 2),
        "opened_at": ts.astimezone(timezone.utc).isoformat(),
        "note": (note or "").strip()[:120],
        "created_at": now().isoformat(),
    }
    rows.append(pos)
    save_positions(rows)
    return pos


def remove_position(pid: str) -> bool:
    rows = load_positions()
    left = [r for r in rows if r["id"] != pid]
    if len(left) == len(rows):
        return False
    save_positions(left)
    return True


# ------------------------------------------------------------------ veri
def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _spot_klines(symbol: str, start_ms: int) -> pd.DataFrame | None:
    """Spot 1h mumlar. Sembol spot'ta yoksa None (delta-notr uyarisi icin)."""
    d = flow._get(f"{flow.SAPI}/api/v3/klines", symbol=symbol, interval="1h",
                  startTime=start_ms, limit=1000)
    return _to_frame(d)


def _perp_klines(symbol: str, start_ms: int) -> pd.DataFrame | None:
    d = flow._get(f"{flow.FAPI}/fapi/v1/klines", symbol=symbol, interval="1h",
                  startTime=start_ms, limit=1000)
    return _to_frame(d)


def _to_frame(d) -> pd.DataFrame | None:
    if not d:
        return None
    df = pd.DataFrame(d, columns=["open_time", "open", "high", "low", "close", "volume",
                                  "close_time", "quote_volume", "trades",
                                  "taker_buy_base", "taker_buy_quote", "ignore"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "close"):
        df[c] = df[c].astype(float)
    df = df[["ts", "open", "close"]].sort_values("ts").reset_index(drop=True)
    return df if len(df) else None


def funding_history(symbol: str, start_ms: int) -> list[dict]:
    d = flow._get(f"{flow.FAPI}/fapi/v1/fundingRate", symbol=symbol,
                  startTime=start_ms, limit=1000)
    return d if isinstance(d, list) else []


def premium_map() -> dict[str, dict]:
    """Tum semboller icin anlik funding + sonraki odeme zamani (1 istek)."""
    d = flow._get(f"{flow.FAPI}/fapi/v1/premiumIndex") or []
    return {p["symbol"]: p for p in d if isinstance(p, dict) and "symbol" in p}


def _price_at(df: pd.DataFrame | None, when: datetime) -> float | None:
    """when anindaki veya sonrasindaki ilk barin acilisi (giris fiyati)."""
    if df is None:
        return None
    sub = df[df["ts"] >= when]
    if len(sub):
        return float(sub["open"].iloc[0])
    return float(df["open"].iloc[-1])  # pozisyon cok yeni: ilk bar


# --------------------------------------------------------------- rapor
def position_report(pos: dict, prem: dict[str, dict]) -> dict:
    sym = pos["symbol"]
    notional = float(pos["notional_usd"])
    opened = datetime.fromisoformat(pos["opened_at"])
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    t = now()
    days = max((t - opened).total_seconds() / 86400, 0.0)
    start_ms = _ms(opened) - 3600_000

    spot = _spot_klines(sym, start_ms)
    perp = _perp_klines(sym, start_ms)
    hist = funding_history(sym, _ms(opened))

    spot_entry = _price_at(spot, opened)
    perp_entry = _price_at(perp, opened)
    spot_now = float(spot["close"].iloc[-1]) if spot is not None else None
    perp_now = float(perp["close"].iloc[-1]) if perp is not None else None

    warnings: list[str] = []
    if spot_entry is None or spot_now is None:
        warnings.append("spot bacak yok: bu sembol spot'ta islem gormuyor olabilir — "
                        "pozisyon DELTA-NOTR DEGIL, yon riski var")
    if perp_entry is None:
        warnings.append("vadeli fiyat okunamadi — bacak hesabi eksik")

    # ---- kira geliri (gercek funding olaylari)
    rates = []
    for h in hist:
        try:
            ft = int(h["fundingTime"])
            if ft < _ms(opened):
                continue
            rates.append(float(h["fundingRate"]))
        except Exception:
            continue
    funding_pct = sum(rates) * 100              # notional yuzdesi
    funding_usd = notional * sum(rates)
    neg = sum(1 for r in rates if r < 0)

    # ---- bacak P&L (notional sabit; birim = giris fiyatindan hesaplanan miktar)
    unit_ref = spot_entry or perp_entry
    units = (notional / unit_ref) if unit_ref else 0.0
    spot_pnl = units * (spot_now - spot_entry) if (spot_entry and spot_now) else 0.0
    perp_pnl = units * (perp_entry - perp_now) if (perp_entry and perp_now) else 0.0  # short

    net_usd = funding_usd + spot_pnl + perp_pnl
    net_pct = (net_usd / notional * 100) if notional else 0.0
    ann = lambda pct: pct * 365 / days if days > 0.5 else None  # noqa: E731

    basis_now = ((perp_now / spot_now - 1) * 100) if (spot_now and perp_now) else None
    basis_entry = ((perp_entry / spot_entry - 1) * 100) if (spot_entry and perp_entry) else None
    if basis_now is not None and basis_now > 0.3:
        warnings.append(f"basis %{basis_now:.2f} primli — kapatirken bu primi geri verirsin")
    if days < 1:
        warnings.append("pozisyon 1 günden yeni — yıllıklandırma henüz anlamsız")

    p = prem.get(sym) or {}
    try:
        fund_now = float(p.get("lastFundingRate", 0)) * 100
        ann_now = fund_now * (365 * 24 / int(p.get("fundingIntervalHours") or 8))
    except Exception:
        fund_now = ann_now = None
    next_funding = None
    if p.get("nextFundingTime"):
        nt = datetime.fromtimestamp(int(p["nextFundingTime"]) / 1000, tz=timezone.utc)
        next_funding = nt.isoformat()
        hours_to_next = max((nt - t).total_seconds() / 3600, 0.0)
    else:
        hours_to_next = None

    return {
        "id": pos["id"],
        "symbol": sym,
        "notional_usd": round(notional, 2),
        "opened_at": opened.isoformat(),
        "note": pos.get("note", ""),
        "days": round(days, 2),
        "funding_periods": len(rates),
        "funding_neg_periods": neg,
        "funding_usd": round(funding_usd, 2),
        "funding_pct": round(funding_pct, 4),
        "funding_ann_pct": round(ann(funding_pct), 2) if ann(funding_pct) is not None else None,
        "spot_entry": round(spot_entry, 6) if spot_entry else None,
        "spot_now": round(spot_now, 6) if spot_now else None,
        "perp_entry": round(perp_entry, 6) if perp_entry else None,
        "perp_now": round(perp_now, 6) if perp_now else None,
        "spot_pnl": round(spot_pnl, 2),
        "perp_pnl": round(perp_pnl, 2),
        "net_usd": round(net_usd, 2),
        "net_pct": round(net_pct, 4),
        "net_ann_pct": round(ann(net_pct), 2) if ann(net_pct) is not None else None,
        "basis_entry_pct": round(basis_entry, 3) if basis_entry is not None else None,
        "basis_now_pct": round(basis_now, 3) if basis_now is not None else None,
        "funding_now_pct": round(fund_now, 5) if fund_now is not None else None,
        "ann_now_pct": round(ann_now, 2) if ann_now is not None else None,
        "next_funding_at": next_funding,
        "hours_to_next": round(hours_to_next, 2) if hours_to_next is not None else None,
        "delta_neutral": bool(spot_entry is not None),
        "warnings": warnings,
    }


def report() -> dict:
    """Tum pozisyonlar + toplamlar. Panel ve statik disa aktarim bunu kullanir."""
    rows = load_positions()
    out = {
        "available": True,
        "generated_at": now().isoformat(),
        "positions": [],
        "totals": {},
        "measured": MEASURED,
        "assumptions": [
            "Notional sabit (bileşik yok) — muhafazakâr.",
            "Giriş fiyatı: açılış saatindeki ilk 1h barın açılışı.",
            "Kira geliri Binance'in GERÇEK funding geçmişinden toplanır, tahmin değil.",
        ],
        "error": None,
    }
    if not rows:
        out["totals"] = {"count": 0, "notional_usd": 0, "funding_usd": 0,
                         "net_usd": 0, "funding_pct": 0, "net_pct": 0,
                         "weighted_ann_pct": None, "days_avg": 0}
        return out

    try:
        prem = premium_map()
    except Exception:
        prem = {}

    try:
        for pos in rows:
            out["positions"].append(position_report(pos, prem))
    except Exception as e:                      # tek pozisyon patlarsa hepsi dusmesin
        out["error"] = str(e)

    ps = out["positions"]
    notional = sum(p["notional_usd"] for p in ps)
    funding = sum(p["funding_usd"] for p in ps)
    net = sum(p["net_usd"] for p in ps)
    funding_pct = (funding / notional * 100) if notional else 0.0
    net_pct = (net / notional * 100) if notional else 0.0
    days_avg = (sum(p["days"] * p["notional_usd"] for p in ps) / notional) if notional else 0.0
    # agirlikli yillik: kira geliri bazinda (yon P&L'i disarida)
    w_ann = None
    if notional and days_avg > 0.5:
        w_ann = round(funding_pct * 365 / days_avg, 2)

    out["totals"] = {
        "count": len(ps),
        "notional_usd": round(notional, 2),
        "funding_usd": round(funding, 2),
        "spot_pnl": round(sum(p["spot_pnl"] for p in ps), 2),
        "perp_pnl": round(sum(p["perp_pnl"] for p in ps), 2),
        "net_usd": round(net, 2),
        "funding_pct": round(funding_pct, 4),
        "net_pct": round(net_pct, 4),
        "weighted_ann_pct": w_ann,
        "days_avg": round(days_avg, 2),
        "delta_neutral_count": sum(1 for p in ps if p["delta_neutral"]),
    }
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        # python carry.py add BTCUSDT 1000 2026-11-10
        print(add_position(sys.argv[2], float(sys.argv[3]),
                           sys.argv[4] if len(sys.argv) > 4 else None))
    else:
        r = report()
        t = r["totals"]
        print(f"pozisyon: {t['count']} | notional: {t['notional_usd']:.0f}$ | "
              f"kira: {t['funding_usd']:+.2f}$ ({t['funding_pct']:+.3f}%) | "
              f"net: {t['net_usd']:+.2f}$")
        for p in r["positions"]:
            print(f"  {p['symbol']:<10} {p['notional_usd']:>9.0f}$  {p['days']:>6.2f}g  "
                  f"kira {p['funding_usd']:>+8.2f}$ ({p['funding_periods']} periyot)  "
                  f"net {p['net_usd']:>+8.2f}$")
            for w in p["warnings"]:
                print(f"     ! {w}")
