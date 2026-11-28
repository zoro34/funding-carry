/* Service worker — paneli telefona kurulabilir yapar ve son veriyi önbellekte tutar.
 *
 * Strateji:
 *   - Uygulama kabuğu (html, ikon, manifest): cache-first  -> anında açılır
 *   - data.json ve /api/*: network-first                   -> taze veri; ağ yoksa son önbellek
 *
 * Önemli: service worker yalnızca HTTPS veya localhost'ta çalışır. LAN'da
 * http://192.168.x.x üzerinden açarsan sayfa açılır ama "uygulama olarak kur"
 * seçeneği çıkmaz (tarayıcı güvenli bağlam ister).
 */
// Her varlık değişiminde bu sürüm ARTIRILMALI; artmazsa tarayıcı eski kabuğu
// önbellekten servis eder ve yeni arayüz hiç görünmez. Deploy sırasında
// workflow bunu zaman damgasıyla otomatik yeniden yazar.
const CACHE = 'funding-carry-202611281153';
const SHELL = [
  './', './dashboard.html', './index.html', './manifest.json', './local.js', './learn.json',
  './icons/icon-192.png', './icons/icon-512.png', './icons/maskable-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(SHELL.map(u => c.add(u))))   // eksik dosya kurulumu bozmasın
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const isData = url => url.pathname.endsWith('data.json') || url.pathname.includes('/api/');

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (isData(url)) {
    // network-first: taze veri; başarısızsa son bilinen veri
    e.respondWith(
      fetch(req)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // app shell: cache-first, arkada tazele
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return res;
    }).catch(() => hit))
  );
});
