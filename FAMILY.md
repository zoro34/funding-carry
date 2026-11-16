# AİLE PANELİ — açık rızalı ebeveyn kontrolü

## Ne yapar
- Çoklu çocuk kaydı; her biri için **davet linki** (`/aile-davet?t=<token>`)
- Çocuğun cihazındaki **görünür aile ajanı** günde birkaç kez rapor gönderir:
  ekran süresi, uygulama bazlı kullanım, açılma sayısı, pil, son konum
- Günlük limit / kısıtlı uygulama / uygulama limiti kuralları
- İhlalde **Telegram** bildirimi (ekran süresi aşımı, kısıtlı uygulama)
- Panel: `http://127.0.0.1:8080` → **AİLE** sekmesi

## Ne yapmaz (teknik ve yasal sınır)
- **WhatsApp / mesaj içeriği okumaz.** Uçtan uca şifreli; hiçbir meşru uygulama okuyamaz.
  "Okuyorum" diyen ya yalan söylüyordur ya cihaza zararlı yazılım kuruyordur.
- Kamera, mikrofon, galeri erişimi yok. Gizli çalışma yok — ajan cihazda görünür.
- Aile verisi **public `data.json`'a girmez** (isim/konum sızmaz). Telefon uygulamasında
  AİLE sekmesi "sunucu gerektirir" der; bu bilinçli.

## Ajan sözleşmesi (APK yazılınca kullanılacak)
```
POST /api/family/report
{"token":"<davet>","device":"Redmi Note 12","screen_minutes":214,
 "apps":[{"pkg":"com.whatsapp","minutes":87}],"unlocks":63,"battery":41,
 "location":{"lat":41.0082,"lon":28.9784,"accuracy_m":14}}
```
Yanıt: `{"ok":true,"child":"...","alerts":["screen_limit"]}`

Android tarafında gereken izinler: `PACKAGE_USAGE_STATS` (kullanım), `ACCESS_FINE_LOCATION`,
`POST_NOTIFICATIONS`, `FOREGROUND_SERVICE`; **Device Admin** yalnızca "cihazı kilitle"
özelliği kullanılacaksa. Hiçbiri mesaj içeriğine erişmez.

## Bugün ücretsiz ve resmi alternatif: Google Family Link
Cihaz testi gerektirmeden çalışır, WhatsApp içeriğine dokunmaz:
1. Çocuğun telefonuna **Google Family Link** (Çocuk) kur, Google hesabıyla giriş yap
2. Kendi telefonuna **Family Link** (Ebeveyn) kur
3. Ebeveyn uygulamasında **+ → Çocuk ekle** → çocuğun hesabını seç → kod ile eşleştir
4. Aç: **Ekran süresi**, **Uygulama kullanımı**, **Konum**, **Uygulama engelleme**,
   **Yatma vakti**

## Durum
- Sunucu + panel + Telegram + kurallar: **hazır** (`make test` → smoke_family 6/6)
- Ajan APK: **henüz yok** — yazılması cihaz/izin testi ister (1-2 gün)
- Şu an panelde `DEMO` kaydı var (sahte raporla doğrulama için). Gerçek çocuğu ekleyince `×` ile sil.
