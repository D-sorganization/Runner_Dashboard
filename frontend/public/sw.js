/**
 * Runner Dashboard Service Worker
 * Provides offline shell, caching, and install support.
 *
 * Cache strategy: cache-first for static assets, network-only for API calls,
 * cache-first for offline shell on navigation misses.
 */

const BUILD_ID = new URL(self.location.href).searchParams.get('build') || 'dev';
const CACHE_NAME = `runner-dashboard-${BUILD_ID}`;
const OFFLINE_URL = '/offline.html';
const STATIC_EXTS = /\.(?:js|css|svg|html|woff2|png|webp|ico)$/;

// Assets to cache on install
const STATIC_ASSETS = [
  '/index.html',
  OFFLINE_URL,
  '/icon.svg',
];

// Install — cache static shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Failed to cache some assets:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate — clean old caches and notify clients
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

// Message handler for client communication
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// Helper: check if URL is an API request
function isApiRequest(url) {
  const pathname = new URL(url).pathname;
  return pathname.startsWith('/api/');
}

function isStaticAsset(pathname) {
  return STATIC_EXTS.test(pathname);
}

function networkOnly(request) {
  return fetch(new Request(request, { cache: 'no-store' }));
}

function cacheFirst(request) {
  return caches.match(request).then((cached) => {
    if (cached) {
      return cached;
    }

    return fetch(request).then((networkResponse) => {
      if (networkResponse && networkResponse.status === 200) {
        const clone = networkResponse.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
      }
      return networkResponse;
    });
  });
}

// Fetch — routing strategies
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (url.origin !== self.location.origin) {
    return;
  }

  // API requests must never use or populate the service worker cache.
  if (isApiRequest(request.url)) {
    event.respondWith(networkOnly(request));
    return;
  }

  if (request.method !== 'GET') {
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (isStaticAsset(url.pathname)) {
    event.respondWith(cacheFirst(request));
  }
});

// Web Push — receive and display notifications (issue #1724)
self.addEventListener('push', (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload = { title: event.data.text() };
    }
  }

  const title = payload.title || 'Runner Dashboard';
  const targetUrl = payload.url || payload.deep_link || '/';
  const options = {
    body: payload.body || '',
    icon: payload.icon || '/icon.svg',
    badge: payload.badge || '/icon.svg',
    tag: payload.tag || payload.topic || 'default',
    data: {
      url: targetUrl,
      ...payload,
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Notification click — focus existing window or open target URL
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const rawUrl = event.notification.data && event.notification.data.url;
  const targetUrl = new URL(rawUrl || '/', self.location.origin).href;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === targetUrl && 'focus' in client) {
          return client.focus();
        }
      }
      for (const client of clientList) {
        if ('focus' in client) {
          if ('navigate' in client) {
            return client.navigate(targetUrl).then((c) => (c ? c.focus() : client.focus()));
          }
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
      return null;
    })
  );
});

