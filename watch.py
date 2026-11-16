#!/usr/bin/env python3
"""Bildirim izleme listesi — SADECE seçtiğin semboller için Telegram bildirimi.

Kurallar:
  * liste boşsa: eski davranış (tüm dipte olanlar özetlenir)
  * liste doluysa: yalnızca listedeki semboller bildirilir (dip özeti + fiyat uyarısı)
  * pozisyon (kâğıt) uyarıları her zaman gider — senin açtığın pozisyondur

Kullanim:
  watch.add("BTCUSDT") / watch.remove("BTCUSDT") / watch.list_items() / watch.active()
Telegram komutları (tg_bot.py): /izle BTCUSDT · /birak BTCUSDT · /liste · /temizle
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
STORE = ROOT / "data" / "alerts_watch.json"
MAX = 40


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        d.setdefault("symbols", [])
        return d
    except Exception:
        return {"symbols": [], "updated_at": None}


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    d["updated_at"] = now()
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(s: str) -> str:
    return (s or "").strip().upper().replace("/", "").replace("-", "")


def add(*symbols: str) -> dict:
    d = load()
    added = []
    for s in symbols:
        s = _norm(s)
        if not s:
            continue
        if not s.endswith("USDT") and not s.endswith("USDC"):
            s += "USDT"
        if s not in d["symbols"] and len(d["symbols"]) < MAX:
            d["symbols"].append(s)
            added.append(s)
    save(d)
    return {"added": added, "symbols": d["symbols"]}


def remove(*symbols: str) -> dict:
    d = load()
    rm = []
    for s in symbols:
        s = _norm(s)
        if not s.endswith("USDT") and not s.endswith("USDC") and s:
            s += "USDT"
        if s in d["symbols"]:
            d["symbols"].remove(s)
            rm.append(s)
    save(d)
    return {"removed": rm, "symbols": d["symbols"]}


def clear() -> dict:
    save({"symbols": []})
    return {"symbols": []}


def list_items() -> list[str]:
    return list(load()["symbols"])


def active() -> bool:
    """Liste doluysa sembol filtresi uygulanır."""
    return bool(list_items())


def filter_symbols(rows: list[dict]) -> list[dict]:
    """Verilen satırları izleme listesine göre süz (liste boşsa hepsi)."""
    syms = set(list_items())
    if not syms:
        return rows
    return [r for r in rows if _norm(r.get("symbol", "")) in syms]


def status() -> dict:
    d = load()
    return {"symbols": d["symbols"], "count": len(d["symbols"]),
            "active": bool(d["symbols"]), "updated_at": d.get("updated_at"),
            "note": "Liste boşsa tüm dipte olanlar özetlenir; doluysa yalnızca seçilenler."}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "add":
        print(add(*sys.argv[2:]))
    elif len(sys.argv) > 2 and sys.argv[1] == "remove":
        print(remove(*sys.argv[2:]))
    elif len(sys.argv) > 1 and sys.argv[1] == "clear":
        print(clear())
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
