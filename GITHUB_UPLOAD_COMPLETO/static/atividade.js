/* ═══════════════════════════════════════════════════════════════════════
   LOG DE ATIVIDADE HUMANA — cada clique, cada mexida

   Vale para TODAS as telas: escritório (index.html), Planilha de Campo
   (campo.html) e App Móvel (mobile/*.html). Antes isso morava só no
   index.html, então o trabalho do técnico — que é onde o sistema mais é
   usado — ficava invisível.

   Requisição NÃO é uso: a aba parada chama /controle/eventos sozinha a cada
   minuto e isso fazia o painel dizer que a pessoa estava usando o sistema.
   Clique é humano. Guarda o RÓTULO do elemento, nunca o que foi digitado.

   ⚠️ O service worker cacheia /static/ cache-first: ao mexer neste arquivo,
   subir o ?v= nas tags <script> que o chamam, senão o navegador serve o velho.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__ativLigado) return;          // 2 <script> na mesma página
  window.__ativLigado = true;

  var LOTE = 12, INTERVALO_MS = 20000, TETO = 1500;
  var fila = [], contados = 0, timer = null, ultimoToque = Date.now();
  var _fetchOriginal = window.fetch;

  function telaAtual() {
    try {
      var p = document.querySelector('.tab-panel.active');
      if (p && p.id) return p.id.replace(/^tab-/, '');
      return location.pathname;
    } catch (_) { return null; }
  }

  /* ── marca o último toque humano; vai no cabeçalho de toda requisição ── */
  ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(function (ev) {
    try {
      window.addEventListener(ev, function () { ultimoToque = Date.now(); },
                              { capture: true, passive: true });
    } catch (_) {}
  });

  window.fetch = function (entrada, opcoes) {
    try {
      var u = String((entrada && entrada.url) || entrada || '');
      // só em chamada nossa: cabeçalho custom em domínio de terceiro dispara
      // preflight de CORS e quebraria a requisição
      if (u.indexOf('/') === 0 || u.indexOf(location.origin) === 0) {
        var o = {};
        for (var k in (opcoes || {})) o[k] = opcoes[k];
        var h = new Headers((opcoes && opcoes.headers) || {});
        h.set('X-Interacao-Ms', String(Date.now() - ultimoToque));
        o.headers = h;
        return _fetchOriginal.call(this, entrada, o);
      }
    } catch (_) { /* qualquer problema: manda sem o cabeçalho */ }
    return _fetchOriginal.call(this, entrada, opcoes);
  };

  /* ── captura ────────────────────────────────────────────────────────── */
  var IGNORAR = { HTML: 1, BODY: 1, MAIN: 1, SECTION: 1, FOOTER: 1 };
  var CLICAVEL = { BUTTON: 1, A: 1, SUMMARY: 1, LABEL: 1, TH: 1, OPTION: 1 };

  function rotuloDe(el) {
    for (var n = el, i = 0; n && i < 5; n = n.parentElement, i++) {
      if (n.getAttribute) {
        var aria = n.getAttribute('aria-label') || n.getAttribute('title');
        if (aria && aria.trim()) return aria.trim().slice(0, 120);
      }
      var tag = (n.tagName || '').toUpperCase();
      var ehClicavel = CLICAVEL[tag] ||
        (n.getAttribute && (n.getAttribute('onclick') ||
                            n.getAttribute('role') === 'button')) ||
        (n.classList && (n.classList.contains('sidebar-item') ||
                         n.classList.contains('tab') ||
                         n.classList.contains('btn')));
      if (ehClicavel) {
        var t = (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim();
        if (t) return t.slice(0, 120);
        var nm = n.getAttribute && (n.getAttribute('name') || n.id);
        if (nm) return String(nm).slice(0, 120);
      }
    }
    return null;
  }

  function alvoDe(el) {
    try {
      var tag = (el.tagName || '?').toLowerCase();
      var id = el.id ? '#' + el.id : '';
      var cls = (el.className && typeof el.className === 'string')
        ? '.' + el.className.split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '';
      return (tag + id + cls).slice(0, 150);
    } catch (_) { return null; }
  }

  function empilhar(tipo, rotulo, alvo) {
    if (contados >= TETO) return;
    contados++;
    fila.push({ tipo: tipo, rotulo: rotulo, alvo: alvo, tela: telaAtual() });
    if (fila.length >= LOTE) descarregar();
    else if (!timer) timer = setTimeout(descarregar, INTERVALO_MS);
  }

  function descarregar(usarBeacon) {
    if (timer) { clearTimeout(timer); timer = null; }
    if (!fila.length) return;
    var lote = fila;
    fila = [];
    var corpo = JSON.stringify({ eventos: lote });
    try {
      if (usarBeacon && navigator.sendBeacon) {
        // fechando a aba: beacon é o único que ainda chega
        navigator.sendBeacon('/controle/atividade',
          new Blob([corpo], { type: 'application/json' }));
        return;
      }
      _fetchOriginal.call(window, '/controle/atividade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: corpo
      }).catch(function () { devolver(lote); });
    } catch (_) { devolver(lote); }
  }

  // App de Campo trabalha OFFLINE: em vez de perder o clique, devolve para a
  // fila e tenta no próximo lote. O teto evita crescer sem limite sem rede.
  function devolver(lote) {
    try {
      if (fila.length + lote.length <= TETO) fila = lote.concat(fila);
      if (!timer) timer = setTimeout(descarregar, INTERVALO_MS * 3);
    } catch (_) {}
  }

  document.addEventListener('click', function (e) {
    try {
      var el = e.target;
      if (!el || !el.tagName || IGNORAR[el.tagName.toUpperCase()]) return;
      var rot = rotuloDe(el);
      if (!rot) return;                     // clique em área morta: não registra
      empilhar('clique', rot, alvoDe(el));
    } catch (_) {}
  }, true);

  document.addEventListener('submit', function (e) {
    try {
      var f = e.target;
      empilhar('envio', (f && (f.getAttribute('name') || f.id)) || 'formulário',
               alvoDe(f));
    } catch (_) {}
  }, true);

  // troca de tela no app de escritório (só existe no index.html)
  if (typeof window.goToTab === 'function') {
    var _goToTab = window.goToTab;
    window.goToTab = function (nome) {
      try { empilhar('tela', String(nome), 'goToTab'); } catch (_) {}
      return _goToTab.apply(this, arguments);
    };
  }

  window.addEventListener('pagehide', function () { descarregar(true); });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') descarregar(true);
  });
})();
