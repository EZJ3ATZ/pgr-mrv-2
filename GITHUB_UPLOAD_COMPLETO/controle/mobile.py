# -*- coding: utf-8 -*-
"""Blueprint mobile PWA — rotas de UI e API para app de campo."""
from datetime import date, datetime
import json
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import current_user, login_user, logout_user
from werkzeug.security import check_password_hash

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


# ── Auth guard (sem @login_required para não redirecionar ao login desktop) ──

@mobile_bp.before_request
def _guard():
    public = {'mobile.login_page', 'mobile.index', 'mobile.logout', 'mobile.instalar'}
    endpoint = request.endpoint or ''
    if endpoint not in public and not current_user.is_authenticated:
        return redirect(url_for('mobile.login_page'))


# ── Login / Logout ────────────────────────────────────────────────────

@mobile_bp.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('mobile.login_page'))
    return redirect(url_for('mobile.home'))


@mobile_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('mobile.home'))

    erro = None
    email = ''
    if request.method == 'POST':
        from .auth import User
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
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
            import traceback
            traceback.print_exc()
            erro = f'Erro interno: {e}'
    return render_template('mobile/login.html', erro=erro, email=email)


@mobile_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('mobile.login_page'))


@mobile_bp.route('/instalar')
def instalar():
    return render_template('mobile/instalar.html')


# ── Home: visitas de hoje ─────────────────────────────────────────────

@mobile_bp.route('/hoje')
def home():
    try:
        init_db()
        from datetime import timedelta
        hoje = _hoje()
        ini = (date.today() - timedelta(days=30)).isoformat()  # janela de atrasados
        todos = list_planejamentos({})
        def _mostra(p):
            d  = (p.get('data_prevista') or '')[:10]
            st = (p.get('status') or '')
            if st in ('concluido', 'cancelado'):
                return False
            if st == 'em_execucao':
                return True            # em execução: sempre aparece
            if d == hoje:
                return True            # planejado p/ hoje (qualquer status)
            if st == 'confirmado' and d and ini <= d < hoje:
                return True            # atrasado (confirmado, últimos 30 dias)
            return False
        planejamentos = [p for p in todos if _mostra(p)]
        # atrasados primeiro, depois hoje (data crescente)
        planejamentos.sort(key=lambda p: (p.get('data_prevista') or '9999-12-31')[:10])
    except Exception as e:
        import traceback; traceback.print_exc()
        planejamentos = []

    return render_template('mobile/home.html',
                           data_hoje=_fmt_data(_hoje()),
                           planejamentos=planejamentos,
                           pendentes_offline=0)


# ── Visita a partir de planejamento ───────────────────────────────────

@mobile_bp.route('/visita/<int:pid>')
def visita(pid):
    try:
        init_db()
        p = get_planejamento(pid)
    except Exception:
        p = None
    if not p:
        return redirect(url_for('mobile.home'))
    return render_template('mobile/visita.html',
                           planejamento=p,
                           hoje=_hoje(),
                           usuario=_usuario())


# ── Nova visita avulsa ────────────────────────────────────────────────

@mobile_bp.route('/nova-visita')
def nova_visita():
    return render_template('mobile/nova_visita.html',
                           hoje=_hoje(),
                           usuario=_usuario())


# ── API: busca de empresas ─────────────────────────────────────────────

@mobile_bp.route('/api/empresas')
def api_empresas():
    q = (request.args.get('q') or '').strip()
    limit = min(int(request.args.get('limit', 8) or 8), 20)
    if len(q) < 2:
        return jsonify([])
    try:
        with get_db() as conn:
            like = f'%{q}%'
            # Mostra empresas confirmadas E pendentes QUE TÊM demanda (reais, vindas do
            # Planner — ex.: LCA). Esconde só fantasmas puros (pendente sem demanda).
            cond_real = ("(e.pendente IS NULL OR e.pendente=0 "
                         "OR EXISTS (SELECT 1 FROM demandas d WHERE d.empresa_id = e.id))")
            if _use_pg():
                rows = conn.execute(
                    'SELECT e.id, e.nome, e.pendente FROM empresas e WHERE e.nome ILIKE ? '
                    'AND ' + cond_real + ' ORDER BY e.nome LIMIT ?',
                    (like, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT e.id, e.nome, e.pendente FROM empresas e WHERE LOWER(e.nome) LIKE LOWER(?) '
                    'AND ' + cond_real + ' ORDER BY e.nome LIMIT ?',
                    (like, limit)
                ).fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        return jsonify([])


# ── API: salvar visita ────────────────────────────────────────────────

# Texto de ciência exibido junto à assinatura do responsável (Diretriz Mestra)
CIENCIA_TEXTO = ('Declaro estar ciente das atividades realizadas, atividades não '
                 'realizadas, observações registradas e eventuais impedimentos '
                 'descritos neste relatório.')

IMPREVISTOS_VALIDOS = ('culpa_empresa', 'culpa_equipe', 'clima',
                       'ausencia_trabalhador', 'indisponibilidade', 'outros')


@mobile_bp.route('/api/visita', methods=['POST'])
def api_salvar_visita():
    try:
        init_db()
        data = request.get_json(force=True) or {}

        # ── Relatório de Visita é OBRIGATÓRIO (Diretriz Mestra) ──────────
        resultado = data.get('resultado', 'concluido')
        nao_exec  = _parse_lista(data.get('agentes_nao_executados')) or []

        # Visita não concluída ou com agente não executado → justificativa
        # + tipo de imprevisto NA HORA (não vira divergência depois).
        if resultado != 'concluido' or nao_exec:
            if not (data.get('justificativa') or '').strip():
                return jsonify({'ok': False, 'erro': 'Justificativa obrigatória: visita não '
                                'concluída ou há agentes não executados.'}), 400
            if (data.get('imprevisto_tipo') or '') not in IMPREVISTOS_VALIDOS:
                return jsonify({'ok': False, 'erro': 'Informe o tipo de imprevisto.',
                                'tipos_validos': list(IMPREVISTOS_VALIDOS)}), 400

        # Assinatura do responsável da empresa: ou assina (com nome), ou
        # registra o motivo da ausência. Sem isso a visita não fecha.
        tem_sig = bool(str(data.get('assinatura_empresa_data_url') or '').startswith('data:image/'))
        if tem_sig and not (data.get('assinante_nome') or '').strip():
            return jsonify({'ok': False, 'erro': 'Informe o nome de quem assinou pela empresa.'}), 400
        if not tem_sig and not (data.get('sem_assinatura_motivo') or '').strip():
            return jsonify({'ok': False, 'erro': 'Colete a assinatura do responsável da empresa '
                            'ou informe o motivo da ausência de assinatura.'}), 400

        # Resolve empresa: se não veio empresa_id mas veio o nome (visita avulsa
        # onde o técnico digitou mas não selecionou da lista), tenta casar pelo
        # nome para não salvar visita órfã.
        empresa_id = _int(data.get('empresa_id'))
        if not empresa_id and (data.get('empresa_nome') or '').strip():
            try:
                from .empresa_match import encontrar_empresa
                with get_db() as conn:
                    eid, _score, _met = encontrar_empresa(conn, data['empresa_nome'])
                if eid:
                    empresa_id = eid
            except Exception:
                pass

        vid = criar_visita({
            'planejamento_id':    _int(data.get('planejamento_id')),
            'demanda_id':         _int(data.get('demanda_id')),
            'empresa_id':         empresa_id,
            'numero_os':          (data.get('numero_os') or '').strip() or None,
            'tecnico':            data.get('tecnico') or _usuario(),
            'data_visita':        data.get('data_visita') or _hoje(),
            'hora_inicio':        data.get('hora_inicio'),
            'hora_termino':       data.get('hora_termino'),
            'tipo_visita':        data.get('tipo_visita', 'medicao'),
            'resultado':          data.get('resultado', 'concluido'),
            'observacao_geral':   data.get('observacao') or data.get('observacao_geral'),
            'acompanhante':       data.get('acompanhante'),
            'cargo_acompanhante': data.get('cargo_acompanhante'),
        })

        concluir_visita(vid, {
            'resultado':              data.get('resultado', 'concluido'),
            'justificativa':          data.get('justificativa'),
            'imprevisto_tipo':        data.get('imprevisto_tipo'),
            'hora_termino':           data.get('hora_termino'),
            'agentes_executados':     _parse_lista(data.get('agentes_executados')),
            'agentes_nao_executados': _parse_lista(data.get('agentes_nao_executados')),
            'agentes_adicionados':    _parse_lista(data.get('agentes_adicionados')),
            'cobravel':               int(data.get('cobravel', 1)),
            'observacao':             data.get('observacao') or data.get('observacao_geral'),
            'acompanhante':           data.get('acompanhante'),
            'cargo_acompanhante':     data.get('cargo_acompanhante'),
            'dosimetros_usados':      data.get('dosimetros_usados'),
            'bombas_usadas':          data.get('bombas_usadas'),
            'trabalhadores_json':     data.get('trabalhadores_json'),
        })

        _salvar_assinatura(vid, data.get('assinatura_data_url'),
                           data.get('assinatura_empresa_data_url'),
                           assinante_nome=data.get('assinante_nome'),
                           assinante_cargo=data.get('assinante_cargo') or data.get('cargo_acompanhante'),
                           sem_assinatura_motivo=data.get('sem_assinatura_motivo'))
        _salvar_fotos(vid, data.get('fotos'))
        return jsonify({'ok': True, 'visita_id': vid})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'erro': str(e)}), 500


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
    try:
        return json.loads(v)
    except Exception:
        return [x.strip() for x in str(v).split(',') if x.strip()]


def _salvar_assinatura(visita_id, data_url, data_url_empresa=None,
                       assinante_nome=None, assinante_cargo=None,
                       sem_assinatura_motivo=None):
    """Persiste as assinaturas da visita NO BANCO.
    - assinatura          → técnico Ocupacional (mapeia p/ sig_avaliado no laudo)
    - assinatura_empresa  → responsável da empresa (sig_empresa)
    Junto com a assinatura da empresa grava: nome/cargo de quem assinou,
    data-hora e o texto de ciência (Diretriz Mestra). Guarda a data-URL
    base64 inteira — o filesystem do Railway é efêmero."""
    if not visita_id:
        return
    def _ok(u):
        return bool(u) and str(u).startswith('data:image/')
    try:
        with get_db() as conn:
            if _ok(data_url):
                conn.execute("UPDATE visitas_tecnicas SET assinatura=? WHERE id=?",
                             (data_url, visita_id))
            if _ok(data_url_empresa):
                conn.execute(
                    "UPDATE visitas_tecnicas SET assinatura_empresa=?, assinante_nome=?, "
                    "assinante_cargo=?, assinado_em=CURRENT_TIMESTAMP, ciencia_texto=? WHERE id=?",
                    (data_url_empresa, (assinante_nome or '').strip() or None,
                     (assinante_cargo or '').strip() or None, CIENCIA_TEXTO, visita_id))
            elif sem_assinatura_motivo:
                conn.execute("UPDATE visitas_tecnicas SET sem_assinatura_motivo=? WHERE id=?",
                             (str(sem_assinatura_motivo).strip(), visita_id))
    except Exception as e:
        import traceback; traceback.print_exc()


def _salvar_fotos(visita_id, fotos):
    """Persiste fotos da visita no banco (visita_fotos).
    fotos: [{categoria: ambiente|atividade|equipamentos, data: dataURL, legenda}]"""
    if not visita_id or not fotos:
        return
    if isinstance(fotos, str):
        try:
            fotos = json.loads(fotos)
        except Exception:
            return
    if not isinstance(fotos, list):
        return
    try:
        with get_db() as conn:
            for f in fotos[:30]:  # teto de sanidade por visita
                if not isinstance(f, dict):
                    continue
                data = str(f.get('data') or '')
                if not data.startswith('data:image/'):
                    continue
                cat = f.get('categoria') or 'ambiente'
                if cat not in ('ambiente', 'atividade', 'equipamentos'):
                    cat = 'ambiente'
                conn.execute(
                    'INSERT INTO visita_fotos (visita_id, categoria, data, legenda) VALUES (?,?,?,?)',
                    (visita_id, cat, data, (f.get('legenda') or '')[:300]))
    except Exception:
        import traceback; traceback.print_exc()


def _fmt_data(iso: str) -> str:
    try:
        d = datetime.strptime(iso, '%Y-%m-%d')
        meses = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
        dias  = ['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo']
        return f'{dias[d.weekday()]}, {d.day} de {meses[d.month-1]}. de {d.year}'
    except Exception:
        return iso
