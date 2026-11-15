#!/usr/bin/env python3
"""Akilli para / balina akisi katmani — Binance public verisiyle.

NE OLCUYOR (hepsi gercek veri, uydurma sinyal yok):

1. NET TAKER AKISI  (para girisi)
   Her mumda taker_buy_quote = agresif ALICILARIN hacmi.
   net = 2*taker_buy_quote - quote_volume
   Pozitifse alicilar saticalardan fazla agresif -> gercek para girisi.
   Fiyat degisiminden farkli: fiyat yatayken bile para girebilir.

2. ACIK POZISYON (OI) DEGISIMI
   sumOpenInterestValue (USDT notional). Artan OI = yeni pozisyon aciliyor.
   Fiyat yukari + OI yukari -> yeni para long aciyor.
   Fiyat yukari + OI asagi -> short kapatiyor (yakit bitiyor).

3. BUYUK OYUNCU POZISYONU  (balina vekili)
   topLongShortPositionRatio: "top trader" hesaplarinin POZISYON agirlikli
   long orani. globalLongShortAccountRatio: kucuk hesaplarin orani.
   Ikisinin farki en degerli sinyal: balina long / retail short.

4. BALINA ISLEM IZI
   5 dakikalik mumlarda ortalama islem buyuklugu = quote_volume / trades.
   Median'in N kati uzerindeki dilimler "buyuk oyuncu dilimi" sayilir.

ZAMAN TUTARLILIGI: 5m mum x 288 = tam 24 saat. Boot etiket yok.

SINIR (durustce): bunlar DAVRANIS VEKILLERIDIR. Gercek cuzdan takibi
(Arkham/Nansen) on-chain veri ister ve ucretlidir. Buradaki sinyaller
"kim aldi" demez, "buyuk para hangi yonde baski kurdu" der.

HATA POLITIKASI: 403/418/429 Binance WAF'inin gecici bloklaridir; geri
cekilerek yeniden denenir. Hala alinamazsa sembol 'basarisiz' sayilir ve
sayilir — sessizce yutulmaz (bos tablo "veri yok" gibi gorunmesin).
"""
from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
S = requests.Session()
S.headers.update({"User-Agent": "funding-carry-monitor/2.0"})

BARS_PER_HOUR = 12          # 5m mum
MIN_WHALE_MULT = 3.0        # median islem buyuklugunun kac kati
MIN_WHALE_MUSDT = 1.0       # balina dilimi icin min hacim (milyon USDT)
RETRY_STATUS = {403, 418, 429, 500, 502, 503, 504}

STATS = {"requests": 0, "retries": 0, "failed": 0}


def _get(url: str, **params):
    """Getir; WAF bloklarinda uslu geri cekilmeyle 3 kez dene."""
    for attempt in range(4):
        try:
            STATS["requests"] += 1
            r = S.get(url, params=params or None, timeout=25)
            if r.status_code in RETRY_STATUS:
                STATS["retries"] += 1
                wait = (2 ** attempt) + random.uniform(0, 1.0)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            STATS["retries"] += 1
            if attempt == 3:
                break
            time.sleep((2 ** attempt) + random.uniform(0, 0.8))
    STATS["failed"] += 1
    return None


# ---------------------------------------------------------------- 1) mumlar
def klines_5m(symbol: str, limit: int = 288) -> pd.DataFrame:
    """VADELI 5m mumlar.

    ONEMLI: akis verisi vadeli piyasadan alinir cunku OI ve buyuk oyuncu
    oranlari da vadeli piyasadan geliyor. Spot hacmiyle vadeli pozisyonu
    karistirmak yanlis olur (ayni sembolde hacimler cok farkli).
    """
    d = _get(f"{FAPI}/fapi/v1/klines", symbol=symbol, interval="5m", limit=min(limit, 1000))
    if not d:
        return pd.DataFrame()
    df = pd.DataFrame(d, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"]:
        df[c] = df[c].astype(float)
    df["trades"] = df["trades"].astype(int)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)


def _window_stats(df: pd.DataFrame, bars: int) -> dict:
    if df.empty:
        return {"net_musd": 0.0, "vol_musd": 0.0, "buy_ratio": 0.5}
    w = df.tail(bars)
    vol = float(w["quote_volume"].sum())
    buy = float(w["taker_buy_quote"].sum())
    net = 2 * buy - vol
    return {
        "net_musd": net / 1e6,
        "vol_musd": vol / 1e6,
        "buy_ratio": (buy / vol) if vol > 0 else 0.5,
    }


def whale_activity(df: pd.DataFrame) -> dict:
    """5m mumlardan buyuk oyuncu izi.

    NASIL: her 5m dilimde ortalama islem buyuklugu = quote_volume / trades.
    Sabit bir kat (or. 3x medyan) kullanmak yaniltir cunku ortalama islem
    buyuklugu cogu zaman medyanin ALTINDA kalir -> hic sinyal cikmaz.
    Bunun yerine 24 saatlik dagilimin en buyuk %2'lik dilimi "balina dilimi"
    sayilir. Boylece her sembolde tutarli sayida (~6 dilim/gun) sinyal olusur.
    """
    empty = {"whale_net_musd": 0.0, "whale_vol_musd": 0.0, "whale_share": 0.0,
             "whale_minutes": 0, "avg_trade_usd": 0.0, "median_trade_usd": 0.0,
             "whale_threshold_usd": 0.0}
    if df.empty or int(df["trades"].sum()) == 0:
        return empty

    avg = (df["quote_volume"] / df["trades"].replace(0, pd.NA)).fillna(0.0)
    med = float(avg.median()) or 1.0
    # %98'lik dilim ana kural; medyan*1.05 sadece taban korumasi (dagitim duzse
    # her dilimi "balina" saymayi engeller). 1.5 carpani bazi sembollerde
    # p98'in ustune ciktigi icin hic sinyal uretmiyordu.
    thresh = max(float(avg.quantile(0.98)), 1.05 * med)
    is_whale = (avg >= thresh) & (df["quote_volume"] >= MIN_WHALE_MUSDT * 1e6)

    wq = df.loc[is_whale]
    total_vol = float(df["quote_volume"].sum()) or 1.0
    whale_vol = float(wq["quote_volume"].sum())
    whale_net = float((2 * wq["taker_buy_quote"] - wq["quote_volume"]).sum())

    return {
        "whale_net_musd": whale_net / 1e6,
        "whale_vol_musd": whale_vol / 1e6,
        "whale_share": whale_vol / total_vol,
        "whale_minutes": int(is_whale.sum()),
        "avg_trade_usd": float(avg.tail(12).mean()),   # son 1 saat
        "median_trade_usd": med,
        "whale_threshold_usd": thresh,
    }


# ------------------------------------------------------- 2) acik pozisyon (OI)
def open_interest(symbol: str, period: str = "1h", limit: int = 25) -> dict:
    d = _get(f"{FAPI}/futures/data/openInterestHist",
             symbol=symbol, period=period, limit=limit)
    if not d or len(d) < 2:
        return {"oi_now_musd": 0.0, "oi_chg_1h": 0.0, "oi_chg_24h": 0.0}
    vals = [float(x["sumOpenInterestValue"]) for x in d]
    now = vals[-1]
    return {
        "oi_now_musd": now / 1e6,
        "oi_chg_1h": (now / vals[-2] - 1) * 100 if vals[-2] else 0.0,
        "oi_chg_24h": (now / vals[0] - 1) * 100 if vals[0] else 0.0,
    }


# --------------------------------------------- 3) buyuk oyuncu vs retail orani
def positioning(symbol: str, period: str = "1h", limit: int = 25) -> dict:
    top = _get(f"{FAPI}/futures/data/topLongShortPositionRatio",
               symbol=symbol, period=period, limit=limit)
    glob = _get(f"{FAPI}/futures/data/globalLongShortAccountRatio",
                symbol=symbol, period=period, limit=limit)

    out = {"top_long_pct": 50.0, "top_long_chg": 0.0, "retail_long_pct": 50.0,
           "top_retail_gap": 0.0, "taker_ratio_now": 1.0}
    if top:
        la = [float(x["longAccount"]) * 100 for x in top]
        out["top_long_pct"] = la[-1]
        out["top_long_chg"] = la[-1] - la[0] if len(la) > 1 else 0.0
    if glob:
        out["retail_long_pct"] = float(glob[-1]["longAccount"]) * 100
    out["top_retail_gap"] = out["top_long_pct"] - out["retail_long_pct"]
    return out


# ------------------------------------------------------------------ birlestir
def smart_money(symbol: str) -> dict:
    df = klines_5m(symbol, 288)                 # tam 24 saat
    w1 = _window_stats(df, BARS_PER_HOUR)
    w4 = _window_stats(df, BARS_PER_HOUR * 4)
    w24 = _window_stats(df, BARS_PER_HOUR * 24)

    wh = whale_activity(df)
    oi = open_interest(symbol)
    pos = positioning(symbol)

    price_chg = 0.0
    spark: list[float] = []
    if not df.empty and len(df) > 1:
        price_chg = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        step = max(1, len(df) // 60)
        spark = [round(float(v), 8) for v in df["close"].iloc[::step].tolist()]

    label, tone = _label(price_chg, w24["net_musd"], oi["oi_chg_24h"],
                         pos["top_long_pct"], pos["top_retail_gap"], wh["whale_net_musd"])
    detail = _detail(price_chg, w24["net_musd"], oi["oi_chg_24h"],
                     pos["top_long_pct"], pos["top_retail_gap"], wh["whale_net_musd"])

    return {
        "symbol": symbol,
        "price_chg": round(price_chg, 2),
        "spark": spark,
        "net_1h_musd": round(w1["net_musd"], 2),
        "net_4h_musd": round(w4["net_musd"], 2),
        "net_24h_musd": round(w24["net_musd"], 2),
        "vol_24h_musd": round(w24["vol_musd"], 1),
        "buy_ratio_24h": round(w24["buy_ratio"] * 100, 1),
        "oi_now_musd": round(oi["oi_now_musd"], 1),
        "oi_chg_1h": round(oi["oi_chg_1h"], 2),
        "oi_chg_24h": round(oi["oi_chg_24h"], 2),
        "top_long_pct": round(pos["top_long_pct"], 1),
        "top_long_chg": round(pos["top_long_chg"], 2),
        "retail_long_pct": round(pos["retail_long_pct"], 1),
        "top_retail_gap": round(pos["top_retail_gap"], 1),
        "taker_ratio_now": round(pos["taker_ratio_now"], 2),
        "whale_net_musd": round(wh["whale_net_musd"], 2),
        "whale_vol_musd": round(wh["whale_vol_musd"], 2),
        "whale_share": round(wh["whale_share"] * 100, 1),
        "whale_minutes": wh["whale_minutes"],
        "avg_trade_usd": round(wh["avg_trade_usd"], 0),
        "median_trade_usd": round(wh["median_trade_usd"], 0),
        "label": label,
        "detail": detail,
        "tone": tone,
        "score": _flow_score(w24["net_musd"], oi["oi_chg_24h"],
                             pos["top_retail_gap"], wh["whale_net_musd"]),
    }


def _label(price_chg, net24, oi24, top_long, gap, whale_net) -> tuple[str, str]:
    """Yon + akis + OI kombinasyonundan okunur etiket. tone: good|bad|warn|info.

    Etiketler KISA tutulur: tabloda tek satira sigmali. Uzun aciklama
    tooltip'te (detail) tasinir.
    """
    inflow = net24 > 0
    oi_up = oi24 > 0

    # Diverjanlar once gelir: en bilgilendirici durumlar bunlar.
    if price_chg < -1 and whale_net > 0 and gap < -5:
        return "Balina dip topluyor", "good"
    if price_chg > 1 and whale_net < 0 and gap > 5:
        return "Balina tepede satıyor", "bad"

    if price_chg > 1 and oi_up and inflow:
        return "Yeni para giriyor", "good"
    if price_chg > 1 and not oi_up:
        return "Short kapatıyor", "warn"
    if price_chg < -1 and oi_up and not inflow:
        return "Short birikimi", "bad"
    if price_chg > 0.3 and not inflow:
        return "Dağıtım (alıcı yok)", "warn"
    if whale_net > 0 and gap > 5:
        return "Balina topluyor", "good"
    if whale_net < 0 and gap < -5:
        return "Balina satıyor", "bad"
    if price_chg < -1 and not oi_up:
        return "Long tasfiye", "bad"
    return "Nötr", "info"


def _detail(price_chg, net24, oi24, top_long, gap, whale_net) -> str:
    """Etiketin uzun aciklamasi (tooltip)."""
    return (f"Fiyat {price_chg:+.2f}% · net taker {net24:+.1f}M$ · "
            f"OI {oi24:+.2f}% · büyük oyuncu long %{top_long:.1f} · "
            f"top-retail fark {gap:+.1f} · balina net {whale_net:+.2f}M$")


def _flow_score(net24: float, oi24: float, gap: float, whale_net: float) -> float:
    """0-100 para akisi skoru: net taker + OI + top/retail farki + balina izi."""
    s = 50.0
    s += max(min(net24 / 5.0, 20), -20)        # 100M$ net akis = +/-20 puan
    s += max(min(oi24 / 5.0, 15), -15)         # %75 OI degisimi = +/-15
    s += max(min(gap / 2.0, 10), -10)          # top-retail konum farki
    s += max(min(whale_net / 2.0, 5), -5)      # balina izi
    return round(max(0.0, min(100.0, s)), 1)


# ------------------------------------------------- 4) emir defteri duvarlari
def order_book(symbol: str, limit: int = 500, band_pct: float = 1.0) -> dict:
    """Emir defteri dengesizligi + en buyuk duvarlar.

    "Duvar" = mid fiyatin +/-band_pct'i icindeki en buyuk tek seviye emri.
    Buyuk limit emirler iceren piyasa yapicilari/balinalar fiyati tutmak
    (veya bloklamak) icin burada durur. Gercek veridir, tahmin degil.
    Sinir: emir iptal edilebilir (spoofing) — anlik bir fotoğraf, garanti degil.
    """
    d = _get(f"{FAPI}/fapi/v1/depth", symbol=symbol, limit=limit)
    if not d or not d.get("bids") or not d.get("asks"):
        return {"ob_imbalance_pct": 0.0, "ob_bid_musd": 0.0, "ob_ask_musd": 0.0,
                "wall_bid_musd": 0.0, "wall_bid_pct": 0.0,
                "wall_ask_musd": 0.0, "wall_ask_pct": 0.0, "ob_mid": 0.0}

    bids = [(float(p), float(q)) for p, q in d["bids"]]
    asks = [(float(p), float(q)) for p, q in d["asks"]]
    mid = (bids[0][0] + asks[0][0]) / 2
    lo, hi = mid * (1 - band_pct / 100), mid * (1 + band_pct / 100)

    band_b = [x for x in bids if x[0] >= lo]
    band_a = [x for x in asks if x[0] <= hi]
    bn = sum(p * q for p, q in band_b) or 1.0
    an = sum(p * q for p, q in band_a) or 1.0

    wb = max(band_b, key=lambda x: x[0] * x[1]) if band_b else (mid, 0.0)
    wa = max(band_a, key=lambda x: x[0] * x[1]) if band_a else (mid, 0.0)

    return {
        "ob_mid": round(mid, 8),
        "ob_bid_musd": round(bn / 1e6, 2),
        "ob_ask_musd": round(an / 1e6, 2),
        "ob_imbalance_pct": round((bn - an) / (bn + an) * 100, 2),
        "wall_bid_musd": round(wb[0] * wb[1] / 1e6, 2),
        "wall_bid_pct": round((wb[0] / mid - 1) * 100, 2),
        "wall_ask_musd": round(wa[0] * wa[1] / 1e6, 2),
        "wall_ask_pct": round((wa[0] / mid - 1) * 100, 2),
    }


def _series(url: str, key: str, **params) -> list:
    """Zaman serisi yardimcisi: [{t, v}] dondurur.

    Binance uclari zaman alanini farkli adlandiriyor: futures/data uclari
    'timestamp', fundingRate ucu 'fundingTime' kullanir. Ikisini de dene —
    yoksa seri sessizce bos doner.
    """
    d = _get(url, **params)
    if not d:
        return []
    out = []
    for x in d:
        ts = x.get("timestamp") or x.get("fundingTime") or x.get("time")
        try:
            out.append({"t": int(ts), "v": float(x[key])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def symbol_detail(symbol: str) -> dict:
    """Tek sembol detayi: akis + emir defteri + gecmis seriler."""
    base = smart_money(symbol)
    base.update(order_book(symbol))

    base["funding_hist"] = _series(
        f"{FAPI}/fapi/v1/fundingRate", "fundingRate", symbol=symbol, limit=90)
    base["oi_hist"] = _series(
        f"{FAPI}/futures/data/openInterestHist", "sumOpenInterestValue",
        symbol=symbol, period="1h", limit=48)
    base["top_hist"] = _series(
        f"{FAPI}/futures/data/topLongShortPositionRatio", "longAccount",
        symbol=symbol, period="1h", limit=48)
    base["retail_hist"] = _series(
        f"{FAPI}/futures/data/globalLongShortAccountRatio", "longAccount",
        symbol=symbol, period="1h", limit=48)
    # longAccount oran (0-1) -> yuzde
    for k in ("top_hist", "retail_hist"):
        for pt in base[k]:
            pt["v"] = round(pt["v"] * 100, 3)
    for pt in base["funding_hist"]:
        pt["v"] = round(pt["v"] * 100, 5)      # oran -> yuzde
    for pt in base["oi_hist"]:
        pt["v"] = round(pt["v"] / 1e6, 2)      # USDT -> milyon
    return base


SMART_SNAP = Path(__file__).parent / "data" / "smart_snapshots.csv"


def save_smart_snapshot(rows: list[dict]) -> None:
    """Sinyalleri gecmise yaz — ileride 'etiketler gercekten ise yaradi mi'
    olcebilmek icin. Sadece gercek taramalarda cagrilir."""
    if not rows:
        return
    import csv
    from datetime import datetime, timezone
    SMART_SNAP.parent.mkdir(exist_ok=True)
    cols = ["ts", "symbol", "price_chg", "net_1h_musd", "net_4h_musd", "net_24h_musd",
            "oi_chg_1h", "oi_chg_24h", "top_long_pct", "top_retail_gap",
            "whale_net_musd", "whale_share", "score", "label"]
    new = not SMART_SNAP.exists()
    with SMART_SNAP.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        ts = datetime.now(timezone.utc).isoformat()
        for r in rows:
            w.writerow({**{k: r.get(k) for k in cols}, "ts": ts})


UNIVERSE_CACHE = Path(__file__).parent / "data" / "universe_cache.json"
UNIVERSE_TTL = 600          # 10 dk — sembol listesi/hacim sik degismez


def universe(min_vol_musd: float = 100.0, top: int = 20, force: bool = False) -> list[str]:
    """Hacme gore en buyuk perpetual semboller.

    Agir uclar (exchangeInfo 1.2MB, ticker 300KB, premiumIndex 200KB) her
    taramada cekilirse WAF 403 veriyor. Bu yuzden diske onbelleklenir.
    """
    raw = None
    if not force and UNIVERSE_CACHE.exists():
        try:
            c = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))
            if time.time() - c.get("ts", 0) < UNIVERSE_TTL:
                raw = c.get("rows")
        except Exception:
            raw = None

    if raw is None:
        # DIKKAT: vadeli host'ta yollar /fapi/v1/... seklindedir.
        # /api/v1/... (spot yolu) bu host'ta 403 doner.
        prem = _get(f"{FAPI}/fapi/v1/premiumIndex") or []
        tick = _get(f"{FAPI}/fapi/v1/ticker/24hr") or []
        info = _get(f"{FAPI}/fapi/v1/exchangeInfo") or {}
        live = {s["symbol"] for s in info.get("symbols", [])
                if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"}
        have = {p["symbol"] for p in prem} if isinstance(prem, list) else set()

        rows = []
        for t in tick:
            sym = t["symbol"]
            if sym not in live or sym not in have:
                continue
            rows.append([sym, float(t.get("quoteVolume", 0) or 0) / 1e6])
        rows.sort(key=lambda x: -x[1])
        if rows:
            raw = rows
            UNIVERSE_CACHE.parent.mkdir(exist_ok=True)
            UNIVERSE_CACHE.write_text(
                json.dumps({"ts": time.time(), "rows": rows}), encoding="utf-8")
        else:
            # tamamen basarisizsa eski onbellegi kullan (bayat ama bos degil)
            try:
                raw = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))["rows"]
            except Exception:
                raw = []

    return [s for s, v in raw if v >= min_vol_musd][:top]


def scan_smart_money(min_vol_musd: float = 100.0, top: int = 12,
                     workers: int = 2, save: bool = False) -> dict:
    """Paralel ama olculu: WAF bloklamasin diye 2 thread + jitter.

    save=True sadece GERCEK taramalarda (cron/export) kullanilir; panel
    goruntulemesi gecmisi kirletmesin.
    """
    syms = universe(min_vol_musd=min_vol_musd, top=top)
    STATS.update({"requests": 0, "retries": 0, "failed": 0})

    def work(sym: str):
        time.sleep(random.uniform(0, 0.4))
        try:
            return smart_money(sym)
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(work, syms):
            if res:
                rows.append(res)

    rows.sort(key=lambda x: -x["score"])
    if save:
        save_smart_snapshot(rows)

    # piyasa genisligi: kac sembolde para girisi var, toplam net akis
    inflow = sum(1 for r in rows if r["net_24h_musd"] > 0)
    whale_buy = sum(1 for r in rows if r["whale_net_musd"] > 0)
    net_total = sum(r["net_24h_musd"] for r in rows)

    return {
        "rows": rows,
        "requested": len(syms),
        "ok": len(rows),
        "failed": len(syms) - len(rows),
        "stats": dict(STATS),
        "breadth": {
            "inflow_count": inflow,
            "whale_buy_count": whale_buy,
            "total": len(rows),
            "net_total_musd": round(net_total, 1),
            "inflow_pct": round(inflow / len(rows) * 100, 0) if rows else 0.0,
        },
        "fetched_at": pd.Timestamp.now("UTC").isoformat(),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Akilli para taramasi")
    ap.add_argument("--min-vol", type=float, default=200.0, help="min 24s hacim (milyon USDT)")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--save", action="store_true", help="sinyalleri gecmise yaz")
    a = ap.parse_args()

    res = scan_smart_money(min_vol_musd=a.min_vol, top=a.top, save=a.save)
    print(f"istenen {res['requested']} | basarili {res['ok']} | basarisiz {res['failed']} "
          f"| istek {res['stats']['requests']} | retry {res['stats']['retries']}")
    b = res["breadth"]
    print(f"piyasa genisligi: {b['inflow_count']}/{b['total']} para girisi | "
          f"toplam net {b['net_total_musd']:+.1f}M$ | balina alici {b['whale_buy_count']}\n")
    print(f"{'Sembol':<12}{'Fiyat%':>8}{'Net24h$M':>10}{'OI24h%':>8}"
          f"{'TopLong%':>10}{'Gap':>7}{'Balina$M':>10}  Skor   Etiket")
    print("-" * 104)
    for r in res["rows"]:
        print(f"{r['symbol']:<12}{r['price_chg']:>+8.2f}{r['net_24h_musd']:>+10.1f}"
              f"{r['oi_chg_24h']:>+8.2f}{r['top_long_pct']:>10.1f}{r['top_retail_gap']:>+7.1f}"
              f"{r['whale_net_musd']:>+10.2f}{r['score']:>6.1f}   {r['label']}")
