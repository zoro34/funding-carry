#!/usr/bin/env python3
"""Ad-hoc dogrulama: apk_paylas.sh (LAN APK paylasimi).

Dogrulanan davranis:
  1) Canli paylasim 8090'da dinliyor, APK bayt-bayt yerel dosyayla ayni (sha256)
  2) Dizin listesi 200, dosya adi dogru
  3) Betik idempotent: ayni portta iki kez calistirilinca tek dinleyici kalir
  4) Yanlis/eski surec olduruluyor (port cakismasi yok)
  5) Paylasim dizininde yalnizca APK var (data/sirlar sizmaz)
8091 uzerinde test eder, kullanicinin 8090 paylasimina dokunmaz.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("/root/binance-bot")
APK = ROOT / "FundingCarry-1.4.apk"
SHARE = Path("/root/apk-share")
R: list[tuple[str, str, str]] = []


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def listeners(port: int) -> list[str]:
    out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True).stdout
    rows = [l for l in out.splitlines() if f":{port} " in l]
    pids = []
    for r in rows:
        pids += re.findall(r"pid=(\d+)", r)
    return pids


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        assert r.status == 200, r.status
        return r.read()


def t_live():
    assert listeners(8090), "8090'da dinleyen yok — paylasim kapali"
    rows = subprocess.run(["ss", "-tln"], capture_output=True, text=True).stdout.splitlines()
    assert any("0.0.0.0:8090" in r or "*:8090" in r for r in rows), \
        "0.0.0.0'a bagli degil — telefon ulasamaz"
    body = get("http://127.0.0.1:8090/FundingCarry-1.4.apk")
    assert len(body) == APK.stat().st_size, (len(body), APK.stat().st_size)
    assert hashlib.sha256(body).hexdigest() == sha(APK), "indirilen APK yerel dosyayla ayni degil"
    return f"{len(body)} bayt, sha256 esit, 0.0.0.0:8090"


def t_listing():
    page = get("http://127.0.0.1:8090/").decode()
    assert "FundingCarry-1.4.apk" in page, "listede APK yok"
    return "dizin listesinde APK gorunuyor"


def t_idempotent():
    for attempt in (1, 2):
        p = subprocess.run(["bash", str(ROOT / "apk_paylas.sh"), "8091"],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-300:]
        time.sleep(1)
        pids = set(listeners(8091))
        assert len(pids) == 1, f"{attempt}. kosu: beklenen 1 dinleyici, bulunan {len(pids)}"
        body = get("http://127.0.0.1:8091/FundingCarry-1.4.apk")
        assert hashlib.sha256(body).hexdigest() == sha(APK), "2. kosudan sonra icerik bozuk"
    return "iki kosu -> tek dinleyici, servis saglam"


def t_share_scope():
    files = sorted(p.name for p in SHARE.iterdir() if p.is_file())
    assert files == ["FundingCarry-1.4.apk"], f"paylasim dizini beklenenden genis: {files}"
    return f"yalnizca {files[0]} paylasimda (data/ ve pozisyonlar disarida)"


check("paylasim: canli servis + bayt esitligi", t_live)
check("paylasim: dizin listesi", t_listing)
check("betik: idempotent yeniden baslatma", t_idempotent)
check("paylasim: kapsam (sadece APK)", t_share_scope)

# 8091 test sunucusunu kapat (kullanicinin 8090'i acik kalir)
for pid in set(listeners(8091)):
    subprocess.run(["kill", pid], check=False)

print()
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
print("KAPSAM DISI: fiziksel telefondan indirme/kurulum (bu makineden test edilemez), "
      "WiFi izolasyonu (AP isolation) — telefon ulasamazsa ayri konu.")
sys.exit(1 if fails else 0)
