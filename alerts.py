#!/usr/bin/env python3
"""Özel alarmlar — istediğin coin için kendi koşulunu kur, aç/kapa düğmesiyle yönet.

Koşullar:
  price_above  : fiyat X'in üstüne çıkarsa
  price_below  : fiyat X'in altına inerse
  pct_up       : 24 saatlik değişim +X% üstüne çıkarsa
  pct_down     : 24 saatlik değişim -X% altına inerse
  funding_above: anlık funding (8s, %) X'in üstüne çıkarsa

Davranış:
  * her alarm aç/kapa edilebilir (enabled)
  * tetiklenince Telegram'a gider; varsayılan tek seferlik (one_shot)
  * tek seferlik alarm tetiklenince kapanır; "tekrar" seçilirse cooldown ile devam eder
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
STORE = ROOT / "data" / "custom_alarms.json"
KINDS = {"price_above": "fiyat üstüne çıkarsa", "price_below": "fiyat altına inerse",
         "pct_up": "24s değişim +%", "pct_down": "24s değişim -%",
         "funding_above": "funding (8s %) üstü"}
MAX = 30
COOLDOWN_MIN = 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load() -> list[dict]:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(sym: str) -> str:
    s = (sym or "").strip().upper().replace("/", "").replace("-", "")
    if s and not s.endswith(("USDT", "USDC")):
        s += "USDT"
    return s


def add(symbol: str, kind: str, value: float, note: str = "", one_shot: bool = True) -> dict:
    sym = _norm(symbol)
    if not sym:
        raise ValueError("sembol gerekli")
    if kind not in KINDS:
        raise ValueError(f"geçersiz koşul: {kind} (geçerli: {', '.join(KINDS)})")
    try:
        val = float(value)
    except Exception:
        raise ValueError("değer sayı olmalı")
    rows = load()
    if len(rows) >= MAX:
        raise ValueError(f"en fazla {MAX} alarm")
    a = {"id": uuid.uuid4().hex[:6], "symbol": sym, "kind": kind, "value": val,
         "note": (note or "")[:120], "enabled": True, "one_shot": bool(one_shot),
         "created_at": now(), "triggered_at": None, "trigger_count": 0}
    rows.append(a)
    save(rows)
    return a


def toggle(aid: str, enabled: bool | None = None) -> dict:
    rows = load()
    for r in rows:
        if r["id"] == aid:
            r["enabled"] = (not r["enabled"]) if enabled is None else bool(enabled)
            save(rows)
            return r
    raise ValueError("alarm bulunamadı")


def remove(aid: str) -> bool:
    rows = load()
    left = [r for r in rows if r["id"] != aid]
    if len(left) == len(rows):
        return False
    save(left)
    return True


def clear() -> None:
    save([])


# ------------------------------------------------------------------ fiyat
def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "funding-carry/1.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())


def market(symbol: str) -> dict[str, float | None]:
    """Fiyat + 24s değişim + anlık funding (%)."""
    out: dict[str, float | None] = {"price": None, "pct_24h": None, "funding_pct": None}
    try:
        t = _get(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
        out["price"] = float(t.get("lastPrice"))
        out["pct_24h"] = float(t.get("priceChangePercent"))
    except Exception:
        pass
    try:
        p = _get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
        out["funding_pct"] = float(p.get("lastFundingRate")) * 100
    except Exception:
        pass
    return out


def _hit(kind: str, value: float, m: dict) -> tuple[bool, str]:
    price, pct, fund = m.get("price"), m.get("pct_24h"), m.get("funding_pct")
    if kind == "price_above" and price is not None:
        return price >= value, f"fiyat {price:g} ≥ {value:g}"
    if kind == "price_below" and price is not None:
        return price <= value, f"fiyat {price:g} ≤ {value:g}"
    if kind == "pct_up" and pct is not None:
        return pct >= value, f"24s {pct:+.2f}% ≥ {value:g}%"
    if kind == "pct_down" and pct is not None:
        return pct <= value, f"24s {pct:+.2f}% ≤ {value:g}%"
    if kind == "funding_above" and fund is not None:
        return fund >= value, f"funding {fund:+.4f}% ≥ {value:g}%"
    return False, ""


def evaluate(send=None) -> dict:
    """Aktif alarmları kontrol et; tetiklenenleri döndür (ve isteğe bağlı bildir)."""
    rows = load()
    active = [r for r in rows if r.get("enabled")]
    cache: dict[str, dict[str, float | None]] = {}
    fired = []
    changed = False
    for r in active:
        sym = r["symbol"]
        if sym not in cache:
            cache[sym] = market(sym)
        ok, why = _hit(r["kind"], float(r["value"]), cache[sym])
        if not ok:
            continue
        r["trigger_count"] = int(r.get("trigger_count") or 0) + 1
        r["triggered_at"] = now()
        if r.get("one_shot"):
            r["enabled"] = False
        text = (f"<b>ALARM · {sym}</b>\n{KINDS[r['kind']]} → {r['value']:g}\n"
                f"{why}" + (f"\n<i>{r['note']}</i>" if r.get("note") else ""))
        if send:
            send(text, key=f"alarm:{r['id']}", cooldown_min=COOLDOWN_MIN)
        fired.append({"id": r["id"], "symbol": sym, "kind": r["kind"], "why": why,
                      "one_shot": bool(r.get("one_shot"))})
        changed = True
    if changed:
        save(rows)
    return {"checked": len(active), "fired": fired,
            "symbols": sorted(cache), "market": cache}


def status() -> dict:
    rows = load()
    return {"alarms": rows, "count": len(rows),
            "enabled": sum(1 for r in rows if r.get("enabled")),
            "kinds": KINDS,
            "note": "Tek seferlik alarm tetiklenince kapanır; düğmeyle yeniden açabilirsin."}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 3 and sys.argv[1] == "add":
        print(add(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else 0))
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
