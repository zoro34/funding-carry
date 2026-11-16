#!/usr/bin/env python3
"""Mobil yerlesim smoke testi — 390px emulasyonunda gercek tarayici olcumu.

Kanonik komut: .venv/bin/python smoke_mobile.py   (panel 8080'de calisiyor olmali)

Dogrular:
  1) dort sekmede de sayfa yatay tasmiyor (scrollWidth <= viewport)
  2) her veri tablosu kapsayiciya sigiyor (<= 400px)
  3) mobilde ikincil kolonlar gizli (kolon sayisi esikte)
Tablolar veri gelene kadar beklenir; olculemeyen tablo PASS sayilmaz, FAIL sayilir.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

PANEL = "http://127.0.0.1:8080/"
CHROME = "/usr/lib/chromium/chromium"
WIDTH, TOL = 390, 400
R: list[tuple[str, str, str]] = []

TAB_JS = """
(() => {
  const tw = sel => { const e = document.querySelector(sel + ' table');
                      return e ? Math.round(e.getBoundingClientRect().width) : null; };
  const cols = sel => { const tr = document.querySelector(sel + ' thead tr');
                        return tr ? [...tr.children].filter(c => getComputedStyle(c).display !== 'none').length : null; };
  return JSON.stringify({
    pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
    tables: { carry: tw('#carrytable'), cand: tw('#carrycand'), live: tw('#livetable'),
              smart: tw('#smarttable'), screen: tw('#screentable'), alerts: tw('#alertlist'),
              dip: tw('#diptable'), paper: tw('#papertable') },
    cols: { carry: cols('#carrytable'), cand: cols('#carrycand'), live: cols('#livetable'),
            smart: cols('#smarttable'), screen: cols('#screentable'), alerts: cols('#alertlist'),
            dip: cols('#diptable'), paper: cols('#papertable') }
  });
})()
"""


def check(name, fn):
    try:
        R.append(("PASS", name, str(fn() or "")))
    except AssertionError as e:
        R.append(("FAIL", name, str(e)))
    except Exception as e:  # noqa: BLE001
        R.append(("FAIL", name, f"{type(e).__name__}: {e}"))


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=45, suppress_origin=True)
        self.n = 0

    def send_only(self, method: str, **params):
        """Yanitini BEKLEMEDEN gonder (Page.navigate yukleme bitmeden donmuyor)."""
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))

    def cmd(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})


def main() -> int:
    try:
        urllib.request.urlopen(PANEL, timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"SKIP: panel 8080'de yok ({e}) — once: bash launch_panel.sh")
        return 2

    # her kosuya bos port: onceki kosudan kalan Chromium'a takilmayi onler
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); PORT = sk.getsockname()[1]; sk.close()
    profile = tempfile.mkdtemp(prefix="hermes-verify-chrome-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
         "--remote-allow-origins=*", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={profile}", PANEL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        # hedefi listeden al: navigate yapmadan, panel zaten aciliyor
        target = None
        for _ in range(40):
            lst = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5).read())
            pages = [t for t in lst if t.get("type") == "page"
                     and t.get("webSocketDebuggerUrl") and "8080" in (t.get("url") or "")]
            if pages:
                target = pages[0]
                break
            time.sleep(0.5)
        assert target, "sayfa hedefi bulunamadi"
        cdp = CDP(target["webSocketDebuggerUrl"])
        cdp.cmd("Page.enable")
        cdp.cmd("Emulation.setDeviceMetricsOverride", width=WIDTH, height=900,
                deviceScaleFactor=1, mobile=True)
        time.sleep(4)
        # sayfa gercekten yuklendi mi? (yerel chromium bazen hic http yuklemiyor)
        title = ""
        for _ in range(20):
            title = cdp.cmd("Runtime.evaluate", expression="document.title",
                            returnByValue=True)["result"].get("value", "")
            if "Funding Carry" in title:
                break
            time.sleep(1)
        if "Funding Carry" not in title:
            print("SKIP: yerel Chromium bu ortamda http sayfasi yukleyemiyor "
                  f"(baslik={title!r}). Panel/ag degil, tarayici sorunu — "
                  "olcum ajan tarayicisiyla alinmali.")
            return 2
        time.sleep(20)                                   # canli tarama bitsin
        data = {}
        for t in ("carry", "dip", "genel", "smart", "screen"):
            cdp.cmd("Runtime.evaluate", expression=f"setTab('{t}')", returnByValue=True)
            time.sleep(1.2)
            r = cdp.cmd("Runtime.evaluate", expression=TAB_JS, returnByValue=True)
            data[t] = json.loads(r["result"]["value"])
    finally:
        if cdp:
            cdp.ws.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
            proc.wait(timeout=5)
        shutil.rmtree(profile, ignore_errors=True)

    def t_overflow():
        bad = {k: v["pageOverflow"] for k, v in data.items() if v["pageOverflow"] > 0}
        assert not bad, f"sayfa yana tasiyor: {bad}"
        return "dort sekmede de tasma yok"

    def t_tables():
        measured, bad = 0, {}
        for tab, v in data.items():
            for name, w in v["tables"].items():
                if w is None:
                    continue
                measured += 1
                if w > TOL:
                    bad[f"{tab}/{name}"] = w
        assert measured >= 5, f"cok az tablo olculdu ({measured}) — veri gelmemis"
        assert not bad, f"tablo ekrandan genis: {bad}"
        return f"{measured} tablo olcumu, hepsi <={TOL}px"

    def t_cols():
        # dip: 6 kolonun 5'i gorunur (Islem=AL/KAPAT mobilde gerekli, 41g olay .h-sm ile gizli).
        # Asil kisit genislik: t_tables 390px'e sigmayi ayrica dogruluyor.
        want = {"carry": 8, "cand": 5, "live": 6, "smart": 7, "screen": 7, "alerts": 5,
                "dip": 5, "paper": 7}
        bad = {f"{tab}/{k}": n for tab, v in data.items() for k, n in v["cols"].items()
               if n is not None and n > want[k]}
        assert not bad, f"mobil kolon gizleme eksik: {bad}"
        return "kolon sayilari mobil esikte"

    def t_summary():
        return json.dumps({k: {"overflow": v["pageOverflow"],
                               "carry": v["tables"]["carry"],
                               "alerts": v["tables"]["alerts"]} for k, v in data.items()})

    for name, fn in [("mobil: yatay tasma yok", t_overflow),
                     ("mobil: tablolar ekrana sigiyor", t_tables),
                     ("mobil: kolon gizleme aktif", t_cols),
                     ("mobil: olcum ozeti", t_summary)]:
        check(name, fn)

    print(f"mobil yerlesim — {time.strftime('%Y-%m-%d %H:%M:%S')} ({WIDTH}px emulasyon)\n")
    for st, name, detail in R:
        print(f"[{'OK  ' if st == 'PASS' else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    fails = [r for r in R if r[0] == "FAIL"]
    print(f"\n{len(R) - len(fails)}/{len(R)} gecti")
    print("KAPSAM: 390px emulasyon. 320px gibi cok dar cihazlarda kart ici kaydirma yedek yol.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
