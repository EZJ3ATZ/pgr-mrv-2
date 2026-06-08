/* Service Worker — Medições Ocupacional PWA */
const CACHE = 'medicoes-v3';
const SHELL = [
  '/mobile/',
  '/mobile/hoje',
  '/mobile/nova-visita',
  '/static/mobile.css',
  '/static/sw.js',
  '/static/manifest.json',
];

// ── Install: cache shell ───────────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first para API, cache-first para estáticos ─────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Só intercepta mesmo domínio
  if (url.origin !== location.origin) return;

  // Nunca cachear o próprio SW (evita travar atualizações)
  if (url.pathname === '/sw.js' || url.pathname === '/static/sw.js') return;

  // Navegação (HTML/páginas): SEMPRE network-first — nunca servir app velho.
  // Só cai no cache se estiver realmente offline (mantém PWA usável sem rede).
  if (e.request.mode === 'navigate' ||
      (e.request.headers.get('accept') || '').includes('text/html')) {
    e.respondWith(
      fetch(e.request).catch(async () =>
        (await caches.match(e.request)) || (await caches.match('/mobile/'))
      )
    );
    return;
  }

  // Estáticos: cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      }))
    );
    return;
  }

  // Shell pages: network-first com fallback para cache
  if (url.pathname.startsWith('/mobile/')) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const clone = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // API: network-first, sem fallback (deixa o app tratar o erro)
  // POST de sync: não interceptar (tratado pelo app via fila)
});

// ── Background Sync (Android Chrome) ─────────────────────────────────
self.addEventListener('sync', e => {
  if (e.tag === 'sync-visitas') {
    e.waitUntil(flushQueue());
  }
});

async function flushQueue() {
  // Abre IDB e envia itens da fila
  const db = await openIDB();
  const items = await getAllQueued(db);
  for (const item of items) {
    try {
      const r = await fetch(item.url, {
        method: item.method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item.body),
      });
      if (r.ok) {
        await deleteQueued(db, item.id);
      }
    } catch (_) {
      // Fica na fila para próxima tentativa
    }
  }
}

// ── IndexedDB helpers ─────────────────────────────────────────────────
function openIDB() {
  return new Promise((res, rej) => {
    const req = indexedDB.open('medicoes-offline', 1);
    req.onupgradeneeded = e => {
      e.target.result.createObjectStore('queue', { keyPath: 'id', autoIncrement: true });
    };
    req.onsuccess = e => res(e.target.result);
    req.onerror = e => rej(e);
  });
}

function getAllQueued(db) {
  return new Promise((res, rej) => {
    const tx = db.transaction('queue', 'readonly');
    const req = tx.objectStore('queue').getAll();
    req.onsuccess = e => res(e.target.result);
    req.onerror = e => rej(e);
  });
}

function deleteQueued(db, id) {
  return new Promise((res, rej) => {
    const tx = db.transaction('queue', 'readwrite');
    const req = tx.objectStore('queue').delete(id);
    req.onsuccess = () => res();
    req.onerror = e => rej(e);
  });
}
