// Service Worker simple: cache-first para shell y network-first para API
const CACHE = 'bsred-v1';
const ASSETS = [
  '/', '/index.html', '/logo.jpg',
  'https://cdn.tailwindcss.com'
];

self.addEventListener('install', e=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e=>{
  e.waitUntil(
    caches.keys().then(keys=>Promise.all(
      keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e=>{
  const url = new URL(e.request.url);

  // API: network-first con fallback a cache
  if (url.pathname.includes('/api/horarios')) {
    e.respondWith(
      fetch(e.request).then(res=>{
        const copy = res.clone();
        caches.open(CACHE).then(c=>c.put(e.request, copy));
        return res;
      }).catch(()=> caches.match(e.request))
    );
    return;
  }

  // Estáticos: cache-first
  e.respondWith(
    caches.match(e.request).then(res=> res || fetch(e.request))
  );
});
