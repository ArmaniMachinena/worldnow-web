/* WorldNow:Emergency offline cache.
   The page and the data are fetched from the network first; when the network is down
   the last saved copy is shown. Map libraries are kept from the first visit. */
const CACHE = 'wn-v1';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

function save(req, res) {
  if (res && (res.ok || res.type === 'opaque')) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
  return res;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  const page = req.mode === 'navigate' || /\/(index\.html)?$/.test(u.pathname) || u.pathname.endsWith('manual-ru.pdf');
  const data = u.host === 'raw.githubusercontent.com' && u.pathname.endsWith('.json');
  const lib = /(^|\.)(cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com)$/.test(u.host);
  if (page || data) {
    e.respondWith(fetch(req).then(r => save(req, r)).catch(() => caches.match(req, {ignoreSearch: true})));
  } else if (lib) {
    e.respondWith(caches.match(req).then(m => m || fetch(req).then(r => save(req, r))));
  }
});
