/**
 * Runner Dashboard Service Worker
 * Provides offline shell, caching, and install support.
 *
 * Cache strategy: stale-while-revalidate for static assets,
 * network-first for API calls, cache-first for offline shell.
 */

const CACHE_NAME = 'runner-dashboard-v1';
const OFFLINE_URL = '/offline.html';

// Assets to cache on install
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/src/main.tsx',
  '/src/index.css',
  '/manifest.webmanifest',
  '/icon.svg',
];

// API paths that should not be cached
const API_DENYLIST = [
  /^\/api\/credentials(?:\/|$)/,
  /^\/api\/auth/,
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

// Activate — clean old caches
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

// Helper: check if URL is an API request
function isApiRequest(url) {
  const pathname = new URL(url).pathname;
  return API_DENYLIST.some((pattern) => pattern.test(pathname));
}

// Fetch — routing strategies
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests and cross-origin requests
  if (request.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // API requests: network-first with short timeout
  if (isApiRequest(request.url)) {
    event.respondWith(fetch(request).catch(() => caches.match(request)));
    return;
  }

  // Static assets: stale-while-revalidate
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetchPromise = fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return networkResponse;
        })
        .catch(() => cached);

      return cached || fetchPromise;
    })
  );
});
