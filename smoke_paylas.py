#!/usr/bin/env python3
"""APK paylaşımı doğrulaması — "404 döndü / inmedi" hatasını bir daha kaçırmamak için.

Kontroller:
  1) Paketleme: en yeni FundingCarry-*.apk, build çıktısıyla BİREBİR aynı (sha256)
     → yeni build paylaşım adıyla paketlenmediyse burada yakalanır (404'ün kök nedeni)
  2) Canlı servis: 8090 dinliyor, 0.0.0.0'a bağlı, indirilen baytlar yerelle aynı
  3) Dizin listesi: dosya adı görünüyor (uzun ad yazmadan indirme)
  4) Betik idempotent: 8091'de iki kez çalıştır → tek dinleyici
  5) Kapsam: paylaşım dizininde yalnızca APK var (data/ sızmaz)
Kanonik: make test (bu dosyayı da koşar)
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
BUILT = ROOT / "android-app/android/app/build/outputs/apk/debug/app-debug.apk"
SHARE = Path("/root/apk-share")
R: list[tuple[str, str, str]] = []


def _newest() -> Path:
    cands = sorted(ROOT.glob("FundingCarry-*.apk"),
                   key=lambda x: [int(n) for n in re.findall(r"\d+", x.stem)] or [0])
    return cands[-1] if cands else ROOT / "FundingCarry-yok.apk"


APK = _newest()


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
    pids: list[str] = []
    for r in rows:
        pids += re.findall(r"pid=(\d+)", r)
    return pids


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        assert r.status == 200, r.status
        return r.read()


def t_packaged():
    assert APK.exists(), f"paketlenmiş APK yok: {APK.name}"
    assert BUILT.exists(), "build çıktısı yok (önce make apk)"
    assert sha(APK) == sha(BUILT), (
        f"{APK.name} build çıktısıyla aynı değil — yeni build paketlenmedi "
        f"(paylaşımda 404/eski sürüm riski)")
    ver = re.search(r"(\d+\.\d+)", APK.stem)
    return f"{APK.name} = build çıktısı (sha256 eşit, sürüm {ver.group(1) if ver else '?'})"


def t_live():
    assert listeners(8090), "8090'da dinleyen yok — paylaşım kapalı"
    rows = subprocess.run(["ss", "-tln"], capture_output=True, text=True).stdout.splitlines()
    assert any("0.0.0.0:8090" in r or "*:8090" in r for r in rows), \
        "0.0.0.0'a bağlı değil — telefon ulaşamaz"
    body = get(f"http://127.0.0.1:8090/{APK.name}")
    assert len(body) == APK.stat().st_size, (len(body), APK.stat().st_size)
    assert hashlib.sha256(body).hexdigest() == sha(APK), "indirilen APK yerel dosyayla aynı değil"
    return f"{len(body)} bayt, sha256 eşit, 0.0.0.0:8090, ad: {APK.name}"


def t_listing():
    page = get("http://127.0.0.1:8090/").decode()
    assert APK.name in page, f"listede {APK.name} yok"
    return "dizin listesinde APK görünüyor (adı yazmadan indirme)"


def t_idempotent():
    for attempt in (1, 2):
        p = subprocess.run(["bash", str(ROOT / "apk_paylas.sh"), "8091"],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-300:]
        time.sleep(1)
        pids = set(listeners(8091))
        assert len(pids) == 1, f"{attempt}. koşu: beklenen 1 dinleyici, bulunan {len(pids)}"
        body = get(f"http://127.0.0.1:8091/{APK.name}")
        assert hashlib.sha256(body).hexdigest() == sha(APK), "2. koşudan sonra içerik bozuk"
    return "iki koşu → tek dinleyici, servis sağlam"


def t_share_scope():
    files = sorted(p.name for p in SHARE.iterdir() if p.is_file())
    assert files == [APK.name], f"paylaşım dizini beklenenden geniş: {files}"
    return f"yalnızca {files[0]} paylaşımda (data/ ve pozisyonlar dışarıda)"


check("paketleme: build = paylaşım dosyası", t_packaged)
check("paylaşım: canlı servis + bayt eşitliği", t_live)
check("paylaşım: dizin listesi", t_listing)
check("betik: idempotent yeniden başlatma", t_idempotent)
check("paylaşım: kapsam (sadece APK)", t_share_scope)

for pid in set(listeners(8091)):
    subprocess.run(["kill", pid], check=False)

print()
for st, name, detail in R:
    print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
fails = [r for r in R if r[0] == "FAIL"]
print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
print("KAPSAM DIŞI: fiziksel telefondan indirme/kurulum (bu makineden test edilemez), "
      "WiFi izolasyonu (AP isolation) — telefon ulaşamazsa ayrı konu.")
sys.exit(1 if fails else 0)
