# Binance Backtest Altyapısı

Gerçek Binance verisi + komisyon/slippage dahil backtest motoru.
Henüz canlı işlem YOK — bu adım sadece ölçüm altyapısı.

## Kurulum durumu

    /root/binance-bot/
    ├── .venv/                  Python 3.13 venv (pandas, numpy, requests, matplotlib)
    ├── download_data.py        Binance kline indirici (sayfalı)
    ├── backtest.py             Backtest motoru (Config, Backtester, Result)
    ├── run_baseline.py         Doğrulama + temel stratejiler + rapor
    ├── data/
    │   └── BTCUSDT_1h.csv      17.520 mum, 2024-11-15 → 2026-11-15
    └── reports/
        ├── baseline.md         Tablo + ders
        └── equity_curves.png   Sermaye eğrileri

## Kullanım

    cd /root/binance-bot

    # veri indir / güncelle
    .venv/bin/python download_data.py --symbol BTCUSDT --interval 1h --days 730
    .venv/bin/python download_data.py --symbol ETHUSDT --interval 15m --days 365

    # doğrulama + baseline
    .venv/bin/python run_baseline.py

## Motorun önemli özellikleri

- **Look-ahead bias yok**: sinyal bar i kapanışında üretilir, emir bar i+1
  açılışında dolar (`backtest.py` içinde `tgt` kaydırması).
- **Maliyet gerçekçi**: her yönde komisyon (%0.1) + slippage (%0.05).
- **Long-only spot**: spot hesapta açığa satış yok.
- **Intrabar stop/take-profit**: `Config(stop_loss_pct=0.05, take_profit_pct=0.10)`.
- **Doğrulama testi**: "hep long" stratejisi, ham fiyat değişiminden yalnızca
  2x(komisyon+slippage) kadar sapmalı. `run_baseline.py` her çalıştığında
  bunu kontrol eder ve GECTI/KALDI basar.

## Strateji yazma

`backtest.py` içindeki `Backtester.run(target)` bir 0/1 serisi bekler:

    import pandas as pd
    from backtest import Backtester, Config

    df = pd.read_csv("data/BTCUSDT_1h.csv", parse_dates=["open_time", "close_time"])
    sig = (df["close"] > df["close"].rolling(50).mean()).astype(int)

    r = Backtester(df, Config(initial_capital=1000.0)).run(sig, "benim_stratejim")
    print(r.summary())

## İlk ölçüm sonucu (2 yıl, BTC -%27.9)

| Strateji | Getiri | İşlem | MaxDD |
|---|---|---|---|
| Al-Tut | -28.07% | 1 | -53.7% |
| SMA 50/200 | -29.05% | 63 | -38.1% |
| SMA 20/50 | -60.47% | 198 | -63.8% |
| SMA 10/30 | -72.02% | 341 | -74.4% |

**Ders**: işlem sayısı arttıkça kayıp büyüdü. Komisyon maliyeti işlem
sayısıyla doğrusal. Formasyon stratejisi yazarken ilk kural "az işlem".

## Sıradaki adımlar

1. [x] Formasyon dedektörleri (pivot tabanlı: üçgen, çift tepe/dip, OBO, bayrak)
2. [x] Formasyon stratejisini aynı motorda backtest et
3. [x] Doğrulama: rastgele giriş kontrolü + çapraz sembol → **edge bulunamadı**
4. [ ] (opsiyonel) Walk-forward + 5 yıl + 10 sembol ile metodoloji düzeltmesi
5. [ ] (opsiyonel) Testnet'e bağlama — sadece öğrenme amaçlı

## Doğrulama sonucu (kısa)

`reports/patterns_validation.md` — en iyi görünen strateji (yükselen üçgen,
+%4.44 vs al-tut -%27.86) 1000 rastgele stratejinin sadece %83'ünden iyi.
%95 eşiği geçilemedi. "Al-tut'u geçme" sonucu, ayı piyasasında nakitte
kalmanın artefaktı: nakit 4/4 coinde al-tut'u yendi. **Kanıtlanmış edge yok.**

## Alternatif: funding carry (ölçüldü, pozitif)

`funding_carry.py` + `reports/funding_carry.md` — yön tahmini gerektirmeyen
delta-nötr pozisyon (spot long + vadeli short). 2 yıllık gerçek Binance
funding verisiyle:

| | BTC | ETH | SOL |
|---|---|---|---|
| Yıllık getiri | +4.98% | +4.46% | +0.90% |
| Pozitif periyot oranı | 83.1% | 79.3% | 60.0% |

3/3 sembolde pozitif, 2.190 gözlem, düşük varyans. Getiri düşük (~%5/yıl),
borsa karşı taraf riski var, kaldıraç kullanılırsa nötr olmaktan çıkar.

## İzleme aracı (canlı, API anahtarı gerekmez)

`funding_monitor.py` — public Binance verisiyle fırsat tarar:

    .venv/bin/python funding_monitor.py scan --min-vol 100 --top 10
    .venv/bin/python funding_monitor.py watch --interval 600 --min-ann 8

Kolonlar:
- `Ort.yil%` — son 30 günün ortalama funding'i, yıllığa çevrilmiş
- `Temkinli%` — 25. persentil (kötü periyotlar dahil, sürdürülebilir getiri)
- `Pozitif%` — son 30 günde funding'in pozitif olduğu periyot oranı
- `Basis%` — pozitifse perp primli: primli girmek getiriyi yer
- `Gun` — tur maliyetini (%0.30) kapatmak için gereken gün (temkinli)

Giriş kuralı: `Temkinli% > 0` **ve** `anlık funding >= 0` **ve** `basis < 0.3%`.
Anlık funding negatifse şu an ödüyorsun — geçmiş iyi olsa da girme.

Her çalışma `data/funding_snapshots.csv`'ye satır ekler → zamanla kendi
gerçek verin oluşur.

### Otomatik izleme
Cron job `funding-carry-izleme` 6 saatte bir çalışıyor
(`~/.hermes/scripts/funding_scan.sh`). Çıktı yerelde kaydedilir.
Durdurmak için: `hermes cron list` → job id → kaldır.

## Görsel panel

```bash
.venv/bin/python dashboard.py            # http://127.0.0.1:8080
.venv/bin/python dashboard.py --host 0.0.0.0 --port 8080   # ağdan erişim
```

Dört sekme (panel **KİRA GELİRİ** ile açılır):

**KİRA GELİRİ (CARRY)** — gerçek pozisyon takibi (`carry.py`)
- Açtığın spot long + vadeli short pozisyonları kaydedersin (sembol, notional, açılış tarihi)
- Panel Binance'in **gerçek funding geçmişinden** o güne kadar biriken kira gelirini toplar
- Delta-nötr kontrolü: spot bacak yoksa "DELTA-NÖTR DEĞİL" uyarısı; basis primi uyarısı
- Bacak P&L ayrı ayrı gösterilir (spot -X, short +X → net ≈ kira)
- Bugünkü adaylar: giriş kuralını geçen semboller, temkinli yıllığa göre
- API: `GET /api/carry` · `POST /api/carry/add` · `POST /api/carry/remove`
  (yazma yalnızca localhost; `CARRY_ALLOW_REMOTE=1` ile açılır)

**GENEL** — funding carry tarafı
- KPI kartları, otomatik çıkarımlar, canlı fırsat tablosu
- Skor grafiği + 30 günlük funding zaman serisi

**AKILLI PARA / BALİNA** — vadeli piyasa akışı (`flow.py`)
- Piyasa genişliği şeridi: kaç sembolde para girişi, toplam net akış
- Tablo: sparkline, 24s fiyat, net taker akışı, OI değişimi,
  büyük oyuncu long %, top−retail fark, balina net, skor, yorum
- Filtre çipleri: tümü / para giren / balina alıcı / sıkışma
- **Satıra tıkla → sembol detayı**: emir defteri dengesi + duvarlar,
  48s OI grafiği, büyük oyuncu vs küçük hesap oranı, 30 günlük funding

### Akıllı para katmanı neyi ölçer (ve neyi ölçmez)

Gerçek veri, hepsi Binance vadeli public API:
- **Net taker akışı** — agresif alıcı hacmi − satıcı hacmi (fiyattan bağımsız)
- **OI değişimi** — yeni pozisyon mu açılıyor, kapanıyor mu
- **Büyük oyuncu pozisyonu** — Binance "top trader" pozisyon ağırlıklı long oranı
  vs küçük hesap oranı (fark en değerli sinyal)
- **Balina işlem izi** — 5m dilimlerde işlem büyüklüğünün en üst %2'lik dilimi
- **Emir defteri duvarları** — mid ±%1 içindeki en büyük tek seviye emir

**Ölçmez:** kim aldı/sattı. Gerçek cüzdan takibi (Arkham/Nansen) on-chain veri
ister ve ücretlidir. Bunlar davranış vekilleridir. Emir defteri duvarları
iptal edilebilir (spoofing) — anlık fotoğraf, garanti değil.

Mimari: `analytics.py` (funding istatistiği) + `flow.py` (akış/balina) →
`dashboard.py` (Flask API) → `dashboard.html` (tek dosya, CDN yok).

Uçlar: `/api/all` (14 ms, yerel), `/api/live` (~5 sn), `/api/smart` (~10 sn,
120 sn önbellek), `/api/symbol/<sym>` (tıklamada, önbelleksiz).

**Yazma kuralı:** panel görüntülemesi hiçbir CSV'ye yazmaz. Sadece gerçek
taramalar yazar (cron, `scan`, `export_static.py`). Aksi halde sayfa yenilemek
"tarama sayısını" şişirir.

**Sinyal geçmişi:** `data/smart_snapshots.csv` her gerçek taramada etiketleri
kaydeder → 1-2 ay sonra "bu etiketler gerçekten işe yaradı mı" ölçülebilir.
Doğrulanmamış bir sinyali kesin gerçek gibi sunmamak için bu kayıt şart.

## Bilgisayar kapalıyken çalıştırma

Kapalı bir makinede program çalışmaz — kod 7/24 açık bir yerde olmalı.
İki ücretsiz yol var, detay: **DEPLOY.md**

1. **GitHub Actions** (önerilen, 0 TL): `.github/workflows/funding-scan.yml`
   her 6 saatte bulutta tarar, `data.json` commit eder, panel GitHub Pages'te
   yayınlanır → telefondan erişilir.
2. **Oracle Cloud Always Free VM** (0 TL): panel canlı olarak çalışır.

Panel sunucu bulamazsa otomatik `data.json`'a düşer (statik mod), bu yüzden
aynı `dashboard.html` hem yerelde hem Pages'te çalışır.

## Tüm sembolleri tarama — AL / SAT / BEKLE

```bash
.venv/bin/python screener.py                    # tarama, tablo
.venv/bin/python screener.py --alert            # + alarmları alerts.csv'ye yaz
```

**Huni (neden):** 565 perpetual sembol var. Her birine 4-5 istek atmak 2.500+
istek eder ve Binance bloklar. Bu yüzden üç aşama:

| Aşama | Kapsam | İstek | Ne ölçülür |
|---|---|---|---|
| 0 | 565 sembol | 3 | hacim, funding, basis, 24s fiyat |
| 1 | likit ilk 60 + funding anomalileri | 1/sembol | 5m net taker akışı + balina izi |
| 2 | akışa göre ilk 15 | 2/sembol | açık pozisyon + büyük oyuncu/retail |

Ölçülen: 99 sembol (≥20M$), 66 akış, 15 derin, **114 istek, 0 retry, ~24 sn**.

**Kural (AL / SAT / BEKLE):** yön + para akışı + balina izi + büyük oyuncu
konumu aynı yönde **en az 3 koşul** sağlarsa sinyal üretilir. Ekran "kaç koşul"
ve gerekçeleri gösterir.

**Kovalama koruması:** 24 saatte ±%15'ten fazla uzamış coinler kural gereği
BEKLE'ye düşer. +%46 yükselmiş bir coinde "para girişi var" doğru olabilir ama
giriş yapmak tuzağa dönüşür.

**Dürüst sınır:** bu sinyallerin kazanma oranı **ölçülmedi**. Mekanik bir kural
çıktısıdır, tavsiye değildir. Her sinyal `data/alerts.csv`'ye yazılır →
1-2 ay sonra "gerçekten işe yaradı mı" ölçülebilir.

### Alarmlar

- `alerts.csv`: zaman, sembol, sinyal, koşul sayısı, fiyat, net akış, gap, gerekçe
- Aynı sembol+sinyal 6 saat içinde tekrar yazılmaz (alarm yorgunluğu önlemi)
- Panelde **TARAYICI** sekmesindeki "Alarm Geçmişi" listesinde görünür
- Cron: `alts-screener-alarm` her 30 dakikada bir çalışır

**Önemli:** bu ortamda mesajlaşma kanalı (Telegram vb.) yapılandırılmadığı için
alarmlar telefonuna **bildirim olarak gitmez** — panelde ve CSV'de durur.
Telegram bağlanırsa aynı cron çıktısı oraya da düşebilir.

### Kuralın ölçülmüş performansı — sonuç: edge YOK

`rule_backtest.py` kuralı geçmiş ~20 günle (10 sembol, 1h, 40 istek) test eder.
Kural her saat, **sadece o ana kadarki veriyle** çalıştırılır; sonra +4s/+24s
fiyat hareketine bakılır. Kontrol grubu: aynı sembollerde rastgele girişler.

Ölçülen (1.338 AL, 2.711 SAT, 4.520 rastgele kontrol):

| Sinyal | Ufuk | Ortalama | Medyan | Yukarı oran | Rastgele percentile |
|---|---|---|---|---|---|
| AL | +24h | **-0.21%** | -1.13% | 36.9% | %59 → gürültü |
| SAT | +24h | -0.56% | -0.75% | 36.6% | %54 → gürültü |

Yani **AL sinyali para kazandırmıyor**; ortalama ileri getiri negatif ve
rastgele girişten ayırt edilemiyor. Panel bunu TARAYICI sekmesinde kırmızı
uyarı olarak gösterir (`/api/rulecheck`).

**Sonuç:** AL/SAT etiketleri *durum göstergesi* olarak okunmalı, alım-satım
talimatı olarak değil. Rapor: `reports/rule_backtest.md`.

### Gerçek alarmların takibi

```bash
.venv/bin/python evaluate_alerts.py     # alerts.csv + ileri fiyatlar
```

Her alarm için +1s/+4s/+24s ileri getiriyi hesaplar, aynı sembolün ortalama
ileri getirisiyle (kontrol) karşılaştırır. En az 20 olgun sinyal olana kadar
"yorum yok" der — küçük örneklemden sonuç çıkarmaz.
Cron `alarm-degerlendirme` her gün 09:00'da çalışır → `reports/alerts_eval.md`.

### Telegram bildirimi (kurulum gerekli)

Bu makinede Telegram **yapılandırılmadı** (`.env`'deki satırlar yorum halinde).
Kurulum:

1. Telegram'da **@BotFather** → `/newbot` → token al
2. Token'ı kabuğa yazmadan kaydet:
   ```bash
   read -rsp "Bot token: " T && echo "TELEGRAM_BOT_TOKEN=$T" >> ~/.hermes/.env && unset T
   ```
3. Bota Telegram'dan **bir mesaj at** (bot ancak sana yazabilmesi için önce
   senin yazman gerekir)
4. Chat ID'yi bul ve kaydet:
   ```bash
   source ~/.hermes/.env
   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
   # çıkan id'yi:  echo "TELEGRAM_HOME_CHANNEL=<id>" >> ~/.hermes/.env
   ```
5. Test: `echo "test" | hermes send --to telegram`

Token girildiği anda `screener_scan.sh` her AL/SAT sinyalini Telegram'a da atar
(30 dakikada bir). Token yokken sessizce atlanır — cron bozulmaz.

## Öğrenme katmanı (walk-forward model)

`learn.py` — numpy ile yazılmış lojistik regresyon, sklearn gerekmez.

**Ne öğrenir:** 6 özellikten (24s fiyat değişimi, net taker akışı, balina izi,
OI değişimi, büyük oyuncu−retail farkı, log 24s hacim) +24s **yukarı olasılığı**.

**Nasıl öğrenir:** walk-forward. Her tahmin için ağırlıklar yalnızca o andan
önceki veriyle eğitilir, sonra görülmemiş blokta ölçülür. Tek seferlik
train/test ayrımından farkı: rejim değişimini de görür.

Ölçülen (4.520 örnek, 10 sembol, ~20 gün, 109 kat):

| Ölçüm | Değer |
|---|---|
| AUC (0.5 = yazı-tura) | **0.619** |
| Yüksek güven %20 ortalama +24s getiri | **+0.28%** |
| Düşük güven %20 ortalama +24s getiri | **-2.55%** |
| Tüm örneklem | -0.78% |

Yani model **sıralama** yapabiliyor: en güvenli %20 ile en güvensiz %20 arasında
~2.8 puan fark var. Öğrenilen ağırlıklar: OI +0.28 (pozitif), 24s fiyat -0.32
(negatif → düşen piyasada ortalama-dönüş), hacim -0.18, akış -0.09.

**Dürüst sınırlar:**
- Tek rejim: piyasa bu pencerede düşüyordu; model "düşüşte alım yapma" öğrenmiş
  olabilir. Başka rejimde doğrulanmadı.
- Etiketler 24s ileriye baktığı için örnekler üst üste biniyor → etkin örneklem
  4.360'dan çok daha küçük. AUC olduğundan iyi görünüyor olabilir.
- İşlem maliyeti düşülmedi (+%0.28 ≈ tur maliyeti %0.30 sınırında).
- "Model isabet %45 < baseline %66" yanıltıcı: örneklemin %64'ü düşmüş, doğruluk
  yanlış metrik. Doğru metrik AUC ve güven kovaları.

**Otomatik devre dışı:** AUC < 0.55'e düşerse `score_features` None döner,
panele tahmin basılmaz. Cron `model-yeniden-egitim` her pazartesi 08:00'de
modeli taze veriyle yeniden eğitir ve AUC'yi yeniler.

Panelde `MODEL P↑` kolonu kuraldan **bağımsız ikinci görüş** verir; ikisi
çelişebilir (ör. kural AL, model %28).

## Telefonda uygulama (PWA)

Panel kurulabilir bir PWA: `manifest.json` + `sw.js` + ikonlar. Telefonun ana
ekranına eklenir, tam ekran açılır, son veriyi önbellekte tutar.
Kurulum ve sınırlar: **MOBILE.md**

- LAN testi (bilgisayar açıkken): `http://<yerel-ip>:8080/?tab=screen`
- Kalıcı kurulum (bilgisayar kapalıyken): GitHub Pages (HTTPS) — bkz. DEPLOY.md
- Telefon tarama yapmaz; veriyi buluttaki cron üretir, telefon gösterir
- Push bildirimi yok (Telegram kullan); arka planda çalışmaz

## Uyarı

Bu kod yatırım tavsiyesi değildir. Backtest kârı canlı kâr garantisi vermez;
geçmiş performans gelecek performansı göstermez.
