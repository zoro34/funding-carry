#!/usr/bin/env python3
"""Telegram bildirimi — token varsa gönderir, yoksa kuyruğa yazar.

Kural: sessizce başarısız olma. Token yoksa mesaj reports/telegram_outbox.jsonl'a
düşer ve panelde "Telegram bağlı değil" olarak görünür.
Aynı olay tekrar tekrar gönderilmesin: anahtar + cooldown ile bastırılır.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
OUTBOX = ROOT / "reports" / "telegram_outbox.jsonl"
STATE = ROOT / "data" / "telegram_state.json"
ENV_FILES = [Path.home() / ".hermes" / ".env", ROOT / ".env"]


def _read_env() -> dict[str, str]:
    """~/.hermes/.env + yerel .env — yalnizca GERCEK atamalar (yorum satiri degil)."""
    out: dict[str, str] = {}
    for p in ENV_FILES:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and not v.startswith("#"):
                out.setdefault(k, v)
    return out


def creds() -> tuple[str | None, str | None]:
    env = {**_read_env(), **os.environ}
    return env.get("TELEGRAM_BOT_TOKEN") or None, env.get("TELEGRAM_CHAT_ID") or None


def status() -> dict:
    tok, chat = creds()
    pending = 0
    if OUTBOX.exists():
        pending = sum(1 for _ in OUTBOX.open(encoding="utf-8"))
    return {"configured": bool(tok and chat), "has_token": bool(tok),
            "has_chat": bool(chat), "pending_unsent": pending}


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")


def send(text: str, key: str | None = None, cooldown_min: int = 60) -> dict:
    """Mesaj gönder. key verilirse cooldown içinde tekrar gönderilmez."""
    now = time.time()
    st = _state()
    if key:
        h = hashlib.sha1(key.encode()).hexdigest()[:16]
        last = st.get(h, 0)
        if now - last < cooldown_min * 60:
            return {"sent": False, "reason": "cooldown", "key": key}
        st[h] = now
        # eski anahtarları buda
        if len(st) > 500:
            st = {k: v for k, v in st.items() if now - v < 7 * 86400}
        _save_state(st)

    tok, chat = creds()
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "text": text, "key": key}
    if not (tok and chat):
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**rec, "status": "no_credentials"}, ensure_ascii=False) + "\n")
        return {"sent": False, "reason": "no_credentials", "queued": True}

    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            ok = json.loads(r.read()).get("ok", False)
        if not ok:
            raise RuntimeError("telegram ok=false")
    except Exception as e:  # noqa: BLE001
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**rec, "status": "error", "error": str(e)[:200]},
                               ensure_ascii=False) + "\n")
        return {"sent": False, "reason": "error", "error": str(e)[:200], "queued": True}

    return {"sent": True}


def send_test() -> dict:
    return send("<b>Funding Carry</b>\nTest mesajı — bildirim kanalı çalışıyor.",
                key="test", cooldown_min=0)


if __name__ == "__main__":
    s = status()
    print(json.dumps(s, indent=1, ensure_ascii=False))
    if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "test":
        print(send_test())
