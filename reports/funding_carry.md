# Funding-Rate Arbitrajı vs Formasyon Botu — Gerçek Veri Karşılaştırması

2 yıllık gerçek Binance verisi, komisyon dahil. Aynı dönem, aynı sermaye ($1.000).

## Karşılaştırma

| Yaklaşım | Yıllık getiri | Yön tahmini | Ölçüm sonucu |
|---|---|---|---|
| Al-tut (BTC) | -15.19% | gerekmez | kayıp |
| En iyi formasyon botu (yükselen üçgen) | +2.22% | gerekir | kanıt yok (rastgelelerin %83'ü) |
| **Funding carry (BTCUSDT)** | **+4.98%** | **gerekmez** | 3/3 sembolde pozitif |
| Funding carry (ETHUSDT) | +4.46% | gerekmez | pozitif |
| Funding carry (SOLUSDT) | +0.90% | gerekmez | pozitif |

## Funding carry nedir

Delta-nötr pozisyon:

    Spot BTC al (1 BTC)  +  Vadeli BTC short aç (1 BTC)

Fiyat ne yaparsa yapsın iki bacak birbirini götürür → yön riski yok.
Kazanç: funding oranı pozitifken **long'lar short'lara öder**, yani short
bacak her 8 saatte bir ödeme alır. Bu bir yön tahmini değil, kira geliri.

## Ölçüm detayları (730 gün, 2.190 periyot / sembol)

| | BTCUSDT | ETHUSDT | SOLUSDT |
|---|---|---|---|
| Pozitif funding oranı | 83.1% | 79.3% | 60.0% |
| Ort. funding / 8s | +0.454 bps | +0.407 bps | +0.082 bps |
| Kümülatif (2 yıl) | +9.94% | +8.90% | +1.80% |
| **Yıllık** | **+4.98%** | **+4.46%** | **+0.90%** |
| En kötü tek periyot | -0.0152% | -0.0365% | -0.3028% |
| Negatif periyot sayısı | 371 | 453 | 876 |
| Filtreli varyant (trend pozitifken gir) | +5.38%/yıl | +5.05%/yıl | +3.84%/yıl |
| Tur maliyeti (giriş+çıkış, iki bacak) | 0.30% | 0.30% | 0.30% |
| Net (tek giriş/çıkış, 2 yıl tut) | +9.64% | +8.60% | +1.50% |

Tur maliyeti tek seferlik: sürekli pozisyonda tutulduğu için 2 yılda
yalnızca bir kez ödenir. Bu yüzden getirinin neredeyse tamamı net.

## Neden formasyon botundan daha güvenilir

1. **Yön tahmini yok** → tahmin hatası riski yok. Formasyon botunun edge'i
   "fiyat şu yöne gidecek" iddiasına dayanır; funding carry'nin iddiası
   "long'lar short'lara ödeme yapar" — mekanik, yapısal.
2. **3/3 sembolde pozitif** → tek coin şansı değil.
3. **2.190 gözlem** → 29 işlemden oluşan örneklemden kat kat güçlü.
4. **Düşük varyans** → en kötü tek periyot BTC'de -%0.015. Max drawdown
   formasyon botunda -%9.7, burada pratikte yok (delta-nötr).

## Gerçek riskler — bunlar küçümsenmemeli

- **Getiri düşük: ~%5/yıl.** $1.000 ile yılda $50. Anlamlı olması için
  ciddi sermaye gerekir. Bu bir "hobi botu" değil, sermaye tahsisi işi.
- **Borsa karşı taraf riski.** Paran Binance'te durur. Borsa batarsa
  (FTX örneği) delta-nötr olman kurtarmaz.
- **Funding negatife dönebilir.** BTC'de 371, SOL'de 876 periyot negatif.
  SOL'de en kötü periyot -%0.30 — birkaç gün üst üste gelirse getiriyi siler.
- **Kaldıraç = likidasyon riski.** %5 getiriyi %50'ye çıkarmak için 10x
  kaldıraç kullanırsan, artık "nötr" değilsin; ani hareket likide eder.
  Belgelenmiş birçok carry trader'ı tam burada hesap patlattı.
- **Operasyonel:** iki hesap (spot + vadeli), teminat yönetimi, otomatik
  transfer, API anahtarı güvenliği, 7/24 çalışan sunucu.

## Karar önerisi

"Binance'te otomatik para kazanan ajan" hedefi için ölçülmüş tek pozitif
sonuç funding carry. Ama gerçekçi beklenti: **yılda %5-8, düşük risk, yön
tahmini yok.** "Formasyon öğrenen bot" ise ölçümde edge göstermedi.

Sıralama önerisi:
1. Formasyon botunu para riske atmadan bırak (kanıt yok).
2. Funding carry'yi **izleme modunda** kur: gerçek para yok, sadece hangi
   coinde funding ne kadar, ne zaman girmek avantajlı — canlı ölç.
3. 1-2 ay izleme verisi toplandıktan ve operasyonel akış (API, teminat,
   alert) oturduktan sonra küçük sermaye ile başla.
4. Kaldıraç yok. %5 getiriyi 10x kaldıraçla kovalamak, tüm riskleri
     katlar ve nötr olmaktan çıkarır.

## Tekrar üretmek için

    cd /root/binance-bot
    .venv/bin/python funding_carry.py      # gerçek funding verisiyle ölçüm
    .venv/bin/python run_validate.py       # formasyon doğrulaması

---

Yatırım tavsiyesi değildir. Geçmiş getiri gelecek getiriyi garanti etmez.
