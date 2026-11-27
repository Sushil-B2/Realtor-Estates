/* Realtor Estates — service worker
   Strategy
   - Shell assets: stale-while-revalidate (fast boot, updates in background)
   - API: network-only. Never cache tenant data in Cache Storage: a shared
     device in a site office must not leak the previous user's pipeline.
   - Public pages (/f/*, /site): network-first with cache fallback so a field
     agent can re-open a brochure page with no signal.
   - Offline: JSON error for API calls, cached shell for navigation. */

const VERSION = 're-v1.40.1-lead-filter';
const SHELL = `${VERSION}-shell`;
const PUBLIC = `${VERSION}-public`;

const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/styles.css',
  '/js/core.js',
  '/js/views-ops.js',
  '/js/views-ai.js',
  '/js/views-growth.js',
  '/js/views-platform.js',
  '/js/views-workspaces.js',
  '/manifest.webmanifest',
  '/icons/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(SHELL_ASSETS).catch(() => {})).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  if (req.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;

  // API: network only, explicit offline error
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req).catch(() =>
        new Response(JSON.stringify({ error: { code: 'OFFLINE', message: 'You are offline. This action needs a connection.' } }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    );
    return;
  }

  // Public marketing pages: network-first, cache fallback
  if (url.pathname.startsWith('/f/') || url.pathname === '/site') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(PUBLIC).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then((r) => r || new Response('<h1>Offline</h1><p>This page has not been cached yet.</p>', { headers: { 'Content-Type': 'text/html' } })))
    );
    return;
  }

  // Navigation: serve shell
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/index.html').then((r) => r || new Response('Offline', { status: 503 })))
    );
    return;
  }

  // Static: stale-while-revalidate
  event.respondWith(
    caches.open(SHELL).then((cache) =>
      cache.match(req).then((cached) => {
        const network = fetch(req)
          .then((res) => {
            if (res && res.status === 200) cache.put(req, res.clone());
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })
    )
  );
});

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
