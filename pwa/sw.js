// Service Worker for Trading App PWA
const CACHE = 'trading-app-v1';

// Pages to pre-cache on install (the "shell")
const SHELL = ['/', '/settings', '/manifest.json', '/icon.svg'];

// ── Install: cache the shell ──────────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())  // activate immediately
  );
});

// ── Activate: delete old caches ───────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())  // take control immediately
  );
});

// ── Fetch: network-first strategy ─────────────────────────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls: always try network, return error JSON if offline
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(
          JSON.stringify({ status: 'error', message: 'Offline — no network' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        )
      )
    );
    return;
  }

  // Pages: try network first, fall back to cache if offline
  e.respondWith(
    fetch(e.request)
      .then(resp => {
        // Update the cache with the fresh response
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
