// Service Worker: network-first para HTML y APIs, cache-first para estáticos
const CACHE = 'bsred-v3';
const ASSETS = [
  '/',
  '/logo.jpg',
  '/telemetria.js'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k.startsWith('bsred-') && k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // 1. IGNORAR PETICIONES POST (Login, Registro, GPS)
  if (e.request.method === 'POST') {
    return;
  }

  // Las sesiones y métricas en vivo nunca se guardan ni se leen del caché.
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request, { cache: 'no-store' }));
    return;
  }

  // 2. PÁGINAS HTML: Network-first
  if ( url.pathname.includes('.html') || url.pathname === '/usuario' || url.pathname === '/') {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // 3. ESTÁTICOS (Imágenes, Tailwind): Cache-first
  e.respondWith(
    caches.match(e.request).then(res => res || fetch(e.request))
  );
});
