const CACHE_NAME = 'horarios-buses-panguipulli-v1';
const assetsToCache = [
    '/',
    '/index.html',
    '/logo.jpg',
    'https://cdn.tailwindcss.com',
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'
];

// Install event: cache all the main assets
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('Opened cache');
                return cache.addAll(assetsToCache);
            })
    );
});

// Fetch event: serve from cache if available, otherwise fetch from network
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request)
            .then(response => {
                // Cache hit - return response
                if (response) {
                    return response;
                }
                // Not in cache - fetch from network
                return fetch(event.request);
            })
    );
});


