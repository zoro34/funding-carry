# APK — telefona kurulum

Dosya: **`/root/binance-bot/FundingCarry-1.2.apk`** (3.6 MB)

- Paket: `com.suat.fundingcarry` · sürüm **1.2** (versionCode 3) · minSdk 22 (Android 5.1+) · targetSdk 34
- İmza: **debug** (v1 + v2). Play Store'a yüklenemez; elle kurulur.
- İzin: yalnızca `INTERNET`.
- İçinde: arayüz + `local.js` (tarayıcı taraması) + `learn.json` (model ağırlıkları)
  + `data.json` (yedek anlık görüntü).
  Uygulama **sunucusuz** çalışır: Binance public API'ye doğrudan bağlanır.

> Sürüm kontrolü: Telefonda Ayarlar → Uygulamalar → Funding Carry → "Sürüm 1.2"
> görüyorsan doğru APK'dasın. 1.2: bulut verisi + statik AL/SAT sekmesi.

## Nasıl kurulur

1. APK'yı telefona aktar (USB kablosu, Google Drive, Telegram "kendine gönder" vb.)
2. Telefonda dosyayı aç → "Bilinmeyen kaynaklara izin ver" uyarısını onayla
   (Ayarlar → Uygulamalar → Özel erişim → Bilinmeyen uygulama kurma)
3. Kur → "Funding Carry" ikonu ana ekranda
4. İlk açılışta internet ister; tarama ~25-40 saniye sürer

`adb` ile kurmak istersen:

```bash
adb install -r /root/binance-bot/FundingCarry-1.2.apk
```

## Uygulama ne yapıyor

Açıldığında ve her 5 dakikada bir kendisi tarar:

1. 3 istek: tüm perpetual semboller (hacim, funding, basis)
2. ~45 sembol: 5m mumlar → net taker akışı + balina izi
3. 12 sembol: açık pozisyon + büyük oyuncu/küçük hesap oranı + 30 günlük funding

Sonra üç sekme dolar: **GENEL** (funding fırsatları), **AKILLI PARA** (balina/akış),
**TARAYICI** (AL/SAT etiketleri + model olasılığı). Alarmlar cihazda saklanır
(`localStorage`, aynı sembol+sinyal 6 saatte bir kez).

## Gerçek sınırlar (dürüstçe)

- **Arka planda çalışmaz.** Uygulama kapalıyken tarama yok; Android arka plan
  işlerini öldürür. "Her zaman takip" istiyorsan ya uygulamayı açık tut ya da
  sunucu tarafını (GitHub Actions / VPS) kullan — o zaman bildirim de mümkün.
- **Bildirim göndermez.** Push için ayrı bir bildirim servisi gerekir (FCM).
  Alarmlar uygulama içinde listelenir.
- **iPhone'a kurulmaz.** Bu bir Android APK'sı. iOS için PWA yolu var (MOBILE.md).
- **Model ağırlıkları gömülü.** Sunucudaki haftalık yeniden eğitim APK'yı
  otomatik güncellemez; güncel model için APK'yı yeniden derlemek gerekir.
- **Tarama maliyeti:** ~60 istek / ~25 sn, ~5-15 MB veri. Mobil veride dikkat.
- **Yatırım tavsiyesi değildir.** AL/SAT etiketleri ölçülmüş edge göstermedi
  (AL +24s ortalama -0.22%, rastgele %59). Durum göstergesi olarak oku.

## Bulut verisi (7/24 güncel, pilsiz)

APK'nın altındaki **"Bulut verisi"** alanına GitHub Pages adresini yaz:

```
https://<kullanici>.github.io/<repo>/data.json
```

Kaydet'e bas. Artık uygulama açılışta **tarama yapmaz**; buluttaki son veriyi
1-2 saniyede gösterir. Bulut adresi boş bırakılırsa cihaz kendi tarar.
Bulut alınamazsa (internet yok) otomatik olarak cihaz taramasına düşer.

Yani: 7/24 takip **sunucuda**, gösterim **telefonda**. Pil ve kota maliyeti yok.

## Yeniden derleme

```bash
cd /root/binance-bot/android-app
bash build_apk.sh          # ~35 sn (önbellek sıcak)
```

Gereken zincir (bu makinede kurulu): JDK 21, Android SDK 34, Node 22+.
Arayüzde değişiklik yaptıktan sonra APK'yı yeniden derlemen gerekir —
`www/` klasörüne `dashboard.html`, `local.js`, `learn.json` kopyalanır.

## Yerel mod (APK'sız test)

Aynı kod tarayıcıda da çalışır: `http://<ip>:8080/?local=1`
Bu, APK'nın içindeki web katmanının birebir aynısıdır.
