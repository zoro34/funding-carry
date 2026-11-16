# AİLE AJANI (APK) — kurulum ve sınırlar

Bu uygulama **gizli değildir**. Kurulumda ne paylaştığını yazar, bildirim çubuğunda
sürekli görünür, istediğin an durdurulup kaldırılabilir.

## Ne paylaşır
- günlük ekran süresi (dakika) ve uygulama bazlı kullanım (ilk 20 uygulama)
- cihazın son konumu (GPS/şebeke), pil yüzdesi, gün içi açılma sayısı
- **bildirim önizlemeleri**: yalnızca bildirimde görünen başlık/metin (izinle)
- 15 dakikada bir `POST /api/family/report`

## Ne paylaşmaz
- sohbet geçmişi / mesaj içerikleri (WhatsApp, Instagram, Messenger — uçtan uca şifreli)
- fotoğraf, video, kamera, mikrofon
- sessiz ekran görüntüsü (Android izin vermez)

## Kurulum (çocuğun telefonunda, 4 dakika)
1. Panelde **AİLE → Çocuk Ekle** (isim + günlük limit) → çıkan **davet linkini** kopyala
2. Telefonda o linki aç (aynı WiFi'deyse `http://192.168.1.15:8080/aile-davet?t=...`)
3. Sayfadaki **1) Aile Ajanı APK indir** → kur ("bilinmeyen kaynağa izin ver")
4. Uygulamayı aç → sırayla izinler:
   - **Bildirim erişimi** (mesaj önizlemesi için)
   - **Kullanım erişimi** (ekran süresi için)
   - **Konum izni** (son konum)
   - **Pil optimizasyonundan muaf tut** (arka planda çalışsın diye)
5. Sayfadaki **2) ajanı eşle** düğmesi anahtarı otomatik doldurur; ya da elle gir
6. **Ajanı başlat** → bildirim çubuğunda "Aile paneliyle paylaşılıyor" görünür
7. Panelde AİLE sekmesinde çocuğun satırı dolar; **Son Bildirimler** tablosu gelir

## Önemli teknik sınır: ağ
Ajan, panelin adresine (`http://192.168.1.15:8080`) **aynı WiFi'den** ulaşabilir.
Çocuğun telefonu mobil verideyse rapor gelmez. Dışarıdan erişim için iki seçenek:
- Cloudflare Tunnel / ngrok ile paneli public adrese açmak (ücretsiz, token gerekir)
- Küçük bir VPS'e taşımak (panel + cron)

Adresi değiştirirsen ajanda **Sunucu adresi** alanını güncelle ve Kaydet'e bas.

## Test
- Ajanda **"Sunucuya test raporu gönder"** → panelde satır güncellenir
- Sunucu tarafı: `make test` → `smoke_family.py` 7/7
- APK doğrulama: `aapt2 dump badging` (izinler) + `apksigner verify` (imza)
