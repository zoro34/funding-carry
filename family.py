#!/usr/bin/env python3
"""Aile paneli — çoklu çocuk, cihaz raporlari, ekran süresi, konum, ihlal bildirimi.

MODEL (önemli): gizli gözetim YOK. Çocuğun cihazina kurulan AJAN, kurulumda izinleri
acikca ister ve "bu cihaz aile paneliyle paylasilir" der. Ajan, link ile eslesir
(invite token) ve veriyi bu sunucuya gönderir. WhatsApp içerigi okunmaz (E2E sifreli,
teknik olarak erisilemez).

Ajan sözleşmesi (POST /api/family/report):
  {"token": "<invite>", "device": "Redmi 9A",
   "screen_minutes": 214,                     # bugün toplam ekran süresi
   "apps": [{"pkg": "com.whatsapp", "minutes": 87}, ...],
   "unlocks": 41,                             # açılma sayısı
   "battery": 62,                             # %
   "location": {"lat": 41.0, "lon": 29.0, "accuracy_m": 12},
   "reported_at": "2026-11-16T18:00:00Z"}     # opsiyonel
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import notify

ROOT = Path(__file__).parent
STORE = ROOT / "data" / "family.json"
REPORTS = ROOT / "data" / "family_reports.jsonl"
MAX_CHILDREN = 10
KEEP_DAYS = 30

# Varsayilan kurallar (panelden degistirilebilir)
DEFAULTS = {"daily_limit_min": 180, "bedtime_start": "22:00", "bedtime_end": "07:00",
            "blocked_apps": [], "app_limits": {}}


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ depolama
def load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        d.setdefault("children", [])
        return d
    except Exception:
        return {"children": []}


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def add_child(name: str, daily_limit_min: int | None = None) -> dict:
    name = (name or "").strip()
    if not (1 <= len(name) <= 40):
        raise ValueError("isim 1-40 karakter olmalı")
    d = load()
    if len(d["children"]) >= MAX_CHILDREN:
        raise ValueError(f"en fazla {MAX_CHILDREN} çocuk")
    if any(c["name"].lower() == name.lower() for c in d["children"]):
        raise ValueError(f"{name} zaten kayıtlı")
    child = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "invite": secrets.token_urlsafe(12),
        "paired": False,
        "device": None,
        "created_at": now().isoformat(),
        "rules": {**DEFAULTS, **({"daily_limit_min": int(daily_limit_min)}
                                 if daily_limit_min else {})},
    }
    d["children"].append(child)
    save(d)
    return child


def remove_child(cid: str) -> bool:
    d = load()
    left = [c for c in d["children"] if c["id"] != cid]
    if len(left) == len(d["children"]):
        return False
    d["children"] = left
    save(d)
    return True


def set_rules(cid: str, **rules) -> dict:
    d = load()
    for c in d["children"]:
        if c["id"] == cid:
            if "daily_limit_min" in rules and rules["daily_limit_min"] is not None:
                c["rules"]["daily_limit_min"] = max(0, int(rules["daily_limit_min"]))
            if "bedtime_start" in rules and rules["bedtime_start"]:
                c["rules"]["bedtime_start"] = str(rules["bedtime_start"])[:5]
            if "bedtime_end" in rules and rules["bedtime_end"]:
                c["rules"]["bedtime_end"] = str(rules["bedtime_end"])[:5]
            if "blocked_apps" in rules and rules["blocked_apps"] is not None:
                c["rules"]["blocked_apps"] = [str(x)[:80] for x in rules["blocked_apps"]][:50]
            if "app_limits" in rules and rules["app_limits"] is not None:
                c["rules"]["app_limits"] = {str(k)[:80]: max(0, int(v))
                                            for k, v in dict(rules["app_limits"]).items()}
            save(d)
            return c
    raise ValueError("çocuk bulunamadı")


def _by_invite(token: str) -> tuple[dict, dict]:
    d = load()
    for c in d["children"]:
        if c["invite"] == token:
            return d, c
    raise ValueError("geçersiz davet anahtarı")


# ------------------------------------------------------------------- rapor
def ingest(payload: dict) -> dict:
    token = (payload.get("token") or "").strip()
    if not token:
        raise ValueError("token gerekli")
    d, child = _by_invite(token)

    try:
        screen = max(0, min(int(payload.get("screen_minutes") or 0), 24 * 60))
    except Exception:
        screen = 0
    apps = []
    for a in (payload.get("apps") or [])[:60]:
        try:
            apps.append({"pkg": str(a.get("pkg"))[:80],
                         "minutes": max(0, min(int(a.get("minutes") or 0), 24 * 60))})
        except Exception:
            continue
    loc = payload.get("location") or None
    if loc is not None:
        try:
            loc = {"lat": float(loc["lat"]), "lon": float(loc["lon"]),
                   "accuracy_m": float(loc.get("accuracy_m") or 0)}
        except Exception:
            loc = None

    rec = {"ts": now().isoformat(), "child_id": child["id"], "name": child["name"],
           "device": str(payload.get("device") or child.get("device") or "cihaz")[:60],
           "screen_minutes": screen, "apps": apps,
           "unlocks": int(payload.get("unlocks") or 0),
           "battery": payload.get("battery"), "location": loc}
    REPORTS.parent.mkdir(parents=True, exist_ok=True)
    with REPORTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    child.update({"paired": True, "device": rec["device"], "last_seen": rec["ts"]})
    save(d)

    alerts = _check_rules(child, rec)
    for a in alerts:
        notify.send(a["text"], key=f"family:{child['id']}:{a['kind']}", cooldown_min=180)
    return {"ok": True, "child": child["name"], "alerts": [a["kind"] for a in alerts]}


def _check_rules(child: dict, rec: dict) -> list[dict]:
    out = []
    r = child.get("rules", {})
    limit = int(r.get("daily_limit_min") or 0)
    if limit and rec["screen_minutes"] > limit:
        out.append({"kind": "screen_limit", "text":
                    f"<b>{child['name']}</b> günlük ekran süresini aştı: "
                    f"{rec['screen_minutes']} dk (limit {limit} dk)"})
    blocked = set(r.get("blocked_apps") or [])
    hit = [a for a in rec["apps"] if a["pkg"] in blocked and a["minutes"] > 0]
    if hit:
        out.append({"kind": "blocked_app", "text":
                    f"<b>{child['name']}</b> kısıtlı uygulama kullandı: " +
                    ", ".join(f"{a['pkg']} {a['minutes']}dk" for a in hit[:5])})
    for pkg, lim in (r.get("app_limits") or {}).items():
        for a in rec["apps"]:
            if a["pkg"] == pkg and lim and a["minutes"] > int(lim):
                out.append({"kind": f"app_limit:{pkg}", "text":
                            f"<b>{child['name']}</b> · {pkg} limiti aşıldı: "
                            f"{a['minutes']} dk (limit {lim} dk)"})
    return out


# ------------------------------------------------------------------ ozet
def _human_min(m: int) -> str:
    return f"{m // 60}s {m % 60}dk" if m >= 60 else f"{m}dk"


def summary() -> dict:
    d = load()
    today = now().date().isoformat()
    reports: list[dict] = []
    if REPORTS.exists():
        for line in REPORTS.read_text(encoding="utf-8").splitlines():
            try:
                reports.append(json.loads(line))
            except Exception:
                continue
    # son 30 gun
    cutoff = now().timestamp() - KEEP_DAYS * 86400
    reports = [r for r in reports
               if datetime.fromisoformat(r["ts"]).timestamp() >= cutoff]

    children = []
    for c in d["children"]:
        mine = [r for r in reports if r["child_id"] == c["id"]]
        todays = [r for r in mine if r["ts"][:10] == today]
        last = mine[-1] if mine else None
        # gunun en yuksek degeri (ajan gun icinde artan deger gonderir)
        screen_today = max((r["screen_minutes"] for r in todays), default=0)
        app_tot: dict[str, int] = {}
        for r in todays[-12:]:
            for a in r["apps"]:
                app_tot[a["pkg"]] = max(app_tot.get(a["pkg"], 0), a["minutes"])
        top = sorted(app_tot.items(), key=lambda kv: -kv[1])[:5]
        children.append({
            "id": c["id"], "name": c["name"], "paired": c.get("paired", False),
            "device": c.get("device"), "invite": c["invite"],
            "last_seen": c.get("last_seen"),
            "minutes_since_seen": (round((now() - datetime.fromisoformat(c["last_seen"]))
                                         .total_seconds() / 60)
                                   if c.get("last_seen") else None),
            "screen_today_min": screen_today,
            "screen_today_h": _human_min(screen_today),
            "limit_min": c["rules"].get("daily_limit_min"),
            "over_limit": bool(c["rules"].get("daily_limit_min")
                               and screen_today > c["rules"]["daily_limit_min"]),
            "top_apps": [{"pkg": p, "minutes": m} for p, m in top],
            "unlocks": last["unlocks"] if last else None,
            "battery": last["battery"] if last else None,
            "location": last["location"] if last else None,
            "reports_today": len(todays),
            "rules": c["rules"],
        })
    totals = {
        "children": len(children),
        "paired": sum(1 for c in children if c["paired"]),
        "over_limit": sum(1 for c in children if c["over_limit"]),
        "reports": len(reports),
    }
    return {"generated_at": now().isoformat(), "children": children, "totals": totals,
            "telegram": notify.status(),
            "notice": "Açık rızalı ajan modeli: WhatsApp içeriği okunmaz (uçtan uca şifreli). "
                      "Veriler çocuğun cihazında görünen aile ajanı tarafından gönderilir."}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        c = add_child(sys.argv[2] if len(sys.argv) > 2 else "Çocuk")
        print(f"eklendi: {c['name']} · davet: {c['invite']}")
    else:
        s = summary()
        print(f"çocuk: {s['totals']['children']} (eşleşen {s['totals']['paired']}) · "
              f"rapor: {s['totals']['reports']}")
        for c in s["children"]:
            print(f"  {c['name']:<12} {c['screen_today_h']:>8} "
                  f"limit {c['limit_min']}dk {'AŞTI' if c['over_limit'] else ''} "
                  f"| {c['device'] or 'cihaz yok'} | {c['last_seen'] or '—'}")
