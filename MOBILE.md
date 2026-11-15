# Telefona uygulama olarak kurmak

Panel bir **PWA** (Progressive Web App). Telefonun ana ekranına uygulama gibi
kurulur: tam ekran açılır, ikonu olur, son veriyi önbellekte tutar.

## Önce net olalım: ne nerede çalışıyor

| İş | Nerede çalışır |
|---|---|
| Tarama / analiz / veri güncelleme | **7/24 açık bir makinede** (GitHub Actions veya VPS) |
| Paneli gösterme, önbellek, çevrimdışı | telefonda (PWA) |

Telefon **tarama yapmaz** — 565 sembolü taramak 100+ istek ve yüzlerce MB
gerektirir; pil ve kota için doğru olan, verinin bulutta üretilip telefonun
sadece göstermesi. Yani "telefonda uygulama" = görüntüleyici; "her zaman takip"
= bulut cron'u.

---

## 1) Hemen dene — LAN (bilgisayar açıkken, 30 saniye)

Panel zaten `0.0.0.0` üzerinde dinliyor.

1. Telefon **aynı Wi-Fi'da** olsun
2. Tarayıcıda aç: **http://192.168.1.15:8080**
3. Sinyal tablosu için: **http://192.168.1.15:8080/?tab=screen**

Bu adres çalışır ve tam işlevlidir. **Ama "Kur" butonu çıkmaz:** tarayıcı
uygulama kurulumuna yalnızca HTTPS (veya localhost) altında izin verir.
Düz http üzerinden sayfa açılır, uygulama olarak kurulamaz.

> Bilgisayar kapanınca bu adres de ölür — çünkü veri orada üretiliyor.

---

## 2) Gerçek kurulum — GitHub Pages (ücretsiz, bilgisayar kapalıyken de çalışır)

HTTPS verir, dolayısıyla "Kur" butonu çıkar; ayrıca veri GitHub'ın sunucusunda
6 saatte bir tazelenir.

**Adımlar:**

```bash
cd /root/binance-bot
git init && git add -A && git commit -m "funding carry monitor"
# GitHub'da boş bir repo aç, sonra:
git remote add origin https://github.com/<kullanici>/<repo>.git
git branch -M main && git push -u origin main
```

GitHub'da:
1. **Settings → Actions → General → Workflow permissions** → *Read and write*
2. **Settings → Pages** → Source: *Deploy from a branch* → `main` → `/docs`
3. **Actions** → "Funding Scan" → *Run workflow* (ilk çalışmayı elle başlat)

Panel adresi: `https://<kullanici>.github.io/<repo>/`

**Telefona kurma:**
- **Android / Chrome:** adresi aç → sağ üst ⋮ → **Uygulamayı yükle** (veya
  adres çubuğundaki "Kur" simgesi). Panelin kendi **Kur** butonu da çıkar.
- **iPhone / Safari:** adresi aç → Paylaş → **Ana Ekrana Ekle**.
  (iOS'ta kurulum butonu çıkmaz; Safari'nin kendi menüsü kullanılır.)

Kurulduktan sonra ikon ana ekranda durur, tam ekran açılır, internet yoksa son
bilinen veriyi "çevrimdışı" etiketiyle gösterir.

**Güncelleme sıklığı:** workflow varsayılan olarak **6 saatte** bir çalışır.
Saatlik istersen `.github/workflows/funding-scan.yml` içindeki cron'u
`"17 * * * *"` yap. Not: GitHub cron'u yoğunlukta 10-40 dk gecikebilir ve
private repo'da aylık 2.000 dakika sınırı vardır (saatlik ~2.900 dk eder →
public repo kullan ya da 6 saatte bırak).

---

## 3) Alternatif: kendi sunucun (en hızlı güncelleme)

Telefonun kurulumu aynı (HTTPS gerekir). Oracle Always Free VM ya da ~$5 VPS
üzerinde `dashboard.py`'yi systemd ile çalıştır, önüne Caddy/nginx ile HTTPS
koy. Avantaj: tarama istediğin sıklıkta, canlı API; dezavantaj: sunucu yönetimi.
Detay: `DEPLOY.md`.

---

## Uygulamanın sınırları (dürüstçe)

- **Bildirim göndermez.** PWA push için ayrı bir push servisi (VAPID + backend)
  gerekir; şu an kurulu değil. Alarmlar panelde ve `data/alerts.csv`'de.
  Telefona bildirim istiyorsan en pratik yol **Telegram** (README'de kurulum var).
- **Arka planda çalışmaz.** Kapalı uygulama veri çekmez; veri bulutta üretilir.
- **Veri gecikmesi = cron sıklığı.** 6 saatse paneldeki veri 6 saate kadar eski
  olabilir. Panelde "son tarama" zamanı her zaman yazar.
- **Yatırım tavsiyesi değildir.** AL/SAT etiketleri ölçülmüş edge göstermedi
  (rapor: `reports/rule_backtest.md`); durum göstergesi olarak okunmalı.

---

## Teknik dosyalar

| Dosya | Görev |
|---|---|
| `manifest.json` | uygulama adı, ikon, tam ekran, kısayol |
| `sw.js` | service worker: kabuk cache-first, veri network-first |
| `icons/*.png` | 192 / 512 / maskable ikonlar |
| `dashboard.html` | `?tab=screen` kısayolu, Kur butonu, çevrimdışı etiketi |

Service worker yalnızca HTTPS/localhost'ta devreye girer — bu yüzden LAN
testinde sayfa çalışır ama kurulum/önbellek devre dışı kalır.
