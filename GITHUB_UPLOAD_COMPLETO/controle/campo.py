# -*- coding: utf-8 -*-
"""Blueprint Modo Campo — tela offline-first de campo (tela cheia, cara de PC).

Objetivo: o técnico abre as OS do dia, registra a coleta/relatório, assina e
fotografa MESMO SEM INTERNET. Os dados das OS são baixados quando há rede e
guardados no navegador (IndexedDB) para abrir offline. O envio reusa o endpoint
robusto `/mobile/api/visita` e a mesma fila offline (IndexedDB `medicoes-offline`)
que o service worker já sincroniza.

Não duplica backend: só serve a tela e um JSON com as OS do dia.
"""
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect
from flask_login import current_user

from .db import init_db, list_planejamentos

campo_bp = Blueprint('campo', __name__, url_prefix='/campo')


def _hoje():
    return date.today().isoformat()


def _usuario():
    if current_user.is_authenticated:
        return getattr(current_user, 'nome', None) or getattr(current_user, 'email', '') or 'Técnico'
    return 'Técnico'


# ── Auth guard ─────────────────────────────────────────────────────────
# Mesma regra do desktop: precisa estar logado. Para a API responde 401 JSON
# (a tela cai no cache local); para a página, manda pro login.
@campo_bp.before_request
def _guard():
    if current_user.is_authenticated:
        return
    if (request.path or '').startswith('/campo/api'):
        return jsonify({'ok': False, 'erro': 'nao autenticado'}), 401
    return redirect('/auth/login?next=/campo/')


def _mostra(p, hoje, ini):
    """Mesmo filtro da home mobile: o que o técnico precisa ver em campo —
    visitas de hoje, em execução e atrasadas (confirmadas, últimos 30 dias)."""
    d  = (p.get('data_prevista') or '')[:10]
    st = (p.get('status') or '')
    if st in ('concluido', 'cancelado'):
        return False
    if st == 'em_execucao':
        return True
    if d == hoje:
        return True
    if st == 'confirmado' and d and ini <= d < hoje:
        return True
    return False


# ── Página (SPA offline-first) ─────────────────────────────────────────
@campo_bp.route('/')
def index():
    return render_template('campo.html',
                           data_hoje=_fmt_data(_hoje()),
                           usuario=_usuario())


# ── API: OS do dia (JSON cacheável offline) ────────────────────────────
@campo_bp.route('/api/dados')
def api_dados():
    """Retorna as OS que o técnico precisa em campo, com todos os detalhes
    necessários para preencher offline (agentes previstos, qtd dosímetros/bombas,
    empresa, demanda, OS). A tela guarda isso no IndexedDB e renderiza offline."""
    try:
        init_db()
        hoje = _hoje()
        ini  = (date.today() - timedelta(days=30)).isoformat()
        todos = list_planejamentos({})
        os_lista = [p for p in todos if _mostra(p, hoje, ini)]
        os_lista.sort(key=lambda p: (p.get('data_prevista') or '9999-12-31')[:10])
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'erro': str(e), 'os': []}), 500

    return jsonify({
        'ok':         True,
        'gerado_em':  datetime.now().isoformat(timespec='seconds'),
        'usuario':    _usuario(),
        'data_hoje':  _fmt_data(hoje),
        'os':         os_lista,
    })


# ── Helper ─────────────────────────────────────────────────────────────
def _fmt_data(iso: str) -> str:
    try:
        d = datetime.strptime(iso, '%Y-%m-%d')
        meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        dias  = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
        return f'{dias[d.weekday()]}, {d.day} de {meses[d.month - 1]}. de {d.year}'
    except Exception:
        return iso
