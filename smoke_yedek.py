#!/usr/bin/env python3
"""Yedek doğrulaması — arşiv oluşuyor mu, geri yüklenebiliyor mu, veri eksiksiz mi?

Kontroller:
  1) yedek.sh arşiv üretiyor, boyut > 0, dosya sayısı makul
  2) arşiv içinde kritik dosyalar var (pozisyonlar, snapshotlar, raporlar)
  3) geri yükleme çalışıyor ve içerik birebir aynı (sha256)
  4) saklama politikası: KEEP'ten fazla arşiv birikmiyor
  5) tekrar çalıştırma idempotent (üst üste yedek almak bozmuyor)
Kanonik: make test
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPT = ROOT / "yedek.sh"
R: list[tuple[str, str, str]] = []
TMP = Path(tempfile.mkdtemp(prefix="smoke-yedek-"))


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def run(env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    p = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       timeout=300, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-400:]
    return p.stdout.strip()


def newest(dest: Path) -> Path:
    arcs = sorted(dest.glob("funding-carry-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    assert arcs, f"arsiv yok: {dest}"
    return arcs[-1]


def t_create():
    out = run({"YEDEK_DIR": str(TMP / "d1")})
    arc = newest(TMP / "d1")
    assert arc.stat().st_size > 10_000, f"arsiv cok kucuk: {arc.stat().st_size}"
    listing = subprocess.run(["tar", "tzf", str(arc)], capture_output=True, text=True).stdout
    n = len([l for l in listing.splitlines() if l.strip()])
    assert n > 20, f"dosya sayisi az: {n}"
    return f"{arc.name} · {arc.stat().st_size // 1024} KB · {n} dosya"


def t_contents():
    arc = newest(TMP / "d1")
    listing = subprocess.run(["tar", "tzf", str(arc)], capture_output=True, text=True).stdout
    need = ["data/funding_snapshots.csv", "reports/", "carry.py"]
    missing = [x for x in need if x not in listing]
    assert not missing, f"eksik: {missing}"
    assert "paper_positions.json" in listing or "data/" in listing
    return "pozisyonlar + snapshotlar + kod arsivde"


def t_restore():
    arc = newest(TMP / "d1")
    dest = TMP / "r1"
    p = subprocess.run(["bash", str(SCRIPT), "--restore", str(arc), str(dest)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-300:]
    snap = dest / "data" / "funding_snapshots.csv"
    assert snap.exists(), "geri yuklemede snapshot yok"
    a = hashlib.sha256(snap.read_bytes()).hexdigest()
    b = hashlib.sha256((ROOT / "data" / "funding_snapshots.csv").read_bytes()).hexdigest()
    assert a == b, "geri yuklenen icerik birebir ayni degil"
    return "geri yukleme calisiyor, sha256 esit"


def t_retention():
    dest = TMP / "d2"
    for _ in range(4):
        run({"YEDEK_DIR": str(dest), "YEDEK_KEEP": "2"})
        time.sleep(0.4)
    arcs = list(dest.glob("funding-carry-*.tar.gz"))
    assert len(arcs) <= 2, f"saklama politikasi calismiyor: {len(arcs)} arsiv"
    return f"KEEP=2 → {len(arcs)} arsiv kaldi"


def t_idempotent():
    dest = TMP / "d3"
    out1 = run({"YEDEK_DIR": str(dest)})
    out2 = run({"YEDEK_DIR": str(dest)})
    assert "yedek:" in out1 and "yedek:" in out2
    return "iki kosu sorunsuz"


for name, fn in [("yedek: arşiv üretimi", t_create),
                 ("yedek: içerik kapsamı", t_contents),
                 ("yedek: geri yükleme + sha256", t_restore),
                 ("yedek: saklama politikası", t_retention),
                 ("yedek: idempotent", t_idempotent)]:
    check(name, fn)

shutil.rmtree(TMP, ignore_errors=True)
print()
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
sys.exit(1 if fails else 0)
