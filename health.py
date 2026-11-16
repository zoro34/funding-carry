#!/usr/bin/env python3
"""Sağlık kontrolü — sessiz hataları görünür yapar.

Kontrol ettikleri:
  * veri tazeliği: funding_snapshots.csv son satırı kaç saat önce?
  * tarama yaşı: data.json / reports son güncelleme
  * aile/ajan yok, sadece veri hattı
  * paylaşım servisi (8090) ve panel (8080) ayakta mı?
  * disk alanı, cron günlüklerinin son yazım zamanı

Kanonik: health.status() -> dict
Cron: ~/.hermes/scripts/saglik_kontrol.sh (sorun varsa Telegram'a yazar)
"""
from __future__ import annotations

import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
NOW = lambda: datetime.now(timezone.utc)
STALE_H = 12          # tarama bu saatten eskiyse uyarı
STALE_DATA_H = 26     # bulut verisi bu saatten eskiyse uyarı


def _age_hours(ts: float | None) -> float | None:
    if not ts:
        return None
    return round((time.time() - ts) / 3600, 1)


def _mtime(p: Path) -> float | None:
    return p.stat().st_mtime if p.exists() else None


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(1.5)
        return s.connect_ex((host, port)) == 0


def _cron_log_age(name: str) -> float | None:
    return _age_hours(_mtime(ROOT / "reports" / name))


def status() -> dict:
    checks: list[dict] = []

    # 1) funding snapshot tazeliği
    snap = ROOT / "data" / "funding_snapshots.csv"
    age = None
    if snap.exists():
        try:
            df = pd.read_csv(snap, usecols=["scanned_at"])
            last = pd.to_datetime(df["scanned_at"], errors="coerce", utc=True).max()
            age = round((NOW() - last.to_pydatetime()).total_seconds() / 3600, 1)
        except Exception:
            age = None
    checks.append({"name": "funding snapshot", "age_h": age,
                   "ok": age is not None and age <= STALE_H,
                   "detail": f"son tarama {age} sa önce" if age is not None else "snapshot okunamadı"})

    # 2) bulut verisi (data.json)
    dj = ROOT / "data.json"
    dj_age = _age_hours(_mtime(dj))
    checks.append({"name": "bulut verisi (data.json)", "age_h": dj_age,
                   "ok": dj_age is not None and dj_age <= STALE_DATA_H,
                   "detail": f"{dj_age} sa önce üretildi" if dj_age else "data.json yok"})

    # 3) servisler
    panel = _port_open(8080)
    paylas = _port_open(8090)
    checks.append({"name": "panel 8080", "ok": panel, "detail": "açık" if panel else "kapalı"})
    checks.append({"name": "apk paylaşımı 8090", "ok": paylas,
                   "detail": "açık" if paylas else "kapalı"})

    # 4) cron günlükleri
    for log, limit in (("cron.log", 14), ("cloud_push.log", 26), ("alarm.log", 6)):
        a = _cron_log_age(log)
        checks.append({"name": log, "age_h": a, "ok": a is not None and a <= limit,
                       "detail": f"{a} sa önce" if a is not None else "log yok"})

    # 5) disk
    try:
        du = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5).stdout
        line = du.strip().splitlines()[-1].split()
        used_pct = int(line[4].rstrip("%"))
        free = line[3]
    except Exception:
        used_pct, free = -1, "?"
    checks.append({"name": "disk", "ok": 0 <= used_pct < 90,
                   "detail": f"%{used_pct} dolu · {free} boş"})

    bad = [c for c in checks if not c["ok"]]
    return {"generated_at": NOW().isoformat(), "checks": checks,
            "problems": [c["name"] + (" — " + c["detail"] if c.get("detail") else "") for c in bad],
            "ok": not bad,
            "summary": "her şey normal" if not bad else f"{len(bad)} sorun: " + ", ".join(c["name"] for c in bad)}


def alert_text(st: dict) -> str:
    lines = ["<b>Panel sağlık uyarısı</b>"]
    for p in st["problems"]:
        lines.append("• " + p)
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    s = status()
    print(json.dumps(s, ensure_ascii=False, indent=1))
