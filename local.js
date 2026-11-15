/* local.js — telefondan doğrudan Binance taraması (sunucu YOK).
 *
 * Binance public API CORS'a açık (access-control-allow-origin: *), bu yüzden
 * tarayıcı doğrudan veri çekebilir. Python tarafındaki huninin aynısı:
 *
 *   Aşama 0 (3 istek)      : premiumIndex + ticker24 + exchangeInfo -> tüm semboller
 *   Aşama 1 (1 istek/sym)  : 5m mumlar -> net taker akışı + balina izi
 *   Aşama 2 (3 istek/sym)  : OI + büyük oyuncu/retail oranı + funding geçmişi
 *
 * Kural, Python'daki `screener.decide` ile birebir aynı (3 koşul + ±%15 kovalama
 * koruması). Model olasılığı `reports/learn.json`'dan gömülmüştür.
 */
(function () {
  const FAPI = 'https://fapi.binance.com';
  const W = typeof window !== 'undefined' ? window : globalThis;

  // reports/learn.json'dan gömüldü — telefonda yeniden eğitim yok.
  const MODEL = {
    features: ['price_chg', 'net_24h_musd', 'whale_net_musd', 'oi_chg_24h', 'top_retail_gap', 'log_vol'],
    mu: null, sd: null, weights: null, auc: null, generalizes: false,
  };

  const MIN_WHALE_MUSDT = 1.0;
  const MIN_CONDITIONS = 3;
  const CHASE_LIMIT = 15;
  const RT_COST = 0.003;

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function bget(url, params, tries = 3) {
    const q = params ? '?' + new URLSearchParams(params).toString() : '';
    for (let i = 0; i < tries; i++) {
      try {
        const res = await fetch(url + q, { cache: 'no-store' });
        if (res.status === 429 || res.status === 418 || res.status === 403) {
          await sleep(1200 * (i + 1) + Math.random() * 600);
          continue;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return await res.json();
      } catch (e) {
        if (i === tries - 1) return null;
        await sleep(500 * (i + 1));
      }
    }
    return null;
  }

  const mean = a => a.reduce((x, y) => x + y, 0) / (a.length || 1);
  function quantile(arr, q) {
    if (!arr.length) return 0;
    const s = [...arr].sort((a, b) => a - b);
    const pos = (s.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
    return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (pos - lo);
  }

  /* ---------------------------------------------------------- Aşama 0 */
  async function bulk() {
    const [prem, tick, info] = await Promise.all([
      bget(`${FAPI}/fapi/v1/premiumIndex`),
      bget(`${FAPI}/fapi/v1/ticker/24hr`),
      bget(`${FAPI}/fapi/v1/exchangeInfo`),
    ]);
    if (!prem || !tick || !info) throw new Error('Binance erişilemedi (ağ/engel)');

    const intervals = {};
    for (const s of info.symbols || []) {
      if (s.contractType === 'PERPETUAL' && s.status === 'TRADING')
        intervals[s.symbol] = s.fundingIntervalHours || 8;
    }
    const tmap = Object.fromEntries(tick.map(t => [t.symbol, t]));

    const rows = [];
    for (const p of prem) {
      const iv = intervals[p.symbol];
      if (!iv) continue;
      const t = tmap[p.symbol];
      if (!t) continue;
      const mark = parseFloat(p.markPrice || 0), idx = parseFloat(p.indexPrice || 0);
      rows.push({
        symbol: p.symbol, iv,
        vol_musd: parseFloat(t.quoteVolume || 0) / 1e6,
        price_chg_bulk: parseFloat(t.priceChangePercent || 0),
        funding_now_pct: parseFloat(p.lastFundingRate || 0) * 100,
        basis_pct: idx ? (mark / idx - 1) * 100 : 0,
      });
    }
    return rows;
  }

  /* ---------------------------------------------------------- Aşama 1 */
  async function flow5m(sym) {
    const k = await bget(`${FAPI}/fapi/v1/klines`, { symbol: sym, interval: '5m', limit: 288 });
    if (!k || k.length < 60) return null;

    const bars = k.map(r => ({
      close: +r[4], qv: +r[7], trades: +r[8], tbq: +r[10],
    }));
    const win = n => {
      const w = bars.slice(-n);
      const vol = w.reduce((s, b) => s + b.qv, 0);
      const buy = w.reduce((s, b) => s + b.tbq, 0);
      return { net: (2 * buy - vol) / 1e6, vol: vol / 1e6, ratio: vol ? buy / vol : 0.5 };
    };
    const w1 = win(12), w4 = win(48), w24 = win(288);

    // balina: 5m dilimlerde ortalama işlem büyüklüğünün üst %2'si
    const avg = bars.map(b => (b.trades > 0 ? b.qv / b.trades : 0));
    const med = quantile(avg, 0.5) || 1;
    const thr = Math.max(quantile(avg, 0.98), 1.05 * med);
    let wVol = 0, wNet = 0, wCount = 0;
    bars.forEach((b, i) => {
      if (avg[i] >= thr && b.qv >= MIN_WHALE_MUSDT * 1e6) {
        wVol += b.qv; wNet += 2 * b.tbq - b.qv; wCount++;
      }
    });
    const totalVol = bars.reduce((s, b) => s + b.qv, 0) || 1;
    const step = Math.max(1, Math.floor(bars.length / 60));

    return {
      symbol: sym,
      price_chg: (bars[bars.length - 1].close / bars[0].close - 1) * 100,
      spark: bars.filter((_, i) => i % step === 0).map(b => b.close),
      net_1h_musd: w1.net, net_4h_musd: w4.net, net_24h_musd: w24.net,
      vol_24h_musd: w24.vol, buy_ratio_24h: w24.ratio * 100,
      whale_net_musd: wNet / 1e6, whale_share: (wVol / totalVol) * 100,
      whale_minutes: wCount,
    };
  }

  /* ---------------------------------------------------------- Aşama 2 */
  async function deep(sym) {
    const [oi, top, glob] = await Promise.all([
      bget(`${FAPI}/futures/data/openInterestHist`, { symbol: sym, period: '1h', limit: 25 }),
      bget(`${FAPI}/futures/data/topLongShortPositionRatio`, { symbol: sym, period: '1h', limit: 25 }),
      bget(`${FAPI}/futures/data/globalLongShortAccountRatio`, { symbol: sym, period: '1h', limit: 25 }),
    ]);
    const out = { oi_chg_1h: 0, oi_chg_24h: 0, oi_now_musd: 0,
                  top_long_pct: 50, retail_long_pct: 50, top_retail_gap: 0, deep: true };
    if (oi && oi.length > 1) {
      const v = oi.map(x => +x.sumOpenInterestValue);
      out.oi_now_musd = v[v.length - 1] / 1e6;
      out.oi_chg_1h = v[v.length - 1] / v[v.length - 2] * 100 - 100;
      out.oi_chg_24h = v[v.length - 1] / v[0] * 100 - 100;
    }
    if (top && top.length) out.top_long_pct = +top[top.length - 1].longAccount * 100;
    if (glob && glob.length) out.retail_long_pct = +glob[glob.length - 1].longAccount * 100;
    out.top_retail_gap = out.top_long_pct - out.retail_long_pct;
    return out;
  }

  async function fundingStats(sym) {
    const d = await bget(`${FAPI}/fapi/v1/fundingRate`, { symbol: sym, limit: 90 });
    if (!d || !d.length) return { mean: 0, p25: 0, pos: 0 };
    const rates = d.map(x => parseFloat(x.fundingRate));
    return { mean: mean(rates), p25: quantile(rates, 0.25),
             pos: rates.filter(r => r > 0).length / rates.length };
  }

  /* ------------------------------------------------------------ karar */
  function decide(row) {
    const chg = row.price_chg, net = row.net_24h_musd, oi = row.oi_chg_24h || 0;
    const gap = row.top_retail_gap || 0, whale = row.whale_net_musd;
    let al = 0, sat = 0; const ra = [], rs = [];
    if (net > 0) { al++; ra.push('para girişi'); } else { sat++; rs.push('para çıkışı'); }
    if (whale > 0) { al++; ra.push('balina alıcı'); } else { sat++; rs.push('balina satıcı'); }
    if (row.deep) {
      if (oi > 0 && chg > 0) { al++; ra.push('OI artıyor'); }
      if (oi > 0 && chg < 0) { sat++; rs.push('OI artıyor (short)'); }
      if (gap > 0) { al++; ra.push('büyük>küçük long'); } else { sat++; rs.push('büyük<küçük long'); }
    }
    if (chg > 0) { al++; ra.push('fiyat yukarı'); } else { sat++; rs.push('fiyat aşağı'); }

    const score = Math.max(0, Math.min(100,
      50 + net / 4 + whale / 2 + gap + oi / 2 + chg * 2));
    if (!row.deep) return { signal: '—', conditions: Math.max(al, sat), reasons: ['ön eleme'], score };
    if (Math.abs(chg) > CHASE_LIMIT)
      return { signal: 'BEKLE', conditions: Math.max(al, sat),
               reasons: [`aşırı uzamış (${chg >= 0 ? '+' : ''}${chg.toFixed(0)}%) — kovalama`], score };
    if (al >= MIN_CONDITIONS && al > sat) return { signal: 'AL', conditions: al, reasons: ra, score };
    if (sat >= MIN_CONDITIONS && sat > al) return { signal: 'SAT', conditions: sat, reasons: rs, score };
    return { signal: 'BEKLE', conditions: Math.max(al, sat), reasons: ['çelişkili sinyal'], score };
  }

  function prob(row) {
    if (!MODEL.generalizes || !MODEL.weights) return null;
    const src = { ...row, log_vol: Math.log1p(Math.max(0, row.vol_24h_musd || 0)) };
    let z = MODEL.weights[0];
    for (let i = 0; i < MODEL.features.length; i++) {
      const f = MODEL.features[i], v = src[f];
      if (v === undefined || v === null || !isFinite(v)) return null;
      z += MODEL.weights[i + 1] * ((v - MODEL.mu[i]) / MODEL.sd[i]);
    }
    return 1 / (1 + Math.exp(-z));
  }

  /* ------------------------------------------------------- ana tarama */
  async function run(opts = {}) {
    const minVol = opts.minVol ?? 20;
    const stage1N = opts.stage1N ?? 40;
    const stage2N = opts.stage2N ?? 12;
    const onProgress = opts.onProgress || (() => {});
    const t0 = Date.now();

    onProgress('sembol listesi');
    const all = await bulk();
    const uni = all.filter(r => r.vol_musd >= minVol).sort((a, b) => b.vol_musd - a.vol_musd);
    const bmap = Object.fromEntries(uni.map(r => [r.symbol, r]));

    // likit ilk N + funding anomalileri
    const anomal = [...uni].sort((a, b) =>
      Math.abs(b.funding_now_pct) - Math.abs(a.funding_now_pct)).slice(0, 10);
    const cand = [...new Set([...uni.slice(0, stage1N).map(r => r.symbol),
                              ...anomal.map(r => r.symbol)])];

    onProgress(`akış taranıyor (${cand.length})`);
    const flows = [];
    for (let i = 0; i < cand.length; i++) {
      const f = await flow5m(cand[i]);
      if (f) flows.push(f);
      if (i % 4 === 3) onProgress(`akış ${i + 1}/${cand.length}`);
      await sleep(60);
    }

    flows.sort((a, b) => (b.net_24h_musd + b.whale_net_musd * 2) -
                         (a.net_24h_musd + a.whale_net_musd * 2));
    const topFlows = flows.slice(0, stage2N);

    onProgress(`pozisyonlanma (${topFlows.length})`);
    const rows = [];
    for (const f of topFlows) {
      const [d, fund] = await Promise.all([deep(f.symbol), fundingStats(f.symbol)]);
      const base = bmap[f.symbol] || {};
      const perYear = 365 * 24 / (base.iv || 8);
      const p25ann = fund.p25 * perYear * 100, meanAnn = fund.mean * perYear * 100;
      const daily = p25ann > 0 ? p25ann : meanAnn;
      const row = {
        ...f, ...d,
        funding_now_pct: base.funding_now_pct ?? 0,
        basis_pct: base.basis_pct ?? 0,
        ann_funding_pct: +((base.funding_now_pct ?? 0) * perYear * 100).toFixed(1),
        vol_musd: base.vol_musd ?? f.vol_24h_musd,
        interval_h: base.iv || 8,
      };
      row.ann_p25_pct = +p25ann.toFixed(2);
      row.ann_30d_pct = +meanAnn.toFixed(2);
      row.pos_share_pct = +(fund.pos * 100).toFixed(0);
      row.breakeven_days = daily > 0 ? +(RT_COST * 100 / (daily / 365)).toFixed(1) : null;
      row.ok = row.ann_p25_pct > 0 && row.basis_pct < 0.3 && row.funding_now_pct >= 0;
      const dec = decide(row);
      row.signal = dec.signal; row.conditions = dec.conditions;
      row.reasons = dec.reasons; row.score = +dec.score.toFixed(1);
      row.prob = prob(row);
      rows.push(row);
      await sleep(60);
    }

    const order = { AL: 0, SAT: 1, BEKLE: 2, '—': 3 };
    rows.sort((a, b) => (order[a.signal] - order[b.signal]) || b.conditions - a.conditions);

    const inflow = rows.filter(r => r.net_24h_musd > 0).length;
    const whaleBuy = rows.filter(r => r.whale_net_musd > 0).length;

    return {
      scanned: uni.length, stage1: flows.length, stage2: rows.length,
      elapsed: +((Date.now() - t0) / 1000).toFixed(1),
      live: {
        count: rows.length, fetched_at: new Date().toISOString(),
        rows: rows.map(r => ({
          symbol: r.symbol, interval_h: r.interval_h || 8,
          funding_now_pct: r.funding_now_pct, ann_now_pct: +(r.funding_now_pct * 3 * 365).toFixed(2),
          ann_30d_pct: r.ann_30d_pct, ann_p25_pct: r.ann_p25_pct,
          pos_share_pct: r.pos_share_pct, basis_pct: r.basis_pct,
          vol_musd: r.vol_musd, breakeven_days: r.breakeven_days, ok: r.ok,
        })),
      },
      smart: {
        rows: rows.map(r => ({
          symbol: r.symbol, price_chg: +r.price_chg.toFixed(2), spark: r.spark,
          net_1h_musd: +r.net_1h_musd.toFixed(2), net_4h_musd: +r.net_4h_musd.toFixed(2),
          net_24h_musd: +r.net_24h_musd.toFixed(2), vol_24h_musd: +r.vol_24h_musd.toFixed(1),
          buy_ratio_24h: +r.buy_ratio_24h.toFixed(1),
          oi_chg_1h: +r.oi_chg_1h.toFixed(2), oi_chg_24h: +r.oi_chg_24h.toFixed(2),
          oi_now_musd: +r.oi_now_musd.toFixed(1),
          top_long_pct: +r.top_long_pct.toFixed(1), retail_long_pct: +r.retail_long_pct.toFixed(1),
          top_retail_gap: +r.top_retail_gap.toFixed(1),
          whale_net_musd: +r.whale_net_musd.toFixed(2), whale_share: +r.whale_share.toFixed(1),
          whale_minutes: r.whale_minutes,
          label: r.signal === 'AL' ? 'AL sinyali' : r.signal === 'SAT' ? 'SAT sinyali' : 'Bekle',
          tone: r.signal === 'AL' ? 'good' : r.signal === 'SAT' ? 'bad' : 'info',
          detail: `${r.reasons.join(', ')}`, score: r.score, prob: r.prob,
        })),
        requested: rows.length, ok: rows.length, failed: cand.length - flows.length,
        breadth: { inflow_count: inflow, whale_buy_count: whaleBuy, total: rows.length,
                   net_total_musd: +rows.reduce((s, r) => s + r.net_24h_musd, 0).toFixed(1),
                   inflow_pct: rows.length ? Math.round(inflow / rows.length * 100) : 0 },
        fetched_at: new Date().toISOString(), cached: false,
      },
      screen: {
        rows: rows.map(r => ({
          symbol: r.symbol, spark: r.spark, signal: r.signal, conditions: r.conditions,
          score: r.score, prob: r.prob, price_chg: +r.price_chg.toFixed(2),
          net_24h_musd: +r.net_24h_musd.toFixed(2), oi_chg_24h: +r.oi_chg_24h.toFixed(2),
          top_retail_gap: +r.top_retail_gap.toFixed(1),
          whale_net_musd: +r.whale_net_musd.toFixed(2),
          ann_funding_pct: r.ann_funding_pct, reasons: r.reasons,
        })),
        scanned_total: uni.length, stage1: flows.length, stage2: rows.length,
        elapsed_s: +((Date.now() - t0) / 1000).toFixed(1),
        stats: { requests: 0, retries: 0, failed: 0 },
        model: MODEL.generalizes ? { auc: MODEL.auc, generalizes: true } : null,
        alerts: [], fetched_at: new Date().toISOString(), min_vol: minVol, local: true,
      },
    };
  }

  W.LocalScan = { run, decide, prob, bulk, flow5m, deep, MODEL, loadModel };
  if (typeof module !== 'undefined') module.exports = W.LocalScan;

  /* Model ağırlıklarını reports/learn.json'dan yükle (varsa). */
  async function loadModel(url = 'reports/learn.json') {
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) return false;
      const m = await r.json();
      Object.assign(MODEL, {
        features: m.features, mu: m.mu, sd: m.sd, weights: m.weights,
        auc: m.auc, generalizes: !!m.generalizes,
      });
      return true;
    } catch (e) { return false; }
  }
})();
