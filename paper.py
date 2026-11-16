#!/usr/bin/env python3
"""Kagit (paper) pozisyon takibi — emir gondermez, yalnizca olcer ve uyarir.

Kullanim amaci: "istedigim pozisyonu baslat" -> pozisyonu KAYDEDER, giris/stop/hedef
seviyelerini izler, stop veya hedefe degince Telegram'a haber verir. Gercek emir
GONDERILMEZ (API anahtari yok, ve kullanici onayi olmadan asla).

Olculen beklenti (reports/dip_study.json) varsa varsayilan stop/hedef oradan gelir:
  hedef = +24s p75, stop = |+24s p25|  (muhafazakar: olculen dagilimin ceyrekleri)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import flow
import notify

ROOT = Path(__file__).parent
STORE = ROOT / "data" / "paper_positions.json"
STUDY = ROOT / "reports" / "dip_study.json"
MAX_POS = 20


def now() -> datetime:
    return datetime.now(timezone.utc)


def load() -> list[dict]:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def defaults() -> dict:
    """Olculen dagilimdan muhafazakar stop/hedef (yoksa 5% / 3%)."""
    try:
        d = json.loads(STUDY.read_text(encoding="utf-8"))
        h = d["horizons"]["h24"]["dip"]
        stop = round(min(max(abs(h["p25"]), 2.0), 12.0), 2)
        target = round(min(max(h["p75"], 1.5), 10.0), 2)
        return {"stop_pct": stop, "target_pct": target,
                "source": f"ölçülen +24s çeyrekleri (p25 {h['p25']:+.2f}%, p75 {h['p75']:+.2f}%)"}
    except Exception:
        return {"stop_pct": 5.0, "target_pct": 3.0, "source": "varsayılan (ölçüm yok)"}


def price(symbol: str) -> float | None:
    d = flow._get(f"{flow.FAPI}/fapi/v1/klines", symbol=symbol, interval="1h", limit=1)
    if not d:
        return None
    try:
        return float(d[-1][4])
    except Exception:
        return None


def add(symbol: str, notional_usd: float, stop_pct: float | None = None,
        target_pct: float | None = None, note: str = "") -> dict:
    sym = (symbol or "").strip().upper()
    if not sym.endswith(("USDT", "USDC", "BUSD")):
        raise ValueError("sembol USDT/USDC ile bitmeli (ör. BTCUSDT)")
    try:
        notional = float(notional_usd)
    except Exception:
        raise ValueError("notional sayı olmalı (USD)")
    if not (10 <= notional <= 1_000_000):
        raise ValueError("notional 10 ile 1.000.000 USD arasında olmalı")
    d = defaults()
    stop = abs(float(stop_pct)) if stop_pct else d["stop_pct"]
    target = abs(float(target_pct)) if target_pct else d["target_pct"]
    if not (0.5 <= stop <= 50):
        raise ValueError("stop %0.5–50 arasında olmalı")
    if not (0.3 <= target <= 100):
        raise ValueError("hedef %0.3–100 arasında olmalı")

    px = price(sym)
    if not px:
        raise ValueError(f"{sym} fiyatı okunamadı (sembol vadeli piyasada var mı?)")

    rows = load()
    if len(rows) >= MAX_POS:
        raise ValueError(f"en fazla {MAX_POS} açık pozisyon")
    if any(r["symbol"] == sym and r["status"] == "open" for r in rows):
        raise ValueError(f"{sym} için zaten açık pozisyon var — önce kapat")

    pos = {
        "id": uuid.uuid4().hex[:8], "symbol": sym, "side": "LONG",
        "notional_usd": round(notional, 2), "entry_price": px,
        "stop_pct": round(stop, 2), "target_pct": round(target, 2),
        "stop_price": round(px * (1 - stop / 100), 8),
        "target_price": round(px * (1 + target / 100), 8),
        "opened_at": now().isoformat(), "status": "open", "note": note.strip()[:120],
        "defaults_source": d["source"],
    }
    rows.append(pos)
    save(rows)
    notify.send(
        f"<b>Pozisyon açıldı</b> {sym}\n"
        f"Giriş: {px:.6g}$ · Notional: {notional:,.0f}$\n"
        f"Stop: {pos['stop_price']:.6g}$ (-{stop:.2f}%) · Hedef: {pos['target_price']:.6g}$ (+{target:.2f}%)\n"
        f"<i>Kâğıt pozisyon — gerçek emir gönderilmedi.</i>",
        key=f"open:{sym}:{pos['id']}", cooldown_min=0)
    return pos


def evaluate(pos: dict, px: float) -> dict:
    pnl_pct = (px / pos["entry_price"] - 1) * 100
    risk = pos["stop_pct"]
    return {
        **pos,
        "price_now": px,
        "pnl_pct": round(pnl_pct, 3),
        "pnl_usd": round(pos["notional_usd"] * pnl_pct / 100, 2),
        "r_multiple": round(pnl_pct / risk, 2) if risk else None,
        "to_stop_pct": round((pos["stop_price"] / px - 1) * 100, 2),
        "to_target_pct": round((pos["target_price"] / px - 1) * 100, 2),
        "hit_stop": px <= pos["stop_price"],
        "hit_target": px >= pos["target_price"],
        "hours_open": round((now() - datetime.fromisoformat(pos["opened_at"]))
                            .total_seconds() / 3600, 1),
    }


def report(alert: bool = False) -> dict:
    rows = load()
    changed = False
    evaluated: list[dict] = []

    # 1) değerlendir; tetiklenenleri KAPAT (store'da güncelle)
    for r in rows:
        if r["status"] != "open":
            evaluated.append({**r, "price_now": None, "pnl_pct": None, "pnl_usd": None,
                              "r_multiple": None, "to_stop_pct": None,
                              "to_target_pct": None, "hit_stop": False,
                              "hit_target": False, "hours_open": None})
            continue
        px = price(r["symbol"])
        if not px:
            evaluated.append({**r, "price_now": None, "pnl_pct": None, "pnl_usd": None,
                              "r_multiple": None, "to_stop_pct": None,
                              "to_target_pct": None, "hit_stop": False,
                              "hit_target": False, "hours_open": None})
            continue
        e = evaluate(r, px)
        if alert and (e["hit_stop"] or e["hit_target"]):
            kind = "STOP" if e["hit_stop"] else "HEDEF"
            r.update({"status": "stopped" if e["hit_stop"] else "target",
                      "closed_at": now().isoformat(), "closed_price": px,
                      "closed_pnl_usd": e["pnl_usd"]})
            changed = True
            notify.send(
                f"<b>{kind}</b> {r['symbol']}\n"
                f"Fiyat {px:.6g}$ · PnL {e['pnl_pct']:+.2f}% ({e['pnl_usd']:+.2f}$)\n"
                f"<i>Kâğıt pozisyon kapatıldı.</i>",
                key=f"{kind.lower()}:{r['symbol']}:{r['id']}", cooldown_min=0)
            e = {**e, "status": r["status"], "closed_pnl_usd": r["closed_pnl_usd"]}
        evaluated.append(e)

    if changed:
        save(rows)

    # 2) toplamlar NİHAİ durumdan (kapananlar open sayılmaz)
    out = {"positions": evaluated,
           "open": sum(1 for p in evaluated if p["status"] == "open"),
           "closed": sum(1 for p in evaluated if p["status"] != "open"),
           "unrealized_usd": round(sum(p["pnl_usd"] or 0 for p in evaluated
                                       if p["status"] == "open"), 2),
           "realized_usd": round(sum(float(p.get("closed_pnl_usd") or 0) for p in evaluated
                                     if p["status"] != "open"), 2),
           "telegram": notify.status(), "defaults": defaults(),
           "generated_at": now().isoformat()}
    return out


REASONS = ("manuel", "stop", "hedef", "fikir_değişti", "haber", "diğer")


def close(pid: str, reason: str = "manuel", note: str = "") -> dict | None:
    rows = load()
    px = None
    for r in rows:
        if r["id"] == pid and r["status"] == "open":
            px = price(r["symbol"])
            if not px:
                raise ValueError("fiyat okunamadı")
            pnl = (px / r["entry_price"] - 1) * 100
            r.update({"status": "closed_manual", "closed_at": now().isoformat(),
                      "closed_price": px,
                      "closed_pnl_pct": round(pnl, 3),
                      "closed_pnl_usd": round(r["notional_usd"] * pnl / 100, 2),
                      "close_reason": (reason or "manuel")[:24],
                      "close_note": (note or "")[:200]})
            save(rows)
            notify.send(f"<b>Pozisyon elle kapatıldı</b> {r['symbol']}\n"
                        f"PnL {pnl:+.2f}% ({r['closed_pnl_usd']:+.2f}$)",
                        key=f"close:{pid}", cooldown_min=0)
            return r
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "alarm":
        r = report(alert=True)
    else:
        r = report()
    print(f"acik: {r['open']} · kapali: {r['closed']} · gerceklesen: {r['realized_usd']:+.2f}$ "
          f"· acik PnL: {r['unrealized_usd']:+.2f}$")
    for p in r["positions"]:
        if p["status"] != "open":
            continue
        print(f"  {p['symbol']:<10} giris {p['entry_price']:.6g} simdi {p['price_now']:.6g} "
              f"PnL {p['pnl_pct']:+.2f}% ({p['pnl_usd']:+.2f}$) R {p['r_multiple']:+.2f} "
              f"stop {p['to_stop_pct']:+.1f}% hedef {p['to_target_pct']:+.1f}%")
    print(f"telegram: {r['telegram']}")
