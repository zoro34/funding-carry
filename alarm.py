#!/usr/bin/env python3
"""Alarm tarayicisi — cron'dan calisir, Telegram'a gonderir.

Yaptigi:
  1) Kagit pozisyonlari degerlendirir; stop/hedefe degenleri kapatip bildirir.
  2) Dipte olan sembolleri tarar (dip_study rows), yeni DIP varsa tek ozet mesaj atar.
Ayni mesaj tekrarini notify cooldown'i engeller. Token yoksa kuyruga yazar.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import notify
import watch
import paper

ROOT = Path(__file__).parent
STUDY = ROOT / "reports" / "dip_study.json"
DIP_KEY_COOLDOWN_H = 4


def dip_digest() -> dict | None:
    try:
        d = json.loads(STUDY.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = [r for r in d.get("rows", []) if r.get("is_dip")]
    rows = watch.filter_symbols(rows)          # sadece izlenen semboller (liste doluysa)
    if not rows:
        return None
    # ayni sembol seti 4 saatte bir bildirilir
    key = "dip:" + ",".join(sorted(r["symbol"] for r in rows))
    lines = [f"<b>Dipte olanlar</b> ({len(rows)} sembol)"]
    for r in rows[:8]:
        lines.append(f"• <b>{r['symbol']}</b> konum %{r['pos_pct']:.0f} · 24s {r['chg_24h']:+.1f}%")
    h24 = (d.get("horizons") or {}).get("h24") or {}
    dip = h24.get("dip") or {}
    if dip.get("median") is not None:
        lines.append(f"\n<i>Ölçülen +24s medyan {dip['median']:+.2f}% — dip alımı ölçülen avantaj "
                     f"sağlamıyor, durum göstergesi.</i>")
    return notify.send("\n".join(lines), key=key, cooldown_min=DIP_KEY_COOLDOWN_H * 60)


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    r = paper.report(alert=True)
    print(f"{stamp} | acik {r['open']} | acik PnL {r['unrealized_usd']:+.2f}$ | "
          f"gerceklesen {r['realized_usd']:+.2f}$ | telegram {r['telegram']['configured']}")
    for p in r["positions"]:
        if p["status"] != "open":
            continue
        print(f"  {p['symbol']:<10} {p['pnl_pct']:+.2f}% ({p['pnl_usd']:+.2f}$) "
              f"R {p['r_multiple']:+.2f} stop {p['to_stop_pct']:+.1f}%")
    dg = dip_digest()
    if dg:
        print(f"  dip ozeti: sent={dg.get('sent')} {dg.get('reason') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
