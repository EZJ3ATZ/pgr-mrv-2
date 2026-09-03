/* Service Worker — Medições Ocupacional PWA */
const CACHE = 'medicoes-v10';   // v10: o shell do app de campo saiu; o activate
                                 // apaga as versoes velhas e com elas as paginas
                                 // /mobile/* que ficaram cacheadas nos aparelhos
// O app de campo (/mobile e /campo) foi aposentado em 02/09/2026 por uso zero,
// e com ele o shell offline dele. Sobrou o strict necessário para o app de
// escritório: ele registra este mesmo /sw.js e usa a fila 'medicoes-offline'
// para reenviar coleta feita sem rede (index.html, _offEnqueue/_offSincronizar).
const SHELL = [];

// ── Install: cache shell (tolerante a falha — uma URL ruim não derruba tudo) ──
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c =>
      // fetch manual (em vez de c.add) p/ só cachear resposta ok e NÃO-redirecionada
      Promise.all(SHELL.map(u =>
        fetch(u, { credentials: 'same-origin' })
          .then(r => { if (r && r.ok && !r.redirected) return c.put(u, r); })
          .catch(err => console.warn('[sw] nao cacheou', u, err))
      ))
    ).then(() => self.skipWaiting())
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
      fetch(e.request)
        .then(r => {
          // Atualiza a cópia offline com a última versão vista online
          if (r && r.ok && !r.redirected) {
            const clone = r.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone)).catch(() => {});
          }
          return r;
        })
        .catch(async () =>
          await caches.match(e.request)
        )
    );
    return;
  }

  // Estáticos: cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(r => {
        if (r && r.ok) {                       // não cacheia 404/500 transitório
          const clone = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return r;
      }))
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
      // Só remove da fila com confirmação real (JSON ok:true). Sessão expirada
      // devolve 401/HTML → o item FICA na fila em vez de sumir.
      let saved = false;
      if (r.ok && (r.headers.get('content-type') || '').includes('application/json')) {
        try { saved = ((await r.json()) || {}).ok === true; } catch (_) { saved = false; }
      }
      if (saved) {
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
