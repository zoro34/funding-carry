# Formasyon Doğrulama Raporu — BTCUSDT 1h (2 yıl)

Amaç: pivot tabanlı formasyon dedektörleri gerçek bir edge üretiyor mu, yoksa
gürültü mü? Üç bağımsız testle ölçüldü.

## Tespit edilen sinyaller (BTCUSDT, order=5, 1110 tepe / 1094 dip)

| Formasyon | Sinyal | Yön |
|---|---|---|
| double_bottom (ikiz dip) | 91 | giriş |
| double_top (ikiz tepe) | 78 | çıkış |
| inverse_hs (ters OBO) | 25 | giriş |
| head_shoulders (OBO) | 14 | çıkış |
| ascending_triangle (yükselen üçgen) | 9 | giriş |
| bull_flag (boğa bayrağı) | 3 | giriş |

## Test 1: Rastgele giriş kontrolü — EN ÖNEMLİSİ

Yükselen üçgen: 11 işlem, **+4.44%**, kazanma %36.

Aynı işlem sayısı (11), aynı tutma süresi (96 bar), aynı SL %5/TP %10 ile
**1000 rastgele strateji** üretildi:

| | Getiri |
|---|---|
| Rastgele ortalama | -6.72% |
| Rastgele medyan | -6.83% |
| Rastgele en iyi %5 | +15.70% |
| Rastgele en iyi (tek) | +56.75% |
| **Formasyon sonucu** | **+4.44%** |

Formasyon, rastgele stratejilerin yalnızca **%83'ünden** iyi.
İstatistiksel anlamlılık eşiği %95. → **GÜRÜLTÜ.**

Rastgele girişlerin en iyisi +56.75% yaptı. Bizim "keşfimiz" +4.44%.
Yani bu sonuç, rastgele girip çıkmaktan ayırt edilemiyor.

## Test 2: Çapraz sembol (parametreler BTC'de seçildi, dokunulmadı)

| Sembol | Formasyon | Getiri | Al-Tut | Fark | İşlem | Kazanma |
|---|---|---|---|---|---|---|
| BTCUSDT | ascending_triangle | +4.44% | -27.86% | +32.29% | 11 | 36% |
| ETHUSDT | ascending_triangle | -8.85% | -40.24% | +31.39% | 5 | 20% |
| SOLUSDT | ascending_triangle | +57.42% | -66.09% | +123.51% | 11 | 73% |
| BNBUSDT | ascending_triangle | +7.30% | -3.36% | +10.65% | 2 | 100% |

Yükselen üçgen 4/4 sembolde al-tut'u geçti (ort. +49.5% fark).

**AMA:** toplam işlem sayısı 29. BNB'de 2 işlem (%100 kazanma = anlamsız).
SOL'deki +57.42% tüm ortalamayı tek başına taşıyor — en kötü performanslı
coinde (SOL -%66) çıkan bu sonuç, klasik aşırı uyum işaretidir.

## Test 3: Kritik yapısal sorun — "alpha" aslında "nakitte kalmak"

Bu 2 yıllık pencerede:

| Varlık | Getiri |
|---|---|
| BTC | -27.86% |
| ETH | -40.24% |
| SOL | -66.09% |
| BNB | -3.36% |
| **Nakit (hiç işlem yapmamak)** | **0.00%** |

Nakit, 4/4 varlıkta al-tut'u geçti. Formasyon stratejileri zamanın çoğunda
nakitte olduğu için otomatik olarak al-tut'u "yendi". Bu bir edge değil,
**ayı piyasasında pozisyon almamak**. Yani ölçtüğümüz "fazla getiri"
piyasa zamanlaması değil, sadece düşük maruz kalma.

## Karar

**Bu haliyle formasyon botu KANITLANMIŞ edge üretmiyor.**

- Rastgele girişle ayırt edilemiyor (%83 < %95 eşiği)
- Örneklem yetersiz (29 işlem, 30 altı)
- "Al-tut'u geçme" sonucu ayı piyasası artefaktı
- Tek olumlu sonuç (SOL) en kötü coinde çıkmış → aşırı uyum şüphesi

Bu, "formasyonlar tamamen işe yaramaz" demek değil. "Bu parametrelerle,
bu veride, bu maliyetle ölçülemedi" demek. Fark önemli: kanıt yokluğu,
yokluk kanıtı değil — ama para riske atmak için kanıt gerekir.

## Bu noktadan sonra dürüst seçenekler

1. **Bırak.** Öğrenme değeri yüksek, para riski sıfır. En az kaybettiren seçenek.
2. **Metodolojiyi düzelt ve devam et.** 5 yıl veri, 10+ sembol, walk-forward
   doğrulama, çoklu test düzeltmesi (Bonferroni). Doğru yol ama haftalar sürer
   ve sonunda yine "edge yok" çıkabilir.
3. **Farklı strateji ailesi dene.** Formasyon yerine: mean-reversion, funding-rate
   arbitrajı, volatilite rejim filtresi. Bunlar ölçülebilir ve bazıları
   gerçekten belgelenmiş edge'e sahip (özellikle funding/basis arbitrajı —
   piyasa nötr, düşük getiri ama yön tahmini gerektirmez).
4. **Parayı riske atmadan Testnet'te gözlemle.** Öğrenme amaçlı, kâr beklentisi yok.

## Tekrar üretmek için

    cd /root/binance-bot
    .venv/bin/python run_patterns.py     # formasyon bazlı backtest
    .venv/bin/python run_validate.py     # rastgele kontrol + çapraz sembol

---

Bu rapor yatırım tavsiyesi değildir. Backtest kârı canlı kâr garantisi vermez.
