// Service Worker: network-first para HTML y APIs, cache-first para estáticos
const CACHE = 'bsred-v6';
const ASSETS = [
  '/',
  '/logo.jpg',
  '/logo_192.png',
  '/telemetria.js',
  '/mapa-rutas.js',
  '/favoritos.js',
  '/choferes.js'
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

  // Las escrituras se envían directamente, sin consultar el caché.
  if (e.request.method !== 'GET') {
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

function notificationText(value, limit) {
  if (typeof value !== 'string') return '';
  return value.replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ')
    .replace(/\s+/g, ' ').trim().slice(0, limit);
}

function routeId(value) {
  if (typeof value === 'string' && !/^\d{1,15}$/.test(value)) return null;
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function notificationUrl(value, horarioId = null) {
  try {
    const url = new URL(typeof value === 'string' ? value : '/', self.location.origin);
    const id = routeId(url.searchParams.get('recorrido'));
    if (url.origin !== self.location.origin || url.pathname !== '/' || !id) return '/';
    if (horarioId !== null && id !== horarioId) return '/';
    return `/?recorrido=${id}`;
  } catch (_) {
    return '/';
  }
}

function departureTime(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
    return null;
  }
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

self.addEventListener('push', event => {
  let payload = {};
  try {
    const parsed = event.data ? event.data.json() : null;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) payload = parsed;
  } catch (_) {
    // Un mensaje inválido nunca se interpreta como HTML o código.
  }

  const departure = departureTime(payload.salida_en);
  // Un dispositivo que estuvo sin conexión no recibe un aviso de una salida pasada.
  if (departure !== null && departure <= Date.now()) return;

  const id = routeId(payload.horario_id);
  const url = id === null ? '/' : notificationUrl(payload.url, id);
  const validDeparture = id !== null && departure !== null && url !== '/';
  const titleText = validDeparture ? notificationText(payload.title, 100) : 'BSRed';
  const title = titleText.toLocaleLowerCase('es').includes('salida programada')
    ? titleText : `Salida programada · ${titleText || 'BSRed'}`;
  const body = (validDeparture ? notificationText(payload.body, 500) : '')
    || 'Revisa el horario de tu recorrido favorito en BSRed.';
  // La misma salida reemplaza su aviso, incluso si llega dos veces por Web Push.
  const tag = validDeparture
    ? `bsred-salida-${id}-${Math.floor(departure / 1000)}`
    : 'bsred-aviso-salida';

  event.waitUntil(self.registration.showNotification(title, {
    body,
    icon: '/logo_192.png',
    badge: '/logo_192.png',
    tag,
    renotify: false,
    data: {
      url: validDeparture ? url : '/',
      horario_id: validDeparture ? id : null,
      salida_en: validDeparture ? new Date(departure).toISOString() : null
    }
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const id = routeId(event.notification.data?.horario_id);
  const path = id === null ? '/' : notificationUrl(event.notification.data?.url, id);
  const url = new URL(path, self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const candidates = windows.filter(client => {
      try { return new URL(client.url).origin === self.location.origin; }
      catch (_) { return false; }
    }).sort((a, b) => Number(b.url === url) - Number(a.url === url));

    for (const client of candidates) {
      try {
        const target = client.url === url ? client : await client.navigate(url);
        if (target) return await target.focus();
      } catch (_) {
        // Si una pestaña se cerró entre ambas operaciones, probamos la siguiente.
      }
    }
    return self.clients.openWindow(url);
  })());
});
