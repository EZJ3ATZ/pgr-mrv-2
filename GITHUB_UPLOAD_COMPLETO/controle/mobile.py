# -*- coding: utf-8 -*-
"""Blueprint mobile PWA — rotas de UI e API para app de campo."""
from datetime import date, datetime
import json
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from flask_login import login_required, current_user, login_user, logout_user

from .db import (
    get_db, init_db, row_to_dict,
    list_planejamentos, get_planejamento,
    criar_visita, concluir_visita,
)

mobile_bp = Blueprint('mobile', __name__, url_prefix='/mobile')


def _hoje():
    return date.today().isoformat()


def _usuario():
    if current_user.is_authenticated:
        return getattr(current_user, 'nome', None) or getattr(current_user, 'email', '') or 'Técnico'
    return 'Técnico'


# ── Login / Logout ────────────────────────────────────────────────────

@mobile_bp.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('mobile.login_page'))
    return redirect(url_for('mobile.home'))


@mobile_bp.before_request
def _mobile_login_required():
    public = {'mobile.login_page', 'mobile.index'}
    from flask import request as _req
    from flask_login import current_user as _cu
    endpoint = _req.endpoint or ''
    if endpoint not in public and not _cu.is_authenticated:
        return redirect(url_for('mobile.login_page'))


@mobile_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    from .auth import User
    from .db import row_to_dict
    from werkzeug.security import check_password_hash
    erro = None
    email = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        try:
            with get_db() as conn:
                row = conn.execute(
                    'SELECT * FROM usuarios WHERE email=? AND ativo=1', (email,)
                ).fetchone()
            if row:
                d = row_to_dict(row)
                if check_password_hash(d['senha_hash'], senha):
                    u = User(d['id'], d['nome'], d['email'],
                             d.get('registro_mte', ''), d.get('role', 'tecnico'))
                    login_user(u, remember=True)
                    return redirect(url_for('mobile.home'))
            erro = 'E-mail ou senha incorretos.'
        except Exception as e:
            erro = f'Erro: {e}'
    return render_template('mobile/login.html', erro=erro, email=email)


@mobile_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('mobile.login_page'))


# ── Home: visitas de hoje ─────────────────────────────────────────────

@mobile_bp.route('/hoje')
@login_required
def home():
    init_db()
    hoje = _hoje()
    # Planejamentos com data de hoje (ou em execução)
    todos = list_planejamentos({'tecnico': None})
    planejamentos = [
        p for p in todos
        if (p.get('data_prevista') or '')[:10] == hoje
        or p.get('status') == 'em_execucao'
    ]

    # Conta itens pendentes na fila offline (sempre 0 no server — JS atualiza)
    return render_template('mobile/home.html',
                           data_hoje=_fmt_data(hoje),
                           planejamentos=planejamentos,
                           pendentes_offline=0)


# ── Visita a partir de planejamento ───────────────────────────────────

@mobile_bp.route('/visita/<int:pid>')
@login_required
def visita(pid):
    init_db()
    p = get_planejamento(pid)
    if not p:
        return redirect(url_for('mobile.home'))
    return render_template('mobile/visita.html',
                           planejamento=p,
                           hoje=_hoje(),
                           usuario=_usuario())


# ── Nova visita avulsa ────────────────────────────────────────────────

@mobile_bp.route('/nova-visita')
@login_required
def nova_visita():
    init_db()
    return render_template('mobile/nova_visita.html',
                           hoje=_hoje(),
                           usuario=_usuario())


# ── API: busca de empresas (autocomplete) ─────────────────────────────

@mobile_bp.route('/api/empresas')
@login_required
def api_empresas():
    init_db()
    q = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 8)), 20)
    if not q or len(q) < 2:
        return jsonify([])
    with get_db() as conn:
        like = f'%{q}%'
        rows = conn.execute(
            '''SELECT id, nome FROM empresas
               WHERE nome ILIKE ? AND (pendente IS NULL OR pendente=0)
               ORDER BY nome LIMIT ?''',
            (like, limit)
        ).fetchall() if _use_pg() else conn.execute(
            '''SELECT id, nome FROM empresas
               WHERE LOWER(nome) LIKE LOWER(?) AND (pendente IS NULL OR pendente=0)
               ORDER BY nome LIMIT ?''',
            (like, limit)
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


# ── API: salvar visita (online ou recebida do offline-queue) ──────────

@mobile_bp.route('/api/visita', methods=['POST'])
@login_required
def api_salvar_visita():
    init_db()
    data = request.get_json(force=True) or {}

    planejamento_id = _int(data.get('planejamento_id'))
    empresa_id      = _int(data.get('empresa_id'))
    demanda_id      = _int(data.get('demanda_id'))

    # Cria o registro de visita
    vid = criar_visita({
        'planejamento_id': planejamento_id,
        'demanda_id':      demanda_id,
        'empresa_id':      empresa_id,
        'tecnico':         data.get('tecnico') or _usuario(),
        'data_visita':     data.get('data_visita') or _hoje(),
        'hora_inicio':     data.get('hora_inicio'),
        'hora_termino':    data.get('hora_termino'),
        'tipo_visita':     data.get('tipo_visita', 'medicao'),
        'resultado':       data.get('resultado', 'concluido'),
        'observacao_geral': data.get('observacao') or data.get('observacao_geral'),
    })

    # Conclui com dados de execução de campo
    concluir_visita(vid, {
        'resultado':             data.get('resultado', 'concluido'),
        'justificativa':         data.get('justificativa'),
        'hora_termino':          data.get('hora_termino'),
        'agentes_executados':    _parse_lista(data.get('agentes_executados')),
        'agentes_nao_executados': _parse_lista(data.get('agentes_nao_executados')),
        'agentes_adicionados':   _parse_lista(data.get('agentes_adicionados')),
        'cobravel':              int(data.get('cobravel', 1)),
        'observacao':            data.get('observacao') or data.get('observacao_geral'),
    })

    # Salva assinatura digital como arquivo se presente
    _salvar_assinatura(vid, data.get('assinatura_data_url'))

    return jsonify({'ok': True, 'visita_id': vid})


# ── Helpers ───────────────────────────────────────────────────────────

def _use_pg():
    import os
    return bool(os.environ.get('DATABASE_URL'))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_lista(v):
    if not v:
        return None
    if isinstance(v, list):
        return v
    # Tenta JSON, senão divide por vírgula
    try:
        return json.loads(v)
    except Exception:
        return [x.strip() for x in str(v).split(',') if x.strip()]


def _salvar_assinatura(visita_id, data_url):
    if not data_url or not data_url.startswith('data:image/'):
        return
    try:
        import base64, os
        header, b64 = data_url.split(',', 1)
        img_bytes = base64.b64decode(b64)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sig_dir = os.path.join(base, 'data', 'assinaturas')
        os.makedirs(sig_dir, exist_ok=True)
        path = os.path.join(sig_dir, f'visita_{visita_id}.png')
        with open(path, 'wb') as f:
            f.write(img_bytes)
    except Exception:
        pass  # Assinatura é opcional


def _fmt_data(iso: str) -> str:
    try:
        d = datetime.strptime(iso, '%Y-%m-%d')
        meses = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
        dias  = ['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo']
        return f'{dias[d.weekday()]}, {d.day} de {meses[d.month-1]}. de {d.year}'
    except Exception:
        return iso
