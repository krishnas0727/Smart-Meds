const CACHE_NAME = 'smart-meds-static-v1';
const PUBLIC_ASSETS = [
  '/static/offline.html',
  '/static/icons/smart-meds-192.png',
  '/static/icons/smart-meds-512.png',
  '/static/manifest.webmanifest'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PUBLIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key.startsWith('smart-meds-static-') && key !== CACHE_NAME)
        .map((key) => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  // Cache only the explicit public asset allowlist; all app pages and API calls are network-only.
  if (PUBLIC_ASSETS.includes(url.pathname)) {
    event.respondWith(
      fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match(request))
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/static/offline.html')));
  }
});

// ==================== BROWSER NOTIFICATIONS EVENT LISTENERS ==================== //
self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'Smart Meds — Medicine Reminder';
  const options = {
    body: data.body || 'Time to take your scheduled dose.',
    icon: '/static/icons/smart-meds-192.png',
    badge: '/static/icons/smart-meds-192.png',
    tag: data.tag || 'smart-meds-notification',
    renotify: true,
    data: data.url || '/'
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const urlToOpen = event.notification.data || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(urlToOpen) && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});
