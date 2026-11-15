# "Bilgisayar kapalıyken çalışsın" — gerçek cevap

## Önce dürüst kısım

**Kapalı bir bilgisayarda hiçbir program çalışamaz.** Fiziksel sınır.
"Bilgisayar kapalıyken çalışan program" diye bir şey yok — çalışan şey
**başka bir bilgisayardır**. Yani kodun senin makinende değil, 7/24 açık
bir yerde çalışması gerekir.

Üç gerçek seçenek var. Aşağıdaki ilk ikisi **0 TL**.

---

## Seçenek 1 — GitHub Actions (ücretsiz, kurulum 10 dk) ✅ önerilen

Kod her 6 saatte GitHub'ın sunucularında çalışır, sonucu repoya commit eder.
Panel de GitHub Pages'te yayınlanır → telefondan bile açabilirsin.

**Maliyet:** 0 TL. Public repo'da dakika sınırı yok. Private repo'da ayda
2.000 ücretsiz dakika var; bu iş çalışma başına ~1 dakika.

**Adımlar:**

```bash
cd /root/binance-bot

# 1) git deposu hazırla (GitHub'da bos bir repo aç, URL'yi asagiya yaz)
git init
git add -A
git commit -m "funding carry monitor"

# 2) GitHub'da repo olusturduktan sonra:
git remote add origin https://github.com/<kullanici>/<repo>.git
git branch -M main
git push -u origin main
```

Sonra GitHub'da:
1. **Settings → Actions → General → Workflow permissions** →
   "Read and write permissions" seç (commit atabilmesi için).
2. **Settings → Pages** → Source: "Deploy from a branch",
   Branch: `main`, Folder: `/docs` → Save.
3. **Actions** sekmesi → "Funding Scan" → **Run workflow** (ilk çalışmayı elle
   tetikle). Bundan sonra 6 saatte bir kendi çalışır.

Panel adresi: `https://<kullanici>.github.io/<repo>/`

**Nasıl çalışıyor:** `.github/workflows/funding-scan.yml`
- `schedule: cron "17 */6 * * *"` → 6 saatte bir
- `export_static.py` → Binance'i tarar, `data.json` üretir
- `dashboard.html` → `docs/index.html` olarak kopyalanır
- değişiklik varsa commit + push → Pages otomatik yenilenir

**Sınır:** GitHub cron'u yoğunlukta 10-40 dakika gecikebilir, nadiren bir
turu atlar. Funding 8 saatlik periyot olduğu için bu sorun değil.

---

## Seçenek 2 — Ücretsiz bulut VM (Oracle Cloud Always Free)

**Maliyet:** 0 TL (kredi kartı doğrulaması ister, ücret çekmez).
ARM VM, 4 çekirdek / 24 GB RAM — bu iş için fazlasıyla yeterli.

```bash
# VM'e bağlandıktan sonra
sudo apt update && sudo apt install -y python3-venv git
git clone <senin-repo> && cd binance-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# servis olarak calistir
sudo tee /etc/systemd/system/funding-dashboard.service > /dev/null <<'EOF'
[Unit]
Description=Funding Carry Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/binance-bot
ExecStart=/home/ubuntu/binance-bot/.venv/bin/python dashboard.py --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now funding-dashboard
```

Ek olarak 6 saatlik tarama için crontab:
```
17 */6 * * * cd /home/ubuntu/binance-bot && .venv/bin/python funding_monitor.py scan --min-vol 100 --top 20 >> /var/log/funding.log 2>&1
```

**Artı:** Panel canlı çalışır (gerçek zamanlı tarama, canlı API).
**Eksi:** VM'i sen kurup yönetirsin; güvenlik duvarı, güncelleme, izleme sana ait.

---

## Seçenek 3 — Ücretli VPS ($4-6/ay)

En az uğraş. Hetzner / DigitalOcean / Contabo. Seçenek 2'deki adımların aynısı.

---

## Karşılaştırma

| | GitHub Actions | Oracle Free VM | VPS |
|---|---|---|---|
| Maliyet | 0 | 0 | ~$5/ay |
| Kurulum | 10 dk | 40 dk | 20 dk |
| Panel | statik (6 saatte bir) | canlı | canlı |
| Yönetim yükü | yok | orta | orta |
| Bilgisayar kapalıyken | ✅ | ✅ | ✅ |
| Telefondan erişim | ✅ (Pages) | ✅ (IP:8080) | ✅ |

---

## Yerel kullanım (bilgisayar açıkken)

```bash
cd /root/binance-bot
.venv/bin/python dashboard.py
# tarayıcı: http://127.0.0.1:8080
```

Panel sunucu yoksa otomatik olarak `data.json`'a düşer (statik mod).
Bu yüzden aynı `dashboard.html` hem yerelde hem GitHub Pages'te çalışır.

---

## Verinin nereye yazıldığı

| Dosya | İçerik |
|---|---|
| `data/funding_snapshots.csv` | her gerçek taramanın tüm satırları (zaman serisi) |
| `data.json` | panele servis edilen son durum (analiz + canlı + seri) |

**Not:** Panel görüntülemesi snapshot YAZMAZ — sadece gerçek taramalar
(cron, `scan` komutu, `export_static.py`) yazar. Aksi halde sayfayı her
yenilemek "tarama sayısını" şişirir ve zaman serisi anlamsızlaşır.
