# -*- coding: utf-8 -*-
"""Endpoints REST do modulo Controle de Medicoes e Amostradores."""
import io
import os
import re
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
from flask import Blueprint, request, jsonify, send_file, redirect, url_for, render_template_string

# Fuso horário oficial do Brasil (sem horário de verão desde 2019) = UTC-3.
# O servidor (Railway) roda em UTC; isto converte para horário de Brasília.
_BRT = timezone(timedelta(hours=-3))

def agora_brt():
    """datetime atual no fuso de Brasília (UTC-3), naive (sem tzinfo)."""
    return datetime.now(timezone.utc).astimezone(_BRT).replace(tzinfo=None)
from flask_login import login_required, current_user

from .db import (
    USE_PG,
    normalizar_status_amostrador, STATUS_AMOSTRADOR, STATUS_AMOSTRADOR_LABEL,
    get_db, init_db, row_to_dict, list_amostradores, list_demandas,
    get_demanda_completa, upsert_empresa, stats_dashboard,
    registrar_sync, list_sync_log,
    list_demandas_por_empresa, get_empresa_demandas, get_empresa_painel,
    list_amostradores_vencendo, contar_vencendo,
    mesclar_empresas_duplicatas,
    list_raw_tasks, stats_raw_pipeline,
    list_operational_demands, list_operational_por_empresa, list_contatos_empresa,
    # Planejamento + Visitas
    criar_planejamento, get_planejamento, list_planejamentos, update_planejamento_status,
    atualizar_planejamento,
    criar_visita, get_visita, list_visitas, concluir_visita,
    registrar_evento,
)
from .import_xlsx import importar_amostradores, importar_medicoes, importar_demandas_planner

controle_bp = Blueprint('controle', __name__, url_prefix='/controle')


def _mte_do_tecnico(nome):
    """Pré-preenche o nº MTE do documento a partir do nome do técnico: procura
    em usuarios E no catálogo de TSTs (tecnicos_mte) — quem não tem login,
    como boa parte do time de medições, só é achado pelo catálogo. Nome que
    não casa com ninguém volta VAZIO (só cai no usuário logado se o documento
    for dele): melhor sair sem registro que sair com registro de outra pessoa."""
    nome = (nome or '').strip()
    if nome:
        try:
            from .db import mte_por_nome
            mte = mte_por_nome(nome)
            if mte:
                return mte
        except Exception as e:
            print(f'[_mte_do_tecnico] {e}')
    # Fallback no usuário logado SÓ quando o documento é dele (ou o nome veio
    # vazio). Antes valia para qualquer nome: técnico sem registro cadastrado
    # saía no documento com o MTE de quem estava logado.
    if current_user.is_authenticated:
        proprio = not nome
        if not proprio:
            try:
                from .db import _mesma_pessoa
                proprio = _mesma_pessoa(nome, getattr(current_user, 'nome', '') or '')
            except Exception:
                proprio = False
        if proprio:
            return (getattr(current_user, 'registro_mte', '') or '').strip()
    return ''


def _int_arg(nome, default=None):
    """Lê um query-param como int, sem quebrar em valor inválido."""
    v = request.args.get(nome)
    if v is None or v == '':
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _iso_br(s):
    """Converte data ISO (yyyy-mm-dd, com ou sem hora) para dd/mm/aaaa.
    Já-BR ou vazio passam intactos. Usado para nunca vazar ISO ao cliente."""
    s = (s or '').strip()
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        return f'{s[8:10]}/{s[5:7]}/{s[0:4]}'
    return s


def _parse_intervalos_min(s):
    """Soma intervalos em minutos. Aceita 'mm', 'hh:mm' e vários separados por
    vírgula/; (ex.: '0:30, 15'). Espelha o cálculo da tela do wizard — antes o
    backend fazia int(float('1:30')) e estourava, NÃO descontava o intervalo,
    e o t/Volume do PDF saíam maiores que na tela."""
    total = 0
    for tok in str(s or '').replace(';', ',').split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            if ':' in tok:
                h, m = tok.split(':')[:2]
                total += int(h) * 60 + int(m)
            else:
                total += int(round(float(tok.replace(',', '.'))))
        except Exception:
            pass
    return total


def _sanitize_rl(obj):
    """Escapa & < > em TODO texto de usuário (recursivo) antes de ir pro PDF.
    O reportlab trata o conteúdo do Paragraph como mini-HTML — um nome/campo
    com '<', '&' ou '<b>' quebrava o laudo (HTTP 500) ou injetava marcação.
    A marcação própria do gerador (<b>, <font>) é adicionada no código, em
    torno destes valores já escapados, então continua funcionando."""
    if isinstance(obj, str):
        return obj.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if isinstance(obj, dict):
        return {k: _sanitize_rl(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_rl(v) for v in obj]
    return obj


def _qint(name, default, maximo=None):
    """Inteiro da query string com fallback seguro — evita HTTP 500 com ?x=abc."""
    try:
        v = int(request.args.get(name, default))
    except (TypeError, ValueError):
        v = default
    return min(v, maximo) if maximo is not None else v


# Ferramentas de manutenção/diagnóstico e endpoints de debug: só admin (regra 9).
# O frontend já esconde os botões de não-admin, mas a trava real tem de ser aqui —
# senão um técnico chama o endpoint direto e altera/apaga dados operacionais.
# Lista explícita para NÃO bloquear leituras do dashboard (ex.: /graph/status).
_ADMIN_ONLY_EXACT = frozenset({
    '/controle/graph/sync', '/controle/graph/lab_sync',
    '/controle/graph/ra_backfill', '/controle/graph/ra_backfill_preview',
    '/controle/graph/ra_backfill_result',
    '/controle/graph/test_mail', '/controle/graph/users',
    '/controle/amostradores/concluir-utilizados',
    '/controle/amostradores/diagnostico',
    '/controle/amostradores/fix_data_entrada',
    '/controle/amostradores/normalizar_status',
    '/controle/amostradores/reconciliar_status',
    '/controle/empresas/mesclar_duplicatas',
    '/controle/empresas/suspeitas',
    '/controle/demandas/limpar-fantasmas',
    '/controle/demandas/match-empresas',
    '/controle/demandas/reclassificar',
    '/controle/demandas/re-extrair',
    '/controle/coletas/dedup',
    '/controle/equipamentos/rebuild-frota',
    '/controle/amostradores/sincronizar-datas-laudo',
})


def _is_admin_only(path):
    if path.startswith('/controle/admin') or path.startswith('/controle/reset'):
        return True
    if path.startswith('/controle/import/') or path.startswith('/controle/graph/debug'):
        return True
    if path in _ADMIN_ONLY_EXACT:
        return True
    # /controle/empresas/<id>/excluir-fantasma
    if path.startswith('/controle/empresas/') and path.endswith('/excluir-fantasma'):
        return True
    return False


@controle_bp.before_request
def _require_login():
    # Webhook server-to-server (CRM Comercial → OS de medição no Planner):
    # autentica pelo segredo compartilhado no próprio handler, NÃO por sessão
    # de usuário. Precisa ser isento aqui senão o gate de login barra (401).
    if request.path == '/controle/planner/criar-os':
        return None
    # Orquestrador da OS: rotas /controle/os/* aceitam server-to-server pelo
    # mesmo segredo (o handler valida); sem segredo válido, exigem sessão.
    if request.path.startswith('/controle/os/'):
        _seg = os.environ.get('CRM_PLANNER_SECRET', '')
        if _seg and request.headers.get('x-crm-secret') == _seg:
            return None
    # Toda rota do controle exige login — leitura e escrita.
    # Dados operacionais (empresas, OS, coletas) não são públicos.
    if not current_user.is_authenticated:
        return jsonify({'erro': 'Login necessário', 'redirect': '/auth/login'}), 401
    # Manutenção/diagnóstico/debug exigem role admin
    if _is_admin_only(request.path):
        if getattr(current_user, 'role', '') != 'admin':
            return jsonify({'erro': 'Apenas administradores'}), 403


# ── Dashboard ─────────────────────────────────────────────────────────
@controle_bp.route('/stats')
def stats():
    init_db()
    d = stats_dashboard()
    # Adiciona contagem de alertas ativos para badge na sidebar
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM alertas_operacionais WHERE status='ativo'"
            ).fetchone()
            d['alertas_ativos'] = row['c'] if row else 0
    except Exception:
        d['alertas_ativos'] = 0
    # Medições realizadas por tipo de agente (NR-15) — contagem absoluta das coletas
    # feitas pelo sistema. Alimenta a tabela "Agentes · NR-15" da home.
    try:
        with get_db() as conn:
            def _cnt(sql):
                try:
                    r = conn.execute(sql).fetchone()
                    return (row_to_dict(r).get('c', 0) if r else 0) or 0
                except Exception:
                    return 0
            d['agentes_medidos'] = {
                'ruido':    _cnt("SELECT COUNT(*) AS c FROM coletas_ruido"),
                'quimico':  _cnt("SELECT COUNT(*) AS c FROM coletas_quimico"),
                'calor':    _cnt("SELECT COUNT(*) AS c FROM coletas_outros WHERE tipo='calor'"),
                'vibracao': _cnt("SELECT COUNT(*) AS c FROM coletas_outros WHERE tipo LIKE 'vibracao%'"),
            }
    except Exception:
        d['agentes_medidos'] = {'ruido': 0, 'quimico': 0, 'calor': 0, 'vibracao': 0}
    return jsonify(d)


@controle_bp.route('/produtividade/tecnicos')
def produtividade_tecnicos():
    """Produtividade por técnico contada POR MEDIÇÃO (coleta finalizada).
    Cada coleta de ruído/químico/outros é atribuída a quem a finalizou
    (coletas_*.tecnico_login). Alimenta o painel 'Produtividade por Técnico'."""
    init_db()
    from .db import produtividade_por_tecnico
    try:
        return jsonify(produtividade_por_tecnico(request.args.get('de'), request.args.get('ate')))
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/amostradores/analytics')
def amostradores_analytics():
    """Analytics operacional dos amostradores por status (TASK C).
    Contagem por status, tempos médios (coleta→lab, lab→concluído) e gargalos.
    Derivado dos timestamps reais → reflete cada mudança de status."""
    init_db()
    from .db import stats_amostradores_fluxo
    try:
        return jsonify(stats_amostradores_fluxo())
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ── Importacao ────────────────────────────────────────────────────────
@controle_bp.route('/import/amostradores', methods=['POST'])
def import_amostr():
    init_db()
    f = request.files.get('file')
    user = request.form.get('user', 'Matheus')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo'}), 400
    try:
        nome_arq = f.filename or 'arquivo.xlsx'
        res = importar_amostradores(f.read())
        if res.get('sheets_reconhecidas', 0) == 0:
            return jsonify({'erro': 'Arquivo não reconhecido: nenhuma aba tem o cabeçalho '
                            'de amostradores (Status / Tipo de Amostrador / Código). '
                            'Confira se é a planilha certa.'}), 400
        registrar_sync('amostradores', nome_arq, res.get('inserted', 0), res.get('updated', 0), user)
        return jsonify({'ok': True, **res})
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/import/medicoes', methods=['POST'])
def import_med():
    init_db()
    f = request.files.get('file')
    user = request.form.get('user', 'Matheus')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo'}), 400
    try:
        nome_arq = f.filename or 'arquivo.xlsx'
        res = importar_medicoes(f.read())
        if res.get('sheets_reconhecidas', 0) == 0:
            return jsonify({'erro': 'Arquivo não reconhecido: nenhuma aba tem o cabeçalho '
                            'de medições (Unidade / Agente). Confira se é a planilha certa.'}), 400
        registrar_sync('medicoes', nome_arq, res.get('medicoes_inseridas', 0), 0, user)
        return jsonify({'ok': True, **res})
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/import/planner', methods=['POST'])
def import_planner():
    """Importa export do Microsoft Planner (Demandas_Medicoes_*.xlsx)."""
    init_db()
    f = request.files.get('file')
    user = request.form.get('user', 'Matheus')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo'}), 400
    try:
        nome_arq = f.filename or 'arquivo.xlsx'
        res = importar_demandas_planner(f.read())
        if res.get('sheets_reconhecidas', 0) == 0:
            return jsonify({'erro': 'Arquivo não reconhecido: nenhuma aba tem o cabeçalho '
                            'do export do Planner (Nome/Empresa + Criação/Prazo/Status). '
                            'Confira se é a planilha certa.'}), 400
        registrar_sync('planner', nome_arq, res.get('demandas_inseridas', 0), res.get('demandas_atualizadas', 0), user)
        return jsonify({'ok': True, **res})
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/sync_log')
def get_sync_log():
    """Retorna historico das ultimas importacoes."""
    init_db()
    return jsonify(list_sync_log(limit=_qint('limit', 20)))


# ── Handoff CRM Comercial → OS de medição no Planner ───────────────────
# Chamado server-to-server pela edge function do CRM (criar-os-planner) quando
# um negócio é GANHO com produtos de medição. Cria uma tarefa "A ABRIR" no
# plano Entregas Técnicas com o label MEDIÇÕES; o sync de 15 min a ingere como
# demanda (empresa casada pelo título; agentes extraídos da descrição).
# Sem número de OS: a OS nasce depois no SOC/financeiro (fluxo do MAESTRO) —
# o técnico preenche o nº no título quando a OS for aberta.
# Auth: header x-crm-secret == env CRM_PLANNER_SECRET (isento do login, ver
# _require_login acima). dry_run=True devolve o que criaria SEM tocar no Planner.

def _compor_descricao_os(dados, destino='medicao'):
    """Monta a descrição (Notas) da task no formato que o parser do portal lê.
    Medição: os nomes dos itens vão como texto — o motor extrair_agentes_multifonte
    reconhece Ruído/Calor/Vibração/químicos deles. Engenharia: lista os serviços/
    documentos a entregar + os labels aplicados (a task NÃO é lida pelo sync de
    medições, que filtra só o label MEDIÇÕES — vive só no Planner p/ a engenharia)."""
    linhas = ['⚠ OS A ABRIR — demanda criada automaticamente pelo CRM Comercial',
              '=' * 48]
    if dados.get('empresa'):
        linhas.append(f"Empresa: {dados['empresa']}")
    if dados.get('cnpj'):
        linhas.append(f"CNPJ: {dados['cnpj']}")
    if dados.get('negocio_crm_id'):
        tit = dados.get('negocio_titulo') or ''
        linhas.append(f"Negócio (CRM): #{dados['negocio_crm_id']}" + (f" — {tit}" if tit else ''))
    if dados.get('consultor'):
        linhas.append(f"Consultor(a): {dados['consultor']}")
    if dados.get('fechado_em'):
        linhas.append(f"Fechado em: {dados['fechado_em']}")
    if destino == 'engenharia':
        linhas += ['', 'SERVIÇOS DE ENGENHARIA A ENTREGAR:']
        vazio = '- (nenhum serviço informado)'
    else:
        linhas += ['', 'AGENTES / MEDIÇÕES A REALIZAR:']
        vazio = '- (nenhum item de medição informado)'
    itens = dados.get('itens') or []
    algum = False
    for it in itens:
        nome = (it.get('nome') or '').strip()
        if not nome:
            continue
        algum = True
        try:
            qn = float(it.get('quantidade'))
        except (TypeError, ValueError):
            qn = None
        suf = f"  (x{int(qn)})" if qn and qn > 1 else ''
        linhas.append(f"- {nome}{suf}")
    if not algum:
        linhas.append(vazio)
    if dados.get('labels_aplicados'):
        linhas += ['', 'Labels (Planner): ' + ', '.join(dados['labels_aplicados'])]
    if dados.get('obs'):
        linhas += ['', f"Obs: {dados['obs']}"]
    linhas += ['',
               '➡ PREENCHER O NÚMERO DA OS no título desta tarefa quando o SOC/financeiro abrir a OS.',
               '(gerada pelo CRM — não digitar no Planner à mão)']
    return '\n'.join(linhas)


@controle_bp.route('/planner/criar-os', methods=['POST'])
def planner_criar_os():
    # Auth por segredo compartilhado (server-to-server, sem sessão).
    segredo = os.environ.get('CRM_PLANNER_SECRET', '')
    if not segredo or request.headers.get('x-crm-secret') != segredo:
        return jsonify({'ok': False, 'erro': 'não autorizado'}), 401

    body = request.get_json(silent=True) or {}
    empresa = (body.get('empresa') or '').strip()
    if not empresa:
        return jsonify({'ok': False, 'erro': 'empresa obrigatória'}), 400

    # destino: 'medicao' (default, comportamento original) ou 'engenharia'
    destino = (body.get('destino') or 'medicao').strip().lower()
    if destino in ('medição', 'medicoes', 'medições'):
        destino = 'medicao'
    if destino not in ('medicao', 'engenharia'):
        return jsonify({'ok': False, 'erro': "destino inválido (use 'medicao' ou 'engenharia')"}), 400

    dry_run = bool(body.get('dry_run'))
    dados = {
        'empresa':        empresa,
        'cnpj':           (body.get('cnpj') or '').strip(),
        'negocio_crm_id': body.get('negocio_crm_id'),
        'negocio_titulo': (body.get('negocio_titulo') or '').strip(),
        'consultor':      (body.get('consultor') or '').strip(),
        'fechado_em':     (body.get('fechado_em') or '').strip(),
        'itens':          body.get('itens') or [],
        'obs':            (body.get('obs') or '').strip(),
    }

    titulo = f"A ABRIR - {empresa}"[:255]

    from .graph import (graph_ok, criar_planner_task, set_task_description,
                        get_medicoes_category_id, get_category_ids_by_names,
                        get_bucket_id_by_name, get_plan_category_map,
                        PLAN_ENTREGAS_TECNICAS, BUCKET_ENG_NOVAS_DEMANDAS)
    plan_id = PLAN_ENTREGAS_TECNICAS

    if not graph_ok():
        return jsonify({'ok': False, 'erro': 'Graph não configurado no portal'}), 503

    bucket_id = None
    if destino == 'engenharia':
        # Labels explícitos do CRM (nomes dos labels do Planner) ou derivados dos itens.
        nomes = body.get('labels') or [ (it.get('nome') or '') for it in dados['itens'] ]
        applied = get_category_ids_by_names(plan_id, nomes)
        if not applied:
            return jsonify({'ok': False, 'erro':
                'nenhum label de engenharia reconhecido — envie "labels" '
                '(ex.: ["PGR/PCMSO","TREINAMENTO","LTCAT","PPR","LIP","PCMSO","RELATÓRIO TÉCNICO"])'
            }), 400
        bucket_id = get_bucket_id_by_name(plan_id, 'Engenharia - Novas Demandas',
                                          BUCKET_ENG_NOVAS_DEMANDAS)
        cat_map = get_plan_category_map(plan_id) or {}
        dados['labels_aplicados'] = [cat_map.get(cid, cid) for cid in applied]
    else:
        categoria_id = get_medicoes_category_id(plan_id)
        applied = {categoria_id: True}
        dados['labels_aplicados'] = ['MEDIÇÕES']

    descricao = _compor_descricao_os(dados, destino=destino)

    if dry_run:
        return jsonify({'ok': True, 'dry_run': True, 'destino': destino,
                        'plan_id': plan_id, 'labels': applied,
                        'labels_nomes': dados['labels_aplicados'],
                        'bucket_id': bucket_id, 'titulo': titulo,
                        'descricao': descricao})

    try:
        task = criar_planner_task(plan_id, titulo,
                                  applied_categories=applied, bucket_id=bucket_id)
        task_id = task.get('id')
        desc_ok = False
        try:
            desc_ok = set_task_description(task_id, descricao)
        except Exception as e:
            log.warning('[criar-os] descrição falhou task %s: %s', task_id, e)
        # evento no feed operacional para rastreio
        try:
            registrar_evento('os_criada_crm',
                             f'OS A ABRIR ({destino}) criada pelo CRM: {empresa[:80]}',
                             ref_tipo='demanda')
        except Exception:
            pass
        return jsonify({'ok': True, 'dry_run': False, 'destino': destino,
                        'task_id': task_id, 'titulo': titulo,
                        'labels': applied, 'labels_nomes': dados['labels_aplicados'],
                        'bucket_id': bucket_id, 'descricao_ok': desc_ok})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'erro': str(e)}), 500


# ── Auditoria ──────────────────────────────────────────────────────────
@controle_bp.route('/eventos')
@login_required
def get_eventos():
    init_db()
    limit = _qint('limit', 100, 500)
    tipo = request.args.get('tipo')
    with get_db() as conn:
        sql = 'SELECT * FROM eventos WHERE 1=1'
        params = []
        if tipo:
            sql += ' AND tipo=?'; params.append(tipo)
        sql += f' ORDER BY criado_em DESC LIMIT {limit}'
        rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    return jsonify(rows)


# ── Amostradores ──────────────────────────────────────────────────────
@controle_bp.route('/amostradores')
def get_amostradores():
    init_db()
    return jsonify(list_amostradores(request.args.to_dict()))


@controle_bp.route('/amostradores/arquivar', methods=['POST'])
def amostradores_arquivar():
    """Arquiva amostradores concluídos há >=30 dias (TASK D).
    Roda automaticamente ao listar; este endpoint força a passada e
    retorna quantos foram arquivados. Histórico: GET /amostradores?arquivados=1"""
    init_db()
    from .db import arquivar_amostradores_concluidos
    try:
        d = request.json or {}
        dias = int(d.get('dias', 30))
        n = arquivar_amostradores_concluidos(dias)
        return jsonify({'ok': True, 'arquivados': n, 'dias': dias})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@controle_bp.route('/amostradores', methods=['POST'])
def cria_amostrador():
    init_db()
    d = request.json or {}
    codigo = (str(d.get('codigo') or '')).strip().upper()
    if not codigo or not d.get('tipo'):
        return jsonify({'erro': 'codigo e tipo obrigatorios'}), 400
    with get_db() as conn:
        # trava: não cadastrar amostrador que já existe (mesmo código, qualquer status)
        ja = conn.execute('SELECT id, status FROM amostradores WHERE UPPER(codigo)=?',
                          (codigo,)).fetchone()
        if ja:
            jad = row_to_dict(ja)
            return jsonify({'erro': f'Amostrador {codigo} já está cadastrado '
                                    f'(status atual: {jad.get("status", "?")}).',
                            'duplicado': True, 'id': jad.get('id')}), 409
        cur = conn.execute("""
            INSERT INTO amostradores (codigo, tipo, status, data_entrada, observacao)
            VALUES (?, ?, ?, ?, ?)""",
            (codigo, d['tipo'], normalizar_status_amostrador(d.get('status', 'disponivel')),
             d.get('data_entrada', datetime.now().strftime('%Y-%m-%d')),
             d.get('observacao', '')))
        novo_id = cur.lastrowid
    registrar_evento('amostrador_criado', f'{codigo} ({d["tipo"]})',
                     novo_id, 'amostrador',
                     current_user.nome if current_user.is_authenticated else 'sistema',
                     request.remote_addr)
    return jsonify({'ok': True, 'id': novo_id})


@controle_bp.route('/amostradores/lote', methods=['POST'])
def cria_amostradores_lote():
    """Cria vários amostradores de uma vez (cadastro em série).
    Ignora códigos que já existem. Body: {tipo, codigos:[...], status,
    data_entrada, observacao, auto_tipo}.
    Se auto_tipo=True (colar lista do e-mail do lab), o tipo de cada código é
    detectado pelo prefixo de letras (ex: TCP4924AV3→TCP, EC93893A→EC,
    PVC99V31→PVC, FVPH2181→FVPH); o campo `tipo` vira fallback."""
    init_db()
    d = request.json or {}
    tipo = (d.get('tipo') or '').strip().upper()
    auto_tipo = bool(d.get('auto_tipo'))
    codigos = d.get('codigos') or []
    if not tipo and not auto_tipo:
        return jsonify({'erro': 'tipo obrigatorio'}), 400
    # normaliza, tira vazios e duplicados mantendo ordem
    vistos, limpos = set(), []
    for c in codigos:
        c = (str(c) or '').strip().upper()
        if c and c not in vistos:
            vistos.add(c); limpos.append(c)
    if not limpos:
        return jsonify({'erro': 'nenhum codigo valido'}), 400
    status = normalizar_status_amostrador(d.get('status', 'disponivel'))
    data_entrada = d.get('data_entrada') or datetime.now().strftime('%Y-%m-%d')
    obs = d.get('observacao', '')

    def _tipo_do_codigo(cod):
        # prefixo = letras iniciais antes do primeiro dígito (ex: FVPH2181→FVPH)
        m = re.match(r'^([A-Z]+)', cod)
        return (m.group(1) if m else '') or tipo or 'AMOSTRADOR'

    criados, ignorados = 0, 0
    tipos_usados = set()
    with get_db() as conn:
        # códigos já existentes (qualquer status)
        existentes = {r['codigo'] for r in conn.execute(
            'SELECT codigo FROM amostradores').fetchall() if r['codigo']}
        for c in limpos:
            if c in existentes:
                ignorados += 1
                continue
            t = _tipo_do_codigo(c) if auto_tipo else tipo
            conn.execute("""
                INSERT INTO amostradores (codigo, tipo, status, data_entrada, observacao)
                VALUES (?, ?, ?, ?, ?)""",
                (c, t, status, data_entrada, obs))
            existentes.add(c)
            criados += 1
            tipos_usados.add(t)
    if criados:
        desc_tipo = (', '.join(sorted(tipos_usados)) if auto_tipo else tipo)
        registrar_evento('amostrador_criado',
                         f'{criados} em série ({desc_tipo})', None, 'amostrador',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
    return jsonify({'ok': True, 'criados': criados, 'ignorados': ignorados})


@controle_bp.route('/amostradores/<int:aid>', methods=['PUT'])
def update_amostrador(aid):
    init_db()
    d = request.json or {}
    fields = []
    params = []
    for k in ('status', 'tipo', 'codigo', 'avaliador', 'data_medicao', 'observacao', 'data_entrada', 'data_resultado'):
        if k in d:
            val = normalizar_status_amostrador(d[k]) if k == 'status' else d[k]
            fields.append(f'{k}=?'); params.append(val)
            # Ao concluir manualmente, carimba a data de conclusão (cert recebido)
            if k == 'status' and val == 'concluido':
                fields.append('data_conclusao=COALESCE(data_conclusao, ?)')
                params.append(datetime.now().strftime('%Y-%m-%d'))
    if 'empresa' in d:
        emp_id = upsert_empresa('', d['empresa']) if d['empresa'] else None
        fields.append('empresa_id=?'); params.append(emp_id)
    if not fields:
        return jsonify({'erro': 'nada para atualizar'}), 400
    fields.append('atualizado_em=CURRENT_TIMESTAMP')
    params.append(aid)
    with get_db() as conn:
        conn.execute(f'UPDATE amostradores SET {",".join(fields)} WHERE id=?', params)
    desc = '; '.join(f'{k}={d[k]}' for k in d if k in ('status','codigo','tipo','avaliador','data_medicao','empresa','observacao'))
    registrar_evento('amostrador_atualizado', f'id={aid} {desc}',
                     aid, 'amostrador',
                     current_user.nome if current_user.is_authenticated else 'sistema',
                     request.remote_addr)
    return jsonify({'ok': True})


@controle_bp.route('/amostradores/<int:aid>', methods=['DELETE'])
def delete_amostrador(aid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM amostradores WHERE id=?', (aid,))
    return jsonify({'ok': True})


@controle_bp.route('/amostradores/baixa_simples', methods=['POST'])
def baixa_simples_lote():
    """Baixa rápida em lote: sem bomba/vazão. Atualiza status para Laboratorio + dados básicos."""
    init_db()
    d = request.json or {}
    ids = [int(i) for i in d.get('ids', []) if str(i).isdigit()]
    if not ids:
        return jsonify({'erro': 'Nenhum amostrador selecionado'}), 400

    empresa_nome = (d.get('empresa_nome') or '').strip()
    avaliador    = d.get('avaliador', '')
    data_med     = d.get('data_medicao') or datetime.now().strftime('%Y-%m-%d')
    obs          = d.get('observacao', '')

    empresa_id = None
    empresa_nao_encontrada = False
    if empresa_nome:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM empresas WHERE lower(nome) LIKE lower(?) "
                "ORDER BY length(nome) LIMIT 1",
                (f'%{empresa_nome}%',)
            ).fetchone()
            if row:
                empresa_id = row['id']
            else:
                empresa_nao_encontrada = True

    ph = ','.join(['?'] * len(ids))
    obs_final = obs or (f'Baixa rápida — {empresa_nome}' if empresa_nome else 'Baixa rápida')

    with get_db() as conn:
        # data_envio_lab não é carimbada aqui (fix 03/07/2026): a data real de envio
        # vem do lab sync / lançamento manual — carimbar zerava a métrica coleta→lab.
        # Conta os elegíveis (disponivel/reservado) — o COUNT antigo somava TODOS
        # os ids selecionados, inflando o "afetados".
        afetados = conn.execute(
            f"SELECT COUNT(*) AS c FROM amostradores "
            f"WHERE id IN ({ph}) AND status IN ('disponivel','reservado')", ids
        ).fetchone()['c']
        conn.execute(
            f"""UPDATE amostradores
                SET status='laboratorio', avaliador=?, data_medicao=?,
                    observacao=?, empresa_id=COALESCE(?,empresa_id),
                    atualizado_em=CURRENT_TIMESTAMP
                WHERE id IN ({ph}) AND status IN ('disponivel','reservado')""",
            [avaliador, data_med, obs_final, empresa_id] + ids
        )

    user = getattr(current_user, 'email', 'sistema') if current_user.is_authenticated else 'sistema'
    for aid in ids:
        registrar_evento('amostrador_atualizado', f'Baixa rápida — {empresa_nome or "s/empresa"}',
                         aid, 'amostrador', user)
    resp = {'ok': True, 'afetados': afetados}
    if empresa_nao_encontrada:
        resp['aviso'] = f'Empresa "{empresa_nome}" não encontrada — baixa sem vincular empresa.'
    return jsonify(resp)


@controle_bp.route('/amostradores/concluir', methods=['POST'])
def concluir_amostradores():
    """Marca amostradores como Concluido. Só precisa do nome da empresa."""
    init_db()
    d = request.json or {}
    ids = [int(i) for i in d.get('ids', []) if str(i).isdigit()]
    if not ids:
        return jsonify({'erro': 'Nenhum amostrador selecionado'}), 400

    empresa_nome = (d.get('empresa_nome') or '').strip()
    obs          = d.get('observacao', '')

    empresa_id = None
    empresa_nao_encontrada = False
    if empresa_nome:
        with get_db() as conn:
            # ORDER BY length(nome): entre vários que contêm o texto, pega o nome
            # mais curto (mais próximo do buscado) em vez de um arbitrário.
            row = conn.execute(
                "SELECT id FROM empresas WHERE lower(nome) LIKE lower(?) "
                "ORDER BY length(nome) LIMIT 1",
                (f'%{empresa_nome}%',)
            ).fetchone()
            if row:
                empresa_id = row['id']
            else:
                empresa_nao_encontrada = True

    ph = ','.join(['?'] * len(ids))
    obs_final = obs or (f'Concluído — {empresa_nome}' if empresa_nome else 'Concluído')

    # Guarda: não re-concluir nem "concluir" um descartado. Conta os REALMENTE
    # afetados (elegíveis) em vez de len(ids), que fingia sucesso total.
    with get_db() as conn:
        afetados = conn.execute(
            f"SELECT COUNT(*) AS c FROM amostradores "
            f"WHERE id IN ({ph}) AND status NOT IN ('concluido','descartado')",
            ids
        ).fetchone()['c']
        conn.execute(
            f"""UPDATE amostradores
                SET status='concluido', observacao=?,
                    data_conclusao=COALESCE(data_conclusao, ?),
                    empresa_id=COALESCE(?,empresa_id),
                    atualizado_em=CURRENT_TIMESTAMP
                WHERE id IN ({ph})
                  AND status NOT IN ('concluido','descartado')""",
            [obs_final, datetime.now().strftime('%Y-%m-%d'), empresa_id] + ids
        )

    user = getattr(current_user, 'email', 'sistema') if current_user.is_authenticated else 'sistema'
    for aid in ids:
        registrar_evento('amostrador_atualizado', f'Concluído — {empresa_nome or "s/empresa"}',
                         aid, 'amostrador', user)
    resp = {'ok': True, 'afetados': afetados}
    if empresa_nao_encontrada:
        resp['aviso'] = f'Empresa "{empresa_nome}" não encontrada — concluído sem vincular empresa.'
    return jsonify(resp)


@controle_bp.route('/amostradores/concluir-utilizados', methods=['POST'])
def concluir_amostradores_utilizados():
    """Marca como CONCLUÍDO todo amostrador cujo código aparece numa cadeia de
    custódia (= foi utilizado). Regra: tem cadeia → foi usado → concluído.
    Os códigos usados vêm de controle/cadeia_usados.py (extraído das 854 cadeias)."""
    init_db()
    import re as _re
    try:
        # Lista dinâmica: cadeias históricas + planilhas químicas do sistema
        from .cadeia_usados import codigos_usados
        USADOS = codigos_usados()
    except Exception as e:
        return jsonify({'erro': f'Lista de utilizados indisponível: {e}'}), 500

    def _norm(c):
        return _re.sub(r'\s+', '', str(c or '')).upper()

    alvo, ja, com_cadeia = [], 0, 0
    with get_db() as conn:
        for r in conn.execute('SELECT id, codigo, tipo, status FROM amostradores').fetchall():
            d = row_to_dict(r)
            cod  = _norm(d.get('codigo'))
            tipo = _norm(d.get('tipo'))
            # No sistema o código pode estar só o sufixo (15T47) com tipo à parte (PVC),
            # ou já completo (PVC15T47). Nas cadeias é sempre completo → testa as duas formas.
            candidatos = {cod, tipo + cod}
            if candidatos & USADOS:
                com_cadeia += 1
                if d.get('status') == 'concluido':
                    ja += 1
                else:
                    alvo.append(d['id'])
        if alvo:
            ph = ','.join(['?'] * len(alvo))
            conn.execute(
                f"""UPDATE amostradores
                    SET status='concluido',
                        data_conclusao=COALESCE(data_conclusao, ?),
                        observacao=COALESCE(NULLIF(observacao, ''), 'Concluído — consta em cadeia de custódia'),
                        atualizado_em=CURRENT_TIMESTAMP
                    WHERE id IN ({ph})""",
                [datetime.now().strftime('%Y-%m-%d')] + alvo
            )
    registrar_evento('amostrador_atualizado',
                     f'{len(alvo)} amostradores concluídos (constam em cadeia de custódia)',
                     None, 'amostrador',
                     current_user.nome if current_user.is_authenticated else 'sistema',
                     request.remote_addr)
    return jsonify({'ok': True, 'concluidos': len(alvo), 'ja_concluidos': ja,
                    'com_cadeia': com_cadeia, 'base_usados': len(USADOS)})


@controle_bp.route('/amostradores/diagnostico')
def amostradores_diagnostico():
    """Destrincha estoque/lab para entender os números:
    - disponíveis COM cadeia (já usados → deveriam estar concluídos) vs sem cadeia
    - por idade (data_entrada): novos vs parados há muito tempo
    - lab por idade / sem data de envio."""
    init_db()
    import re as _re
    from datetime import date as _date, datetime as _dt
    try:
        from .cadeia_usados import codigos_usados
        USADOS = codigos_usados()
    except Exception:
        USADOS = frozenset()

    def _norm(c):
        return _re.sub(r'\s+', '', str(c or '')).upper()

    hoje = _date.today()
    def _idade(v):
        s = str(v or '')[:10]
        try:
            return (hoje - _dt.strptime(s, '%Y-%m-%d').date()).days
        except Exception:
            return None

    disp = {'total': 0, 'com_cadeia': 0, 'sem_cadeia': 0,
            'ate_30': 0, 'd30_180': 0, 'mais_180': 0, 'sem_data': 0, 'exemplos_com_cadeia': []}
    lab = {'total': 0, 'sem_data_envio': 0, 'ate_30': 0, 'd30_180': 0, 'mais_180': 0}

    with get_db() as conn:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT codigo, tipo, status, data_entrada, data_envio_lab "
            "FROM amostradores WHERE COALESCE(arquivado,0)=0").fetchall()]

    for d in rows:
        st = (d.get('status') or '').lower()
        if st == 'disponivel':
            disp['total'] += 1
            cod, tipo = _norm(d.get('codigo')), _norm(d.get('tipo'))
            if cod in USADOS or (tipo + cod) in USADOS:
                disp['com_cadeia'] += 1
                if len(disp['exemplos_com_cadeia']) < 20:
                    disp['exemplos_com_cadeia'].append(f"{d.get('tipo','')} {d.get('codigo','')}".strip())
            else:
                disp['sem_cadeia'] += 1
            idade = _idade(d.get('data_entrada'))
            if idade is None:   disp['sem_data'] += 1
            elif idade <= 30:   disp['ate_30'] += 1
            elif idade <= 180:  disp['d30_180'] += 1
            else:               disp['mais_180'] += 1
        elif st == 'laboratorio':
            lab['total'] += 1
            dl = str(d.get('data_envio_lab') or '')
            if not _re.match(r'^\d{4}-\d{2}-\d{2}', dl):
                lab['sem_data_envio'] += 1
            else:
                idade = _idade(dl)
                if idade is None:   pass
                elif idade <= 30:   lab['ate_30'] += 1
                elif idade <= 180:  lab['d30_180'] += 1
                else:               lab['mais_180'] += 1

    return jsonify({'disponivel': disp, 'laboratorio': lab, 'base_usados': len(USADOS)})


# ── Manutencao / bulk updates ─────────────────────────────────────────
@controle_bp.route('/amostradores/fix_data_entrada', methods=['POST'])
def fix_data_entrada():
    """Atualiza data_entrada de amostradores com muitos dias parados."""
    init_db()
    d = request.json or {}
    nova_data  = d.get('data_entrada', '2025-10-23')  # default: 23/10/2025
    dias_min   = int(d.get('dias_min', 200))
    ids_manual = d.get('ids', [])  # lista de IDs especificos, opcional

    with get_db() as conn:
        if ids_manual:
            placeholders = ','.join('?' * len(ids_manual))
            cur = conn.execute(
                f"UPDATE amostradores SET data_entrada=?, atualizado_em=CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders})",
                [nova_data] + ids_manual
            )
        else:
            cur = conn.execute(
                """UPDATE amostradores SET data_entrada=?, atualizado_em=CURRENT_TIMESTAMP
                   WHERE status IN ('disponivel','reservado')
                     AND data_entrada IS NOT NULL
                     AND CAST(julianday('now') - julianday(data_entrada) AS INTEGER) > ?""",
                (nova_data, dias_min)
            )
        afetados = cur.rowcount
    return jsonify({'ok': True, 'afetados': afetados, 'nova_data': nova_data})


# ── Vencimento (laboratorio cobra apos N dias) ────────────────────────
@controle_bp.route('/amostradores_vencendo')
def get_vencendo():
    init_db()
    try:
        from .lab_inbox import get_pendentes_salvos, get_resultados_salvos, get_sync_result_salvo
        return jsonify({
            'stats': contar_vencendo(),
            'amostradores': list_amostradores_vencendo(),
            'lab_pendentes': get_pendentes_salvos(),
            'lab_resultados': get_resultados_salvos(),
            'lab_sync_result': get_sync_result_salvo()
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({
            'stats': {'vencidos': 0, 'urgente': 0, 'alerta': 0, 'sem_data': 0, 'total_no_lab': 0},
            'amostradores': [], 'erro': str(e)
        })


@controle_bp.route('/amostradores/<int:aid>/envio_lab', methods=['POST'])
def marcar_envio_lab(aid):
    """Registra que o amostrador foi enviado ao laboratorio (inicia contagem)."""
    init_db()
    d = request.json or {}
    data_envio = d.get('data_envio_lab') or datetime.now().strftime('%Y-%m-%d')
    dias       = int(d.get('dias_validade', 45) or 45)
    lote       = d.get('lote', '')
    obs        = d.get('observacao_venc', '')
    with get_db() as conn:
        conn.execute("""
            UPDATE amostradores
            SET data_envio_lab=?, dias_validade=?, lote=?, observacao_venc=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?""",
            (data_envio, dias, lote, obs, aid))
    return jsonify({'ok': True})


@controle_bp.route('/amostradores/envio_lab_lote', methods=['POST'])
def marcar_envio_lab_lote():
    """Registra envio ao lab em lote (varios amostradores de uma vez)."""
    init_db()
    d = request.json or {}
    ids = d.get('ids', [])
    if not ids: return jsonify({'erro': 'sem ids'}), 400
    data_envio = d.get('data_envio_lab') or datetime.now().strftime('%Y-%m-%d')
    dias       = int(d.get('dias_validade', 45) or 45)
    lote       = d.get('lote', '')
    placeholders = ','.join(['?'] * len(ids))
    with get_db() as conn:
        conn.execute(f"""
            UPDATE amostradores
            SET data_envio_lab=?, dias_validade=?, lote=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})""",
            [data_envio, dias, lote] + ids)
    return jsonify({'ok': True, 'afetados': len(ids)})


# ── Demandas ──────────────────────────────────────────────────────────
@controle_bp.route('/demandas')
def get_demandas():
    init_db()
    return jsonify(list_demandas(request.args.to_dict()))


@controle_bp.route('/equipamentos')
def get_equipamentos():
    """Lista inventário de equipamentos. ?tipo=X&status=Y"""
    init_db()
    tipo   = request.args.get('tipo', '').strip()
    status = request.args.get('status', '').strip()
    with get_db() as conn:
        q      = 'SELECT * FROM equipamentos_inventario WHERE 1=1'
        params = []
        if tipo:   q += ' AND tipo=?';   params.append(tipo)
        if status: q += ' AND status=?'; params.append(status)
        q += ' ORDER BY tipo, marca, observacao'
        rows = conn.execute(q, params).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@controle_bp.route('/equipamentos/calibracao')
def get_equipamentos_calibracao():
    """Status de calibração dos equipamentos (validade = data_calibracao + 2 anos).
    ?dias=N define a antecedência do alerta (default 90)."""
    init_db()
    from .db import equipamentos_calibracao
    try:
        dias = int(request.args.get('dias', 90))
    except Exception:
        dias = 90
    return jsonify(equipamentos_calibracao(dias))


@controle_bp.route('/equipamentos/<int:eid>', methods=['PUT'])
def update_equipamento(eid):
    """Atualiza campos de um equipamento (SN, cert, validade, status)."""
    init_db()
    d = request.get_json(force=True) or {}
    allowed = {'numero_serie', 'cert_numero', 'cert_validade', 'status', 'observacao', 'modelo', 'data_calibracao'}
    sets, vals = [], []
    for k, v in d.items():
        if k in allowed:
            sets.append(f'{k}=?')
            vals.append(v)
    if not sets:
        return jsonify({'erro': 'Nenhum campo válido'}), 400
    sets.append('atualizado_em=CURRENT_TIMESTAMP')
    vals.append(eid)
    with get_db() as conn:
        conn.execute(f'UPDATE equipamentos_inventario SET {", ".join(sets)} WHERE id=?', vals)
    return jsonify({'ok': True})


# Frota real de bombas — dados conferidos nos certificados de calibração.
# (marca, modelo, numero_serie, label, cert_numero, data_calibracao YYYY-MM-DD)
# Validade = data_calibracao + 2 anos (calculada automaticamente).
_BOMBAS_FROTA = [
    ('SKC',    'AIRLITE',   'A060502',      'Bomba SKC AIRLITE',   '315125B',    '2025-11-18'),
    ('SKC',    'AIRLITE',   'A061553',      'Bomba SKC AIRLITE',   '270925B',    '2025-01-09'),
    ('SKC',    'AIRLITE',   'A061585',      'Bomba SKC AIRLITE',   '315025B',    '2025-11-18'),
    ('SKC',    'AIRLITE',   'A062462',      'Bomba SKC AIRLITE',   '315225B',    '2025-11-18'),
    ('SKC',    'AIRLITE',   'A63555',       'Bomba SKC AIRLITE',   '171922B',    '2022-06-23'),
    ('Gilian', 'BDX-II',    '20230702029',  'Bomba Gilian BDX-II', '2602A38356', '2026-02-28'),
    ('Gilian', 'BDX-II',    '20141201119',  'Bomba Gilian BDX-II', '2602A38357', '2026-02-28'),
    ('Gilian', 'BDX-II',    '20230702030',  'Bomba Gilian BDX-II', '2602A38358', '2026-02-28'),
    ('Gilian', 'BDX-II',    '20230702024',  'Bomba Gilian BDX-II', '2602A38359', '2026-02-28'),
    ('Formis', 'TURAM',     '2420120549',   'Bomba Formis TURAM',  None,         '2025-09-29'),
    ('Formis', 'TURAM',     '2420120550',   'Bomba Formis TURAM',  None,         '2025-09-29'),
    ('Formis', 'TURAM',     '2420120551',   'Bomba Formis TURAM',  None,         '2025-09-29'),
    ('Inlite', 'VENTUSPRO', '25040902602B', 'Bomba Inlite',        '42.188-2025', '2025-08-28'),
    ('Inlite', 'VENTUSPRO', '25040903102B', 'Bomba Inlite',        '42.187-2025', '2025-08-28'),
    ('Inlite', 'VENTUSPRO', '25040907102B', 'Bomba Inlite',        '42.186-2025', '2025-08-28'),
]

# Vibração e calor — Chrompack (série, nº de certificado e data de calibração conferidos nos PDFs).
# (marca, modelo, numero_serie, label, cert_numero, data_calibracao YYYY-MM-DD)
_VIBRACAO_FROTA = [
    ('Chrompack', 'SmartVib', '000000779', 'Medidor de vibração SmartVib', '181.302', '2026-04-09'),
    ('Chrompack', 'SmartVib', '1241',      'Medidor de vibração SmartVib', '',        ''),  # sem certificado na pasta — confirmar
]
_CALOR_FROTA = [
    ('Chrompack', 'SmartTemp', '000000209', 'Termômetro de stress térmico (IBUTG)', '180.646', '2026-03-24'),
]


def _eq_ins(conn, tipo, marca, modelo, sn, label, cert, dcal, compat=''):
    conn.execute(
        "INSERT INTO equipamentos_inventario "
        "(tipo, marca, modelo, numero_serie, compatibilidade, status, "
        "cert_numero, cert_validade, observacao, data_calibracao) "
        "VALUES (?,?,?,?,?,'disponivel',?,NULL,?,?)",
        (tipo, marca, modelo, sn, compat, (cert or None), label, (dcal or None))
    )


@controle_bp.route('/equipamentos/rebuild-frota', methods=['POST'])
def rebuild_frota():
    """Reconstrói o inventário COMPLETO com a frota real dos certificados:
    bombas, dosímetros de ruído (Chrompack+Inlite), calibradores, vibração e calor.
    Substitui os equipamentos desses tipos pelos dados conferidos nos PDFs."""
    init_db()
    import sys
    app_mod = sys.modules.get('app')
    dosim        = getattr(app_mod, '_DOSIM_RUIDO', {}) if app_mod else {}
    dosim_mod    = getattr(app_mod, '_DOSIM_RUIDO_MODELO', {}) if app_mod else {}
    calib        = getattr(app_mod, '_CALIB_RUIDO', {}) if app_mod else {}
    calib_marca  = getattr(app_mod, '_CALIB_RUIDO_MARCA', 'Chrompack') if app_mod else 'Chrompack'
    calib_modelo = getattr(app_mod, '_CALIB_RUIDO_MODELO', 'SmartCal') if app_mod else 'SmartCal'
    n = {'bomba': 0, 'dosimetro': 0, 'calibrador_ruido': 0, 'vibrador': 0, 'termometro': 0}
    with get_db() as conn:
        conn.execute("DELETE FROM equipamentos_inventario WHERE tipo IN "
                     "('bomba','dosimetro','calibrador_ruido','vibrador','termometro')")
        for marca, modelo, sn, label, cert, dcal in _BOMBAS_FROTA:
            _eq_ins(conn, 'bomba', marca, modelo, sn, label, cert, dcal); n['bomba'] += 1
        for mkey, items in (dosim or {}).items():
            marca  = 'Chrompack' if mkey == 'chrompack' else ('Inlite' if mkey == 'inlite' else str(mkey).title())
            modelo = dosim_mod.get(mkey, '')
            for _id, d in items.items():
                _eq_ins(conn, 'dosimetro', marca, modelo, d.get('serie', _id),
                        'Dosímetro de ruído', d.get('cert'), d.get('data_calib'), compat=mkey)
                n['dosimetro'] += 1
        for _id, c in (calib or {}).items():
            marca  = (c.get('marca') or calib_marca).title()
            modelo = c.get('modelo') or calib_modelo
            _eq_ins(conn, 'calibrador_ruido', marca, modelo, c.get('serie', _id),
                    'Calibrador de nível sonoro', c.get('cert'), c.get('data_calib'))
            n['calibrador_ruido'] += 1
        for marca, modelo, sn, label, cert, dcal in _VIBRACAO_FROTA:
            _eq_ins(conn, 'vibrador', marca, modelo, sn, label, cert, dcal); n['vibrador'] += 1
        for marca, modelo, sn, label, cert, dcal in _CALOR_FROTA:
            _eq_ins(conn, 'termometro', marca, modelo, sn, label, cert, dcal); n['termometro'] += 1
    total = sum(n.values())
    registrar_evento('limpeza_demandas', f'Inventário reconstruído dos certificados: {total} equipamentos {n}',
                     None, 'equipamento',
                     current_user.nome if current_user.is_authenticated else 'sistema',
                     request.remote_addr)
    return jsonify({'ok': True, 'total': total, 'detalhe': n})


_VAZAO_NUM_RE = re.compile(r'\d+(?:[.,]\d+)?')

def parse_vazao(raw):
    """Converte a string de vazao do guia_metodos numa faixa numerica (L/min).
    Retorna {raw, passivo, min, max, recomendada, media}.
      - passivo=True  -> amostrador passivo / sem vazao ('0' ou vazio)
      - min/max=None   -> nao foi possivel determinar
      - recomendada    -> valor a pre-preencher no planejamento
      - media          -> (min+max)/2
    Formatos suportados: '0,02 A 0,2 L/MIN', '2 L/MIN', '0',
    'MAXIMO 0,1 L/MIN', listas de ciclone '1,7 NYLON OU 2,0 SKC ...', TWA/STEL."""
    s = (raw or '').strip()
    res = {'raw': s, 'passivo': False, 'min': None, 'max': None,
           'recomendada': None, 'media': None}
    if not s or s == '0':
        res['passivo'] = True
        return res
    nums = [float(n.replace(',', '.')) for n in _VAZAO_NUM_RE.findall(s)]
    nums = [n for n in nums if n > 0]
    if not nums:
        res['passivo'] = True
        return res
    up = s.upper()
    is_max = ('MAX' in up) or ('MÁX' in up) or ('MÁXIM' in up) or ('MAXIM' in up)
    if is_max and len(nums) == 1:
        res['max'] = nums[0]
        res['recomendada'] = nums[0]
    elif len(nums) == 1:
        res['min'] = res['max'] = res['recomendada'] = nums[0]
    elif len(nums) == 2:
        lo, hi = min(nums), max(nums)
        res['min'], res['max'] = lo, hi
        res['recomendada'] = round((lo + hi) / 2, 4)
    else:
        # lista de valores discretos (ex: ciclones) -> menor e mais comum
        res['min'], res['max'] = min(nums), max(nums)
        res['recomendada'] = min(nums)
    if res['min'] is not None and res['max'] is not None:
        res['media'] = round((res['min'] + res['max']) / 2, 4)
    elif res['max'] is not None:
        res['media'] = res['max']
    return res


@controle_bp.route('/agentes')
def get_agentes():
    """Retorna todos os agentes do guia_metodos.json."""
    import json
    try:
        guia_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', 'guia_metodos.json')
        with open(guia_path, 'r', encoding='utf-8') as f:
            guia = json.load(f)
        by_cas = guia.get('by_cas', {})
        agentes = []
        seen = set()
        for cas, entry in by_cas.items():
            if isinstance(entry, list):
                entry = entry[0]
            nome = (entry.get('nome') or '').strip()
            if not nome or nome in seen:
                continue
            seen.add(nome)
            # tipo do amostrador extraido do amostradorCod ex: "SKC 226-01 (TCP*****)"
            tipo_amostrador = ''
            cod = entry.get('amostradorCod', '')
            if '(' in cod and ')' in cod:
                tipo_amostrador = cod[cod.index('(')+1:cod.index(')')].replace('*','').strip()
            agentes.append({
                'nome': nome,
                'categoria': 'quimico',
                'cas': entry.get('cas', cas),
                'metodo': entry.get('metodoCod', ''),
                'metodo_desc': entry.get('metodoDesc', ''),
                'vazao': entry.get('vazao', ''),
                'vazao_faixa': parse_vazao(entry.get('vazao', '')),
                'volume': entry.get('volume', ''),
                'amostrador': entry.get('amostradorCod', ''),
                'amostrador_desc': entry.get('amostradorDesc', ''),
                'tipo_amostrador': tipo_amostrador,
                'unidade': entry.get('unidade', ''),
                'cuidados': entry.get('cuidados', ''),
            })
        # Adicionar grupos BTX e BTXE (nao existem individualmente no guia)
        grupos = [
            {
                'nome': 'BTX (Benzeno + Tolueno + Xileno)',
                'categoria': 'quimico',
                'cas': '71-43-2 / 108-88-3 / 1330-20-7',
                'metodo': 'NIOSH 1501',
                'metodo_desc': 'CROMATOGRAFIA DE GASES COM DETECTOR DE IONIZACAO DE CHAMA',
                'vazao': '0,02 A 0,2 L/MIN',
                'volume': '2 A 10 L',
                'amostrador': 'SKC 226-01 (TCP*****)',
                'amostrador_desc': 'TUBO DE CARVAO ATIVADO COCONUT SHELL CHARCOAL, 6X70 mm, 2 SECOES DE 50/100 mg',
                'tipo_amostrador': 'TCP',
                'unidade': 'ppm',
                'cuidados': 'TRANSPORTE DE ROTINA. NAO NECESSITA REFRIGERACAO.',
            },
            {
                'nome': 'BTXE (Benzeno + Tolueno + Xileno + Etilbenzeno)',
                'categoria': 'quimico',
                'cas': '71-43-2 / 108-88-3 / 1330-20-7 / 100-41-4',
                'metodo': 'NIOSH 1501',
                'metodo_desc': 'CROMATOGRAFIA DE GASES COM DETECTOR DE IONIZACAO DE CHAMA',
                'vazao': '0,02 A 0,2 L/MIN',
                'volume': '2 A 10 L',
                'amostrador': 'SKC 226-01 (TCP*****)',
                'amostrador_desc': 'TUBO DE CARVAO ATIVADO COCONUT SHELL CHARCOAL, 6X70 mm, 2 SECOES DE 50/100 mg',
                'tipo_amostrador': 'TCP',
                'unidade': 'ppm',
                'cuidados': 'TRANSPORTE DE ROTINA. NAO NECESSITA REFRIGERACAO.',
            },
        ]
        for g in grupos:
            g['vazao_faixa'] = parse_vazao(g.get('vazao', ''))
        agentes = grupos + agentes

        # Agentes FÍSICOS — não vêm do guia químico (não têm CAS/amostrador/vazão).
        # Só são anexados na tela de Referência de Agentes (?incluir_fisicos=1);
        # os autocompletes do planejamento químico continuam recebendo só químicos.
        if request.args.get('incluir_fisicos') in ('1', 'true', 'sim'):
            fisicos = [
                {'nome': 'Ruído (Dosimetria)',
                 'metodo': 'NHO 01', 'metodo_desc': 'Avaliação da exposição ocupacional ao ruído contínuo/intermitente (Fundacentro) — NR-15 Anexo 1',
                 'tipo_amostrador': 'DOS', 'amostrador_desc': 'Dosímetro de ruído (audiodosímetro), TWA 8h',
                 'unidade': 'dB(A)'},
                {'nome': 'Ruído de Impacto',
                 'metodo': 'NHO 01 / NR-15 Anexo 2', 'metodo_desc': 'Nível de pressão sonora de pico — NR-15 Anexo 2',
                 'tipo_amostrador': 'MNPS', 'amostrador_desc': 'Medidor de nível de pressão sonora (dosímetro/decibelímetro), resposta pico',
                 'unidade': 'dB(C) pico'},
                {'nome': 'Calor (Estresse Térmico — IBUTG)',
                 'metodo': 'NHO 06', 'metodo_desc': 'Avaliação da exposição ocupacional ao calor — IBUTG (Fundacentro) — NR-15 Anexo 3',
                 'tipo_amostrador': 'IBUTG', 'amostrador_desc': 'Medidor de IBUTG (termômetro de bulbo úmido natural, globo e bulbo seco)',
                 'unidade': '°C'},
                {'nome': 'Vibração de Corpo Inteiro (VCI)',
                 'metodo': 'NHO 09', 'metodo_desc': 'Avaliação da exposição ocupacional a vibração de corpo inteiro (Fundacentro) — NR-15 Anexo 8',
                 'tipo_amostrador': 'ACEL', 'amostrador_desc': 'Medidor de vibração com acelerômetro triaxial (assento) — aren e VDV',
                 'unidade': 'm/s² (aren) · m/s^1,75 (VDV)'},
                {'nome': 'Vibração de Mãos e Braços (VMB)',
                 'metodo': 'NHO 10', 'metodo_desc': 'Avaliação da exposição ocupacional a vibração em mãos e braços (Fundacentro) — NR-15 Anexo 8',
                 'tipo_amostrador': 'ACEL', 'amostrador_desc': 'Medidor de vibração com acelerômetro triaxial (empunhadura) — aren',
                 'unidade': 'm/s² (aren)'},
            ]
            for fx in fisicos:
                fx.setdefault('categoria', 'fisico')
                fx.setdefault('cas', '')
                fx.setdefault('vazao', '')
                fx.setdefault('volume', '')
                fx.setdefault('amostrador', fx.get('amostrador_desc', ''))
                fx.setdefault('cuidados', '')
                fx['vazao_faixa'] = parse_vazao('')  # passivo — não há vazão
            agentes = fisicos + agentes

        return jsonify({'agentes': agentes, 'total': len(agentes)})
    except Exception as e:
        return jsonify({'erro': str(e), 'agentes': []}), 500


@controle_bp.route('/demandas_por_empresa')
def get_demandas_por_empresa():
    """Demandas agrupadas por empresa, com progresso total da empresa."""
    init_db()
    return jsonify(list_demandas_por_empresa(request.args.to_dict()))


@controle_bp.route('/empresa/<int:eid>/demandas')
def get_empresa(eid):
    init_db()
    d = get_empresa_demandas(eid)
    return (jsonify(d), 200) if d else (jsonify({'erro': 'nao encontrada'}), 404)


@controle_bp.route('/demanda/<int:did>/contato', methods=['POST'])
def marcar_contato(did):
    """Marca que o contato com cliente foi feito (SLA)."""
    init_db()
    d = request.json or {}
    feito = 1 if d.get('feito', True) else 0
    user = d.get('por', 'Matheus')
    with get_db() as conn:
        conn.execute("""
            UPDATE demandas SET contato_feito=?, contato_feito_em=CURRENT_TIMESTAMP,
                                contato_feito_por=?
            WHERE id=?""", (feito, user, did))
    return jsonify({'ok': True, 'contato_feito': bool(feito)})


@controle_bp.route('/empresa/<int:eid>/contato', methods=['POST'])
def marcar_contato_empresa(eid):
    """Registra contato com empresa: resultado, observação, próximo contato."""
    init_db()
    d = request.json or {}
    user       = d.get('por', 'Matheus')
    resultado  = (d.get('resultado') or '').strip()
    obs        = d.get('obs') or None
    prox       = d.get('proximo_contato') or None
    if not resultado:
        return jsonify({'ok': False, 'erro': 'resultado obrigatorio'}), 400
    with get_db() as conn:
        # Insere na tabela dedicada de histórico
        conn.execute(
            "INSERT INTO contatos_empresa (empresa_id, resultado, obs, proximo_contato, feito_por) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, resultado, obs, prox, user))
        # Mantém atualização nas demandas ativas para compatibilidade
        conn.execute("""
            UPDATE demandas SET
                contato_feito=1, contato_feito_em=CURRENT_TIMESTAMP,
                contato_feito_por=?, contato_resultado=?,
                contato_obs=?, proximo_contato=?
            WHERE empresa_id=? AND status != 'concluida'""",
            (user, resultado, obs, prox, eid))
        # Log no histórico de eventos
        desc = f'Contato: {resultado}'
        if obs: desc += f' — {obs[:120]}'
        conn.execute(
            "INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo, criado_em) "
            "VALUES ('contato_cliente', ?, ?, 'empresa', CURRENT_TIMESTAMP)",
            (desc, eid))
    return jsonify({'ok': True})


@controle_bp.route('/empresa/<int:eid>/contatos', methods=['GET'])
def get_contatos_empresa(eid):
    """Retorna histórico de contatos de uma empresa."""
    init_db()
    contatos = list_contatos_empresa(eid)
    return jsonify({'ok': True, 'contatos': contatos})


@controle_bp.route('/demandas/<int:did>')
def get_demanda(did):
    init_db()
    d = get_demanda_completa(did)
    return (jsonify(d), 200) if d else (jsonify({'erro': 'nao encontrada'}), 404)


def _canonical_to_tipo_legado(canonical: str) -> str:
    """Converte nome canônico do motor inteligente para tipo legado {ruido, calor, ...}."""
    n = canonical.lower()
    if 'ruído' in n or 'ruido' in n or 'dosimetria' in n:
        return 'ruido'
    if 'corpo inteiro' in n or ' vci' in n:
        return 'vibracao_vci'
    if 'mão' in n or 'braço' in n or ' vmb' in n or 'mao' in n or 'braco' in n:
        return 'vibracao_vbma'
    if 'vibr' in n:
        return 'vibracao'
    if 'calor' in n or 'ibutg' in n or 'estresse term' in n:
        return 'calor'
    if 'sílica' in n or 'silica' in n or 'poeira' in n or 'particulado' in n or 'material part' in n:
        return 'particulado'
    return 'quimico'


# Tipos do motor inteligente que NÃO são medição de campo: documento (PGR,
# LTCAT, Laudo de Insalubridade...), ergonomia e biológico. Sem este filtro o
# fallback de _canonical_to_tipo_legado transformava "Laudo de Periculosidade"
# em agente QUÍMICO e ele ia parar no planejamento e na planilha de campo.
_TIPOS_NAO_MEDICAO = {'documento', 'ergonomico', 'biologico'}


def _eh_agente_medicao(canonical: str, tipo_motor: str = '') -> bool:
    t = (tipo_motor or '').strip().lower()
    if t:
        return t not in _TIPOS_NAO_MEDICAO
    # Sem tipo do motor: só classifica nomes CANÔNICOS conhecidos; nome livre
    # é mantido (o _tipo_agente devolve 'documento' p/ desconhecidos e
    # derrubaria substância digitada à mão).
    try:
        from .inteligencia_demandas import _tipo_agente, AGENTES_SST
        if canonical in AGENTES_SST:
            return _tipo_agente(canonical) not in _TIPOS_NAO_MEDICAO
    except Exception:
        pass
    return True


def _extracao_tem_agentes(extracao_json_str: str) -> bool:
    """True se o extracao_json traz QUALQUER agente (mesmo que todos sejam
    documento/ergonomia). Evita cair no parser antigo quando o filtro de
    medição zerou a lista de propósito."""
    try:
        import json as _j
        return bool(_j.loads(extracao_json_str or '{}').get('agentes'))
    except Exception:
        return False


def _ags_de_extracao_json(extracao_json_str: str) -> list:
    """Lê agentes do extracao_json (motor inteligente) no formato legado {tipo, qtd, texto}."""
    if not extracao_json_str:
        return []
    try:
        import json as _j
        raw_ags = _j.loads(extracao_json_str).get('agentes', [])
        return [
            {'tipo': _canonical_to_tipo_legado(a.get('canonical', '')),
             'qtd':  a.get('quantidade', 1),
             'texto': a.get('canonical', '')}
            for a in raw_ags
            if a.get('canonical') and _eh_agente_medicao(a.get('canonical', ''), a.get('tipo', ''))
        ]
    except Exception:
        return []


def _ags_manual(agentes_manual_str):
    """Agentes editados manualmente pelo técnico — override que VENCE a extração
    automática e sobrevive ao re-extrair. Retorna None se não houver edição
    (cai pro automático); retorna lista (pode ser vazia) se houver."""
    if not agentes_manual_str:
        return None
    try:
        import json as _j
        ags = _j.loads(agentes_manual_str)
        if isinstance(ags, list):
            return [{'tipo': a.get('tipo', 'quimico'),
                     'qtd':  a.get('qtd', 1),
                     'texto': a.get('texto', '')} for a in ags if a.get('texto')]
    except Exception:
        pass
    return None


def _ags_multifonte(titulo: str, descricao: str, checklist_raw: str, bucket: str) -> list:
    """Motor inteligente multi-fonte: título + desc + checklist + bucket."""
    try:
        from .inteligencia_demandas import extrair_agentes_multifonte
        import json as _j
        checklist = []
        try:
            if checklist_raw:
                checklist = _j.loads(checklist_raw)
        except Exception:
            pass
        ags = extrair_agentes_multifonte(
            titulo=titulo or '', descricao=descricao or '',
            checklist=checklist, bucket=bucket or '',
        )
        return [{'tipo': _canonical_to_tipo_legado(a.canonical),
                 'qtd': a.quantidade, 'texto': a.canonical}
                for a in ags if _eh_agente_medicao(a.canonical, getattr(a, 'tipo', ''))]
    except Exception:
        return []


@controle_bp.route('/demandas/<int:did>/agentes')
def get_demanda_agentes(did):
    """Retorna agentes de medição extraídos da OS (motor inteligente multi-fonte)."""
    try:
        from .parser_agentes import resumo_agentes
        init_db()
        with get_db() as conn:
            row = conn.execute(
                'SELECT titulo, descricao, checklist, planner_bucket, extracao_json, agentes_manual FROM demandas WHERE id=?',
                (did,)
            ).fetchone()
        if not row:
            return jsonify({'erro': 'nao encontrada'}), 404
        d = row_to_dict(row)

        # 0. edição manual do técnico (override — vence a extração automática)
        agentes = _ags_manual(d.get('agentes_manual'))
        if agentes is None:
            # 1. extracao_json — já computado pelo motor no sync
            agentes = _ags_de_extracao_json(d.get('extracao_json', ''))
            # Extração existe mas só tinha documento/ergonomia (filtrados) →
            # lista vazia é a RESPOSTA CERTA; não cair nos fallbacks (o parser
            # antigo ressuscitaria o lixo).
            so_nao_medicao = not agentes and _extracao_tem_agentes(d.get('extracao_json', ''))
            # 2. motor multifonte (título + desc + checklist + bucket)
            if not agentes and not so_nao_medicao:
                agentes = _ags_multifonte(
                    d.get('titulo', ''), d.get('descricao', ''),
                    d.get('checklist', ''), d.get('planner_bucket', '')
                )
            # 3. fallback: parser antigo (só descrição)
            if not agentes and not so_nao_medicao:
                from .parser_agentes import extrair_agentes
                agentes = extrair_agentes(d.get('descricao') or '')

        return jsonify({'agentes': agentes, 'resumo': resumo_agentes(agentes)})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@controle_bp.route('/demandas/<int:did>/agentes', methods=['POST'])
def save_demanda_agentes(did):
    """Salva a edição MANUAL dos agentes de uma OS (corrige o que a extração errou).
    Vence a extração automática e sobrevive ao 'Reprocessar agentes'.
    Lista vazia → limpa o override e volta ao automático."""
    init_db()
    payload = request.json or {}
    raw = payload.get('agentes', [])
    if not isinstance(raw, list):
        return jsonify({'ok': False, 'erro': 'agentes deve ser uma lista'}), 400
    limpos = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        texto = (a.get('texto') or '').strip()
        if not texto:
            continue
        try:
            qtd = int(a.get('qtd') or 1)
        except (TypeError, ValueError):
            qtd = 1
        limpos.append({'tipo': (a.get('tipo') or 'quimico'), 'qtd': max(1, min(qtd, 99)), 'texto': texto})
    import json as _j
    valor = _j.dumps(limpos, ensure_ascii=False) if limpos else None  # vazio → volta ao automático
    with get_db() as conn:
        conn.execute('UPDATE demandas SET agentes_manual=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?',
                     (valor, did))
    try:
        registrar_evento('agentes_editados',
                         f'Agentes da OS editados manualmente ({len(limpos)} agente(s))',
                         did, 'demanda',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
    except Exception:
        pass
    return jsonify({'ok': True, 'agentes': limpos, 'modo': 'manual' if limpos else 'automatico'})


@controle_bp.route('/demandas/busca')
def busca_demandas():
    """Busca demandas por OS ou nome de empresa para autocomplete. ?q=texto&limit=8"""
    init_db()
    q = (request.args.get('q') or '').strip()
    limit = int(request.args.get('limit') or 8)
    if not q or len(q) < 2:
        return jsonify([])
    with get_db() as conn:
        rows = conn.execute(
            '''SELECT d.numero_os, d.titulo, e.nome AS empresa_nome, d.id
               FROM demandas d LEFT JOIN empresas e ON e.id = d.empresa_id
               WHERE (LOWER(COALESCE(d.numero_os,'')) LIKE LOWER(?)
                      OR LOWER(COALESCE(d.titulo,'')) LIKE LOWER(?)
                      OR LOWER(COALESCE(e.nome,'')) LIKE LOWER(?))
                 AND d.numero_os IS NOT NULL AND d.numero_os != ''
               ORDER BY d.criado_em DESC
               LIMIT ?''',
            (f'%{q}%', f'%{q}%', f'%{q}%', limit)
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@controle_bp.route('/demandas/por_os/<os_num>')
def get_demanda_por_os(os_num):
    """Busca demanda pelo número de OS e retorna id, título e agentes extraídos."""
    try:
        from .parser_agentes import resumo_agentes
        init_db()
        os_clean = os_num.strip()
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, titulo, descricao, checklist, planner_bucket, extracao_json, agentes_manual FROM demandas WHERE numero_os=? LIMIT 1",
                (os_clean,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id, titulo, descricao, checklist, planner_bucket, extracao_json, agentes_manual FROM demandas WHERE titulo LIKE ? LIMIT 1",
                    (f'%{os_clean}%',)
                ).fetchone()
        if not row:
            return jsonify({'encontrado': False}), 200
        d = row_to_dict(row)

        # Edição manual do técnico vence a extração automática
        agentes = _ags_manual(d.get('agentes_manual'))
        if agentes is None:
            agentes = _ags_de_extracao_json(d.get('extracao_json', ''))
            # Só tinha documento/ergonomia (filtrados) → não cair nos fallbacks
            so_nao_medicao = not agentes and _extracao_tem_agentes(d.get('extracao_json', ''))
            if not agentes and not so_nao_medicao:
                agentes = _ags_multifonte(
                    d.get('titulo', ''), d.get('descricao', ''),
                    d.get('checklist', ''), d.get('planner_bucket', '')
                )
            if not agentes and not so_nao_medicao:
                from .parser_agentes import extrair_agentes
                agentes = extrair_agentes(d.get('descricao') or '')

        # Buscar empresa vinculada
        with get_db() as conn:
            dem_full = conn.execute(
                '''SELECT d.empresa_id, e.nome AS empresa_nome, e.cnpj
                   FROM demandas d LEFT JOIN empresas e ON e.id=d.empresa_id
                   WHERE d.id=?''', (d['id'],)
            ).fetchone()
        return jsonify({
            'encontrado':   True,
            'id':           d['id'],
            'titulo':       d['titulo'],
            'empresa_id':   dem_full['empresa_id'] if dem_full else None,
            'empresa_nome': dem_full['empresa_nome'] if dem_full else '',
            'cnpj':         dem_full['cnpj'] if dem_full else '',
            'agentes':      agentes,
            'resumo':       resumo_agentes(agentes),
        })
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


@controle_bp.route('/demandas', methods=['POST'])
def cria_demanda():
    init_db()
    d = request.json or {}
    empresa_id = upsert_empresa(d.get('cnpj', ''), d.get('empresa', ''),
                                cidade=d.get('cidade'), uf=d.get('uf'))
    if not empresa_id:
        return jsonify({'erro': 'empresa obrigatoria'}), 400
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO demandas (numero_os, empresa_id, prazo, status, observacao)
            VALUES (?, ?, ?, ?, ?)""",
            (d.get('numero_os', ''), empresa_id, d.get('prazo'),
             d.get('status', 'pendente'), d.get('observacao', '')))
        did = cur.lastrowid
        # Medicoes
        for m in d.get('medicoes', []):
            conn.execute("""
                INSERT INTO medicoes
                    (demanda_id, agente, tipo_amostrador, qtd_pontos_prevista,
                     necessita_laudo, observacao)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (did, m.get('agente', ''), m.get('tipo_amostrador', ''),
                 m.get('qtd_pontos_prevista', 1),
                 m.get('necessita_laudo', ''), m.get('observacao', '')))
        return jsonify({'ok': True, 'id': did})


@controle_bp.route('/demandas/sem-empresa')
def api_demandas_sem_empresa():
    """Demandas com empresa_id=0 ou NULL (orphans do matching)."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, numero_os, COALESCE(titulo, nome_tarefa) AS titulo,
                   status, prazo, bucket, planner_bucket, criado_em
            FROM demandas
            WHERE (empresa_id IS NULL OR empresa_id = 0)
              AND origem = 'planner'
            ORDER BY criado_em DESC LIMIT 200
        """).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


# ── Baixas — listagem ─────────────────────────────────────────────────
@controle_bp.route('/baixas', methods=['GET'])
def api_list_baixas():
    """Lista baixas com filtros: os (numero_os), empresa_id, demanda_id, limit."""
    init_db()
    params, conds = [], ['1=1']
    if request.args.get('os'):
        conds.append('d.numero_os = ?'); params.append(request.args['os'])
    if _int_arg('empresa_id') is not None:
        conds.append('d.empresa_id = ?'); params.append(_int_arg('empresa_id'))
    if _int_arg('demanda_id') is not None:
        conds.append('m.demanda_id = ?'); params.append(_int_arg('demanda_id'))
    limit = min(_int_arg('limit', 50), 200)
    with get_db() as conn:
        rows = conn.execute(f'''
            SELECT b.id, b.medicao_id, b.amostrador_id,
                   b.avaliador, b.bomba, b.vazao_calibrada,
                   b.volume_recomendado, b.data_medicao, b.observacao,
                   b.tempo_calculado_min, b.tempo_calculado_max,
                   m.agente, m.tipo_amostrador,
                   d.numero_os, d.empresa_id,
                   e.nome AS empresa_nome
            FROM baixas b
            JOIN medicoes m ON m.id = b.medicao_id
            JOIN demandas d ON d.id = m.demanda_id
            LEFT JOIN empresas e ON e.id = d.empresa_id
            WHERE {" AND ".join(conds)}
            ORDER BY b.criado_em DESC LIMIT {limit}
        ''', params).fetchall()
    return jsonify({'baixas': [row_to_dict(r) for r in rows], 'total': len(rows)})


@controle_bp.route('/demandas/<int:did>/concluir', methods=['POST'])
def api_concluir_demanda(did):
    """Baixa manual da demanda/OS — decisão explícita do técnico ao concluir a medição.
    Sobrepõe a regra do Planner (que normalmente é a fonte de verdade), pois é
    ação consciente do usuário. Fecha também o planejamento vinculado, se houver.
    """
    init_db()
    d = request.json or {}
    with get_db() as conn:
        row = conn.execute('SELECT id, numero_os FROM demandas WHERE id=?', (did,)).fetchone()
        if not row:
            return jsonify({'erro': 'Demanda não encontrada'}), 404
        os_num = row_to_dict(row).get('numero_os', '')
        conn.execute(
            "UPDATE demandas SET status='concluida', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
            (did,))
        pid = d.get('planejamento_id')
        if pid:
            conn.execute(
                "UPDATE planejamentos SET status='concluido' WHERE id=? AND status != 'cancelado'",
                (pid,))
            _liberar_reservas_plano(pid, conn)
    registrar_evento('demanda_baixa_manual',
                     f'Baixa manual da OS {os_num or "—"} pelo técnico',
                     ref_id=did, ref_tipo='demanda',
                     usuario=current_user.nome if current_user.is_authenticated else 'sistema',
                     ip=request.remote_addr)
    return jsonify({'ok': True, 'demanda_id': did, 'status': 'concluida'})


# ── Baixa de amostrador ───────────────────────────────────────────────
@controle_bp.route('/baixa', methods=['POST'])
def dar_baixa():
    """Da baixa em uma medicao usando um amostrador.
    Atualiza: amostrador.status, medicao.status/qtd_feita, demanda.status,
    e registra historico em baixas.
    """
    init_db()
    d = request.json or {}
    medicao_id    = d.get('medicao_id')
    amostrador_id = d.get('amostrador_id')
    agente_avulso = (d.get('agente_avulso') or '').strip()
    demanda_id_avulso = d.get('demanda_id')

    if not amostrador_id:
        return jsonify({'erro': 'amostrador_id obrigatorio'}), 400

    # Modo avulso: cria medicao on-the-fly quando não tem medicao pré-cadastrada
    if not medicao_id and agente_avulso:
        if not demanda_id_avulso:
            return jsonify({'erro': 'demanda_id obrigatorio para entrada avulsa'}), 400
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, qtd_pontos_feita, status)
                   VALUES (?, ?, 1, 0, 'pendente')""",
                (demanda_id_avulso, agente_avulso))
            medicao_id = cur.lastrowid

    if not medicao_id:
        return jsonify({'erro': 'medicao_id ou agente_avulso+demanda_id obrigatorios'}), 400

    avaliador        = d.get('avaliador', '')
    bomba            = d.get('bomba', '')
    vazao_calibrada  = float(d.get('vazao_calibrada', 0) or 0)
    vol_recomendado  = float(d.get('volume_recomendado', 0) or 0)
    data_medicao     = d.get('data_medicao', datetime.now().strftime('%Y-%m-%d'))
    obs              = d.get('observacao', '')

    # Calcular tempos a partir da faixa de vazao recomendada (se enviada)
    vazao_min = float(d.get('vazao_min', 0) or 0)
    vazao_max = float(d.get('vazao_max', 0) or 0)
    vol_min   = float(d.get('volume_min', 0) or 0)
    vol_max   = float(d.get('volume_max', 0) or vol_recomendado)
    tempo_min = (vol_min / vazao_calibrada) if (vazao_calibrada > 0 and vol_min > 0) else 0
    tempo_max = (vol_max / vazao_calibrada) if (vazao_calibrada > 0 and vol_max > 0) else 0

    avisos = []
    if vazao_calibrada > 0:
        if vazao_min > 0 and vazao_calibrada < vazao_min:
            avisos.append(f'Vazao calibrada ({vazao_calibrada}) abaixo do minimo do metodo ({vazao_min})')
        if vazao_max > 0 and vazao_calibrada > vazao_max:
            avisos.append(f'Vazao calibrada ({vazao_calibrada}) acima do maximo do metodo ({vazao_max})')

    with get_db() as conn:
        am = conn.execute('SELECT * FROM amostradores WHERE id=?', (amostrador_id,)).fetchone()
        me = conn.execute('SELECT * FROM medicoes WHERE id=?', (medicao_id,)).fetchone()
        if not am: return jsonify({'erro': 'amostrador nao encontrado'}), 404
        if not me: return jsonify({'erro': 'medicao nao encontrada'}), 404

        # Registrar baixa
        conn.execute("""
            INSERT INTO baixas
                (medicao_id, amostrador_id, avaliador, bomba, vazao_calibrada,
                 volume_recomendado, tempo_calculado_min, tempo_calculado_max,
                 data_medicao, observacao)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (medicao_id, amostrador_id, avaliador, bomba, vazao_calibrada,
             vol_recomendado, tempo_min, tempo_max, data_medicao, obs))

        # Buscar empresa da demanda para vincular ao amostrador
        empresa_id = conn.execute(
            'SELECT empresa_id FROM demandas WHERE id=?',
            (me['demanda_id'],)).fetchone()['empresa_id']

        # Atualizar amostrador: muda status, vincula empresa, avaliador e data.
        # data_envio_lab NÃO é mais carimbada com a data da medição (fix 03/07/2026)
        # — a data REAL de envio vem do lab sync (e-mail enviado) ou lançamento manual;
        # carimbar aqui zerava a métrica coleta→lab.
        conn.execute("""
            UPDATE amostradores
            SET status='laboratorio', empresa_id=?, avaliador=?, data_medicao=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?""",
            (empresa_id, avaliador, data_medicao, amostrador_id))

        # Incrementar pontos realizados da medicao
        nova_qtd = (me['qtd_pontos_feita'] or 0) + 1
        novo_status = 'realizado' if nova_qtd >= (me['qtd_pontos_prevista'] or 1) else 'parcial'
        conn.execute("""
            UPDATE medicoes
            SET qtd_pontos_feita=?, status=?
            WHERE id=?""",
            (nova_qtd, novo_status, medicao_id))

        # Atualizar status da demanda se todas medicoes realizadas
        dem_id = me['demanda_id']
        pend = conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=? AND status!='realizado'",
            (dem_id,)).fetchone()['c']
        if pend == 0:
            conn.execute("UPDATE demandas SET status='concluida' WHERE id=?", (dem_id,))

    return jsonify({
        'ok': True,
        'tempo_min': round(tempo_min, 2),
        'tempo_max': round(tempo_max, 2),
        'avisos': avisos
    })


# ── Empresas ──────────────────────────────────────────────────────────
@controle_bp.route('/empresas')
def get_empresas():
    """Lista/busca empresas. Param ?q= filtra por nome ou CNPJ."""
    init_db()
    q = request.args.get('q', '').strip()
    limit = _qint('limit', 100, 200)
    sql = "SELECT * FROM empresas WHERE 1=1"
    params = []
    if q:
        sql += " AND (LOWER(COALESCE(nome,'')) LIKE LOWER(?) OR COALESCE(cnpj,'') LIKE ?)"
        params.extend([f'%{q}%', f'%{q}%'])
    sql += f" ORDER BY nome LIMIT {limit}"
    with get_db() as conn:
        return jsonify([row_to_dict(r) for r in conn.execute(sql, params).fetchall()])


@controle_bp.route('/empresa/<int:eid>')
def get_empresa_by_id(eid):
    """Retorna dados completos de uma empresa pelo ID."""
    init_db()
    with get_db() as conn:
        row = conn.execute('SELECT * FROM empresas WHERE id=?', (eid,)).fetchone()
    if not row:
        return jsonify({'erro': 'nao encontrada'}), 404
    return jsonify(row_to_dict(row))


@controle_bp.route('/coletas/outros')
def get_coletas_outros_route():
    """Lista coletas_outros com filtros: tipo, empresa_id, limit."""
    init_db()
    from .db import list_coletas_outros
    filtros = {}
    if request.args.get('tipo'):
        filtros['tipo'] = request.args['tipo']
    if _int_arg('empresa_id') is not None:
        filtros['empresa_id'] = _int_arg('empresa_id')
    if _int_arg('demanda_id') is not None:
        filtros['demanda_id'] = _int_arg('demanda_id')
    limit = _int_arg('limit', 50)
    rows = list_coletas_outros(filtros)
    return jsonify(rows[:limit])


@controle_bp.route('/empresas/<int:empresa_id>/painel')
def get_empresa_painel_route(empresa_id):
    """Painel completo de uma empresa: stats, demandas, agentes, técnicos, coletas, amostradores."""
    init_db()
    data = get_empresa_painel(empresa_id)
    if not data:
        return jsonify({'erro': 'Empresa não encontrada'}), 404
    return jsonify(data)


@controle_bp.route('/empresas', methods=['POST'])
def cria_empresa():
    init_db()
    d = request.json or {}
    nome = (d.get('nome') or '').strip()
    cnpj = (d.get('cnpj') or '').strip()
    if not nome:
        return jsonify({'erro': 'nome obrigatorio'}), 400
    # Verificar duplicacao por nome ou CNPJ
    with get_db() as conn:
        if cnpj:
            dup = conn.execute(
                'SELECT id, nome FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
            if dup:
                return jsonify({'erro': f'CNPJ ja cadastrado em "{dup["nome"]}"', 'id_existente': dup['id']}), 409
        # Nome parecido
        similar = conn.execute(
            'SELECT id, nome FROM empresas WHERE LOWER(nome) = LOWER(?)',
            (nome,)).fetchone()
        if similar:
            return jsonify({'erro': f'Nome ja cadastrado: "{similar["nome"]}"', 'id_existente': similar['id']}), 409
        # Fuzzy: "Paraopeba (005 - Usina)" vs "Paraopeba" — avisa antes de duplicar.
        # force=true cria mesmo assim (unidade/filial intencional).
        if not d.get('force'):
            try:
                from .empresa_match import normalizar_nome, similaridade
                base_novo = normalizar_nome(nome)
                for r in conn.execute('SELECT id, nome FROM empresas WHERE pendente=0').fetchall():
                    rn = r['nome'] if hasattr(r, '__getitem__') else r[1]
                    rid = r['id'] if hasattr(r, '__getitem__') else r[0]
                    if not rn:
                        continue
                    base_ex = normalizar_nome(rn)
                    if base_ex and (similaridade(base_novo, base_ex) >= 0.85
                                    or base_novo.startswith(base_ex) or base_ex.startswith(base_novo)):
                        return jsonify({
                            'erro': f'Empresa parecida já existe: "{rn}". Se for unidade/filial '
                                    'diferente, confirme a criação.',
                            'id_existente': rid, 'similar': rn, 'requer_confirmacao': True}), 409
            except Exception:
                pass
    eid = upsert_empresa(cnpj, nome,
        contato=d.get('contato'), telefone=d.get('telefone'),
        email=d.get('email'), cidade=d.get('cidade'), uf=d.get('uf'))
    return jsonify({'ok': True, 'id': eid})


@controle_bp.route('/empresas/enriquecer', methods=['POST'])
def api_enriquecer_empresa():
    """Salva no cadastro da empresa os dados que o técnico digitou ao gerar
    um laudo (CNPJ, endereço, CNAE, etc.). Preenche só o que está vazio —
    cruzamento de dados p/ reusar nas próximas planilhas/laudos. Não cria
    empresa nem sobrescreve. Disparado automaticamente no fim de cada laudo."""
    init_db()
    from .db import enriquecer_empresa
    return jsonify(enriquecer_empresa(request.json or {}))


@controle_bp.route('/empresas/busca')
def api_empresas_busca():
    """Autocomplete de empresas por nome — para planejamento de empresa por
    CONTRATO (sem OS). Busca em TODAS as empresas, não só as que têm demanda."""
    init_db()
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    try:
        limit = min(int(request.args.get('limit', 10) or 10), 30)
    except (TypeError, ValueError):
        limit = 10
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, nome, cnpj, cidade, unidade FROM empresas WHERE LOWER(COALESCE(nome,'')) LIKE LOWER(?) "
            "ORDER BY nome LIMIT ?", (f'%{q}%', limit)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@controle_bp.route('/diario_tecnicos')
def api_diario_tecnicos():
    """O que cada técnico fez num dia: coletas (ruído/quím/calor/vibr) + visitas.
    ?data=YYYY-MM-DD (default = hoje). Visível a todos (transparência)."""
    init_db()
    from datetime import date as _date
    data = (request.args.get('data') or '').strip()[:10] or _date.today().isoformat()
    ativ = {}
    def _add(tec, item):
        tec = (tec or '').strip() or 'Sem técnico'
        ativ.setdefault(tec, []).append(item)
    with get_db() as conn:
        for tbl, tipo_tbl, teccol in [
            ('coletas_ruido', 'ruido', 'tecnico'),
            ('coletas_quimico', 'quimico', 'responsavel_coleta'),
            ('coletas_outros', 'outros', 'avaliador')]:
            try:
                rows = conn.execute(
                    f"SELECT * FROM {tbl} WHERE substr(COALESCE(data_coleta,''),1,10)=?",
                    (data,)).fetchall()
            except Exception:
                rows = []
            for r in rows:
                dd = row_to_dict(r)
                tipo_real = (dd.get('tipo') or tipo_tbl) if tbl == 'coletas_outros' else tipo_tbl
                _add(dd.get('tecnico_login') or dd.get(teccol),
                     {'acao': 'coleta', 'tipo': tipo_real,
                      'empresa': dd.get('empresa_nome') or '',
                      'os': dd.get('os') or dd.get('numero_os') or '',
                      'hora': dd.get('hora_inicio') or ''})
        try:
            vrows = conn.execute(
                "SELECT vt.tecnico, vt.resultado, e.nome AS empresa_nome "
                "FROM visitas_tecnicas vt LEFT JOIN empresas e ON e.id=vt.empresa_id "
                "WHERE substr(COALESCE(vt.data_visita,''),1,10)=?", (data,)).fetchall()
        except Exception:
            vrows = []
        for r in vrows:
            dd = row_to_dict(r)
            _add(dd.get('tecnico'),
                 {'acao': 'visita', 'tipo': 'visita',
                  'empresa': dd.get('empresa_nome') or '', 'os': '',
                  'resultado': dd.get('resultado') or ''})
        # Agendado: visitas previstas (planejamentos) neste dia
        try:
            arows = conn.execute(
                "SELECT p.tecnico, p.numero_os, e.nome AS empresa_nome "
                "FROM planejamentos p LEFT JOIN empresas e ON e.id=p.empresa_id "
                "WHERE substr(COALESCE(p.data_prevista,''),1,10)=?", (data,)).fetchall()
        except Exception:
            arows = []
    agendados = []
    for r in arows:
        dd = row_to_dict(r)
        agendados.append({'tecnico': (dd.get('tecnico') or '').strip() or 'Sem técnico',
                          'empresa': dd.get('empresa_nome') or '', 'os': dd.get('numero_os') or ''})
    out = [{'tecnico': t, 'qtd': len(items), 'atividades': items} for t, items in ativ.items()]
    out.sort(key=lambda x: -x['qtd'])
    return jsonify({'data': data, 'total': sum(x['qtd'] for x in out),
                    'tecnicos': out, 'agendados': agendados})


@controle_bp.route('/diario_calendario')
def api_diario_calendario():
    """Contagem por dia do mês p/ o calendário estilo Outlook: 'feito'
    (coletas+visitas) e 'agendado' (planejamentos por data prevista).
    ?mes=YYYY-MM (default = mês atual)."""
    init_db()
    from datetime import date as _date
    mes = (request.args.get('mes') or '').strip()[:7] or _date.today().isoformat()[:7]
    dias = {}
    def _bump(dataiso, key):
        d10 = str(dataiso or '')[:10]
        if len(d10) == 10:
            dias.setdefault(d10, {'feito': 0, 'agendado': 0})[key] += 1
    with get_db() as conn:
        for tbl in ('coletas_ruido', 'coletas_quimico', 'coletas_outros'):
            try:
                rows = conn.execute(
                    f"SELECT data_coleta FROM {tbl} WHERE substr(COALESCE(data_coleta,''),1,7)=?",
                    (mes,)).fetchall()
            except Exception:
                rows = []
            for r in rows:
                _bump(row_to_dict(r).get('data_coleta'), 'feito')
        try:
            vrows = conn.execute(
                "SELECT data_visita FROM visitas_tecnicas WHERE substr(COALESCE(data_visita,''),1,7)=?",
                (mes,)).fetchall()
        except Exception:
            vrows = []
        for r in vrows:
            _bump(row_to_dict(r).get('data_visita'), 'feito')
        try:
            prows = conn.execute(
                "SELECT data_prevista FROM planejamentos WHERE substr(COALESCE(data_prevista,''),1,7)=?",
                (mes,)).fetchall()
        except Exception:
            prows = []
        for r in prows:
            _bump(row_to_dict(r).get('data_prevista'), 'agendado')
    return jsonify({'mes': mes, 'dias': dias})


# ── Estoque de amostradores por agente ────────────────────────────────

# Tipos de amostrador que a empresa NÃO utiliza (não aparecem na previsão)
TIPOS_IGNORADOS_PREVISAO = {'FMD', 'OVM', 'ICR', 'ASL', 'IEC'}

# Aliases de agentes → nome canônico no guia (para lookup exato)
AGENTE_ALIASES = {
    'BTX': 'BENZENO',
    'BTE': 'BENZENO',
    'BTXE': 'BENZENO',
    'BENZENO, TOLUENO E XILENO': 'BENZENO',
    'BENZENO, TOLUENO, XILENO': 'BENZENO',
    'BENZENO, TOLUENO, ETILBENZENO': 'BENZENO',
    'ARSÊNIO': 'ARSÊNIO E COMPOSTOS INORGÂNICOS',
    'ARSENIO': 'ARSÊNIO E COMPOSTOS INORGÂNICOS',
    'ARSÊNIO E COMPOSTOS INORGÂNICOS, COM AS': 'ARSÊNIO E COMPOSTOS INORGÂNICOS',
    'HEXANO, OUTROS ISÔMEROS': 'HEXANO, OUTROS ISÔMEROS QUE NÃO O N-HEXANO',
    'HEXANO, OUTROS ISOMEROS': 'HEXANO, OUTROS ISÔMEROS QUE NÃO O N-HEXANO',
    'HEXANO OUTROS ISÔMEROS': 'HEXANO, OUTROS ISÔMEROS QUE NÃO O N-HEXANO',
    # Grafias canônicas do AGENTES_SST que não casavam com o guia (08/07/2026)
    'SÍLICA CRISTALINA': 'SÍLICA LIVRE CRISTALIZADA (QUARTZO)',
    'SILICA CRISTALINA': 'SÍLICA LIVRE CRISTALIZADA (QUARTZO)',
    'SÍLICA LIVRE CRISTALINA': 'SÍLICA LIVRE CRISTALIZADA (QUARTZO)',
    'MEK (BUTANONA)': 'METIL ETIL CETONA',
    'MEK': 'METIL ETIL CETONA',
    'BUTANONA': 'METIL ETIL CETONA',
    'ÁCIDO CLORÍDRICO': 'CLORETO DE HIDROGÊNIO',
    'ACIDO CLORIDRICO': 'CLORETO DE HIDROGÊNIO',
    'ÓXIDO DE FERRO': 'FERRO, ÓXIDO (FE2O3)',
    'OXIDO DE FERRO': 'FERRO, ÓXIDO (FE2O3)',
    'SODA CÁUSTICA (NAOH)': 'HIDRÓXIDO DE SÓDIO',
    'SODA CAUSTICA (NAOH)': 'HIDRÓXIDO DE SÓDIO',
    'SODA CÁUSTICA': 'HIDRÓXIDO DE SÓDIO',
    'SODA CAUSTICA': 'HIDRÓXIDO DE SÓDIO',
}


def _extrair_tipos_amostrador(amostrador_cod):
    """Extrai siglas de tipo (TCP, TAS, IOL, etc) de strings como
       'SKC 226-01 (TCP*****)' ou 'IOL E X2P' ou 'TCG E TCP'.
    """
    if not amostrador_cod:
        return []
    tipos = set()
    s = amostrador_cod.upper()
    # 1) Codigos entre parenteses com asteriscos: (TCP*****)
    for m in re.findall(r'\(([A-Z][A-Z0-9]+)\*+\)', s):
        tipos.add(m)
    # 2) Siglas isoladas separadas por ' E ', ',', '/'
    if not tipos:
        for parte in re.split(r'\s+E\s+|\s*,\s*|\s*/\s*', s):
            parte = parte.strip()
            if re.fullmatch(r'[A-Z][A-Z0-9]{1,4}', parte):
                tipos.add(parte)
    return list(tipos)


def _buscar_metodos_agente(nome_agente):
    """Busca metodos do agente no guia_metodos.json. Aceita nome ou CAS."""
    try:
        guia_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', 'guia_metodos.json')
        import json
        with open(guia_path, 'r', encoding='utf-8') as f:
            guia = json.load(f)
    except Exception as e:
        print(f'[controle] erro carregando guia: {e}')
        return []
    chave = (nome_agente or '').strip()
    if not chave:
        return []
    # Resolver alias primeiro
    alias = AGENTE_ALIASES.get(chave.upper())
    if alias:
        chave = alias
    # Tentar como CAS
    if chave in guia.get('by_cas', {}):
        return guia['by_cas'][chave]
    # Tentar como nome (uppercase)
    key_upper = chave.upper()
    if key_upper in guia.get('by_name', {}):
        cas = guia['by_name'][key_upper]
        return guia.get('by_cas', {}).get(cas, [])

    # Normaliza espaço, newlines e vírgulas para comparação fuzzy
    def _norm(s):
        s = re.sub(r',\s*', ' ', s)  # vírgulas → espaço
        return re.sub(r'[\s]+', ' ', s.strip())

    key_norm = _norm(key_upper)
    # Busca parcial normalizada — evita matches espúrios em substrings curtas
    best = None
    for nome_upper, cas in guia.get('by_name', {}).items():
        nome_norm = _norm(nome_upper)
        if key_norm in nome_norm:
            if best is None or len(nome_norm) < len(best[0]):
                best = (nome_norm, cas)
    if best:
        return guia.get('by_cas', {}).get(best[1], [])
    # Busca inversa normalizada: nome do guia dentro da chave
    for nome_upper, cas in guia.get('by_name', {}).items():
        nome_norm = _norm(nome_upper)
        if len(nome_norm) > 6 and nome_norm in key_norm:
            return guia.get('by_cas', {}).get(cas, [])
    return []


@controle_bp.route('/agente/<path:nome>/estoque')
def estoque_para_agente(nome):
    """Para um agente, retorna:
      - metodos cadastrados (do guia)
      - tipos de amostrador compativeis
      - quantos amostradores em estoque por tipo
      - lista de codigos disponiveis
    """
    init_db()
    # Agentes extraídos do Planner podem vir com \n/espaços múltiplos no nome —
    # quebrava a URL/lookup (fix 03/07/2026). Colapsa qualquer whitespace.
    nome = re.sub(r'\s+', ' ', nome or '').strip()
    metodos = _buscar_metodos_agente(nome)
    if not metodos:
        return jsonify({
            'agente': nome,
            'metodos': [],
            'tipos_compativeis': [],
            'total_disponivel': 0,
            'por_tipo': {},
            'amostradores': [],
            'aviso': 'Agente nao encontrado na guia de metodos'
        })

    tipos_set = set()
    metodos_resumo = []
    for m in metodos:
        tipos_m = _extrair_tipos_amostrador(m.get('amostradorCod', ''))
        tipos_set.update(tipos_m)
        metodos_resumo.append({
            'metodoCod': m.get('metodoCod', ''),
            'vazao': m.get('vazao', ''),
            'vazao_faixa': parse_vazao(m.get('vazao', '')),
            'volume': m.get('volume', ''),
            'amostradorCod': m.get('amostradorCod', ''),
            'amostradorDesc': m.get('amostradorDesc', ''),
            'tipos': tipos_m,
        })
    tipos = list(tipos_set)

    por_tipo = {}
    amostr = []
    if tipos:
        placeholders = ','.join(['?'] * len(tipos))
        with get_db() as conn:
            rows = conn.execute(f"""
                SELECT a.id, a.codigo, a.tipo, a.status, a.data_entrada,
                       e.nome AS empresa_nome
                FROM amostradores a
                LEFT JOIN empresas e ON e.id = a.empresa_id
                WHERE a.tipo IN ({placeholders})
                ORDER BY
                  CASE WHEN a.status='disponivel' THEN 0 ELSE 1 END,
                  a.tipo, a.codigo
                LIMIT 500
            """, tipos).fetchall()
            for r in rows:
                d = row_to_dict(r)
                amostr.append(d)
                t = d['tipo']
                por_tipo.setdefault(t, {'estoque': 0, 'lab': 0, 'reservado': 0, 'total': 0})
                por_tipo[t]['total'] += 1
                st = (d.get('status') or '').lower()
                if 'dispon' in st or 'estoque' in st: por_tipo[t]['estoque'] += 1
                elif 'lab' in st: por_tipo[t]['lab'] += 1
                elif 'reserv' in st: por_tipo[t]['reservado'] += 1

    total_disponivel = sum(v['estoque'] for v in por_tipo.values())

    return jsonify({
        'agente': nome,
        'metodos': metodos_resumo,
        'tipos_compativeis': tipos,
        'total_disponivel': total_disponivel,
        'por_tipo': por_tipo,
        'amostradores': amostr,
    })


# ── PDF de campo (kit de coleta) ──────────────────────────────────────
@controle_bp.route('/cadeia_pdf', methods=['POST'])
def gerar_cadeia_pdf():
    """Gera PDF com lista de medicoes a fazer em campo.
    Payload: {
      empresa: { nome, cnpj, contato },
      data_medicao: 'dd/mm/aaaa',
      avaliador: str,
      itens: [{ agente, bomba_modelo, bomba_sn, amostrador_tipo, amostrador_codigo,
                vazao_calibrada, tempo_min, tempo_max, observacao }]
    }
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = request.json or {}
    empresa = d.get('empresa', {})
    data_med = d.get('data_medicao', datetime.now().strftime('%d/%m/%Y'))
    avaliador = d.get('avaliador', '')
    itens = d.get('itens', [])
    if not itens:
        return jsonify({'erro': 'sem itens'}), 400

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Title'], fontSize=14, alignment=TA_CENTER, spaceAfter=6)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], fontSize=10, spaceAfter=4)
    body = ParagraphStyle('body', parent=styles['BodyText'], fontSize=9)
    elements = []

    elements.append(Paragraph(f'<b>FICHA DE CAMPO — COLETA AMBIENTAL</b>', h1))
    elements.append(Paragraph(f'<b>Empresa:</b> {empresa.get("nome","-")}', body))
    if empresa.get('cnpj'):
        elements.append(Paragraph(f'<b>CNPJ:</b> {empresa["cnpj"]}', body))
    if empresa.get('contato'):
        elements.append(Paragraph(f'<b>Contato:</b> {empresa["contato"]}', body))
    elements.append(Paragraph(f'<b>Data da medição:</b> {data_med}    <b>Avaliador:</b> {avaliador}', body))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f'<b>Total de pontos:</b> {len(itens)}', body))
    elements.append(Spacer(1, 8))

    # Tabela com Paragraph para word-wrap em colunas largas
    cell_style = ParagraphStyle('cell', parent=body, fontSize=8, leading=10)
    cell_bold  = ParagraphStyle('cellb', parent=cell_style, fontName='Helvetica-Bold')
    def P(txt):
        return Paragraph(str(txt) if txt is not None else '—', cell_style)

    head = ['#', 'Agente', 'Amostrador', 'Bomba (S/N)', 'Vazão\n(L/min)', 'Tempo\n(min)', 'Função/Setor']
    data = [head]
    for i, it in enumerate(itens, 1):
        bomba = f'{it.get("bomba_modelo","")} {it.get("bomba_sn","")}'.strip() or '—'
        amostr = f'{it.get("amostrador_tipo","")} {it.get("amostrador_codigo","")}'.strip() or '—'
        tempo = ''
        if it.get('tempo_min') and it.get('tempo_max'):
            tempo = f'{it["tempo_min"]:.0f}–{it["tempo_max"]:.0f}'
        elif it.get('tempo_min'):
            tempo = f'{it["tempo_min"]:.0f}'
        data.append([
            str(i),
            P(it.get('agente','—')),         # word-wrap
            P(amostr),
            P(bomba),
            str(it.get('vazao_calibrada','—')),
            tempo,
            P(it.get('funcao','') or it.get('observacao','')),
        ])

    # Larguras ajustadas: agente e função maiores, vazão/tempo menores
    tbl = Table(data, repeatRows=1,
        colWidths=[0.7*cm, 4.5*cm, 2.8*cm, 3.2*cm, 1.7*cm, 1.6*cm, 3.5*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2E75B6')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (4,0), (5,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 14))

    # Checklist conferencia
    elements.append(Paragraph('<b>Conferência antes da coleta:</b>', h2))
    elements.append(Paragraph('☐ Todas as bombas calibradas e com bateria carregada', body))
    elements.append(Paragraph('☐ Amostradores rotulados com numero do filtro', body))
    elements.append(Paragraph('☐ Cronometro/relogio ajustado', body))
    elements.append(Paragraph('☐ EPI completo (luvas, oculos, mascara)', body))
    elements.append(Paragraph('☐ Cadeia de custodia preenchida', body))
    elements.append(Spacer(1, 24))
    elements.append(Paragraph('<i>Assinatura do avaliador:</i> _____________________________________', body))

    doc.build(elements)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa.get('nome', 'empresa'))[:40]
    return send_file(buf, as_attachment=True,
        download_name=f'ficha_campo_{nome_safe}_{data_med.replace("/","-")}.pdf',
        mimetype='application/pdf')


# ── Bombas disponiveis (reusa cadastro do quimico) ────────────────────
@controle_bp.route('/bombas')
def get_bombas():
    """Retorna lista de bombas + numeros de serie cadastrados."""
    try:
        # Importar do app principal sem causar import circular
        import sys
        app_mod = sys.modules.get('app')
        if app_mod and hasattr(app_mod, '_PUMP_SN') and hasattr(app_mod, '_PUMP_NAMES'):
            return jsonify({
                'modelos': [
                    {'id': k, 'nome': app_mod._PUMP_NAMES.get(k, k),
                     'sns': app_mod._PUMP_SN.get(k, [])}
                    for k in app_mod._PUMP_SN
                ]
            })
    except Exception as e:
        print(f'[controle] /bombas erro: {e}')
    # Fallback hardcoded
    return jsonify({
        'modelos': [
            {'id': 'airlite', 'nome': 'AIRLITE – SKC', 'sns': ['A060502', 'A061553', 'A061585', 'A062462', 'A63555']},
            {'id': 'bdx',     'nome': 'BDX II – GILLIAN', 'sns': ['20230702024', '20230702029', '20230702030', '20141201119']},
            {'id': 'turam',   'nome': 'FORMIS – TURAM', 'sns': ['2420120549', '2420120550', '2420120551']},
            {'id': 'inlite',  'nome': 'INLITE VENTUSPRO', 'sns': ['25040902602B', '25040903102B', '25040907102B']},
        ]
    })


@controle_bp.route('/calibradores-ruido')
def get_calibradores_ruido():
    """Retorna calibradores de nivel sonoro (ruido) ativos com serie/cert/validade.
    Fonte: _CALIB_RUIDO em app.py (certificado vigente = calibracao mais recente).
    """
    from datetime import datetime, timedelta
    def _validade(dc):
        try:
            return (datetime.strptime(dc, '%Y-%m-%d') + timedelta(days=365)).strftime('%Y-%m-%d')
        except Exception:
            return ''
    _fallback = {
        '1562': {'serie': 'CAL0000001562', 'cert': '142.574',    'data_calib': '2023-02-14', 'marca': 'CHROMPACK'},
        '1575': {'serie': 'CAL0000001575', 'cert': '2503A35509', 'data_calib': '2025-03-20', 'marca': 'CHROMPACK'},
        '2150': {'serie': 'CAL0000002150', 'cert': '172.833',    'data_calib': '2025-08-18', 'marca': 'CHROMPACK'},
        '1614': {'serie': 'CAL0000001614', 'cert': '181.238',    'data_calib': '2026-04-08', 'marca': 'CHROMPACK'},
        '0284': {'serie': 'CAL0000000284', 'cert': '181.239',    'data_calib': '2026-04-08', 'marca': 'CHROMPACK'},
        '25035711': {'serie': '25035711', 'cert': '42.179-2025', 'data_calib': '2025-08-28', 'marca': 'INLITE', 'modelo': 'CalPro'},
    }
    marca, modelo, dados = 'CHROMPACK', 'SMARTCAL', _fallback
    try:
        import sys
        app_mod = sys.modules.get('app')
        if app_mod and hasattr(app_mod, '_CALIB_RUIDO'):
            dados = app_mod._CALIB_RUIDO
            marca = getattr(app_mod, '_CALIB_RUIDO_MARCA', marca)
            modelo = getattr(app_mod, '_CALIB_RUIDO_MODELO', modelo)
    except Exception as e:
        print(f'[controle] /calibradores-ruido erro: {e}')
    hoje = datetime.now().strftime('%Y-%m-%d')
    itens = []
    for k, v in dados.items():
        val = _validade(v.get('data_calib', ''))
        itens.append({
            'id': k, 'serie': v.get('serie', k),
            'marca': v.get('marca', marca), 'modelo': v.get('modelo', modelo),
            'cert': v.get('cert', ''), 'data_calib': v.get('data_calib', ''),
            'validade': val, 'vencido': bool(val and val < hoje),
        })
    itens.sort(key=lambda x: (x['marca'], x['serie']))
    return jsonify({'marca': marca, 'modelo': modelo, 'calibradores': itens})


@controle_bp.route('/dosimetros-ruido')
def get_dosimetros_ruido():
    """Retorna dosimetros de ruido ativos com serie/cert/validade, por marca.
    Fonte: _DOSIM_RUIDO em app.py (estrutura {chrompack:{...}, inlite:{...}}).
    Validade = calibracao + 365 dias (IEC 60942 anual).
    """
    from datetime import datetime, timedelta
    def _validade(dc):
        try:
            return (datetime.strptime(dc, '%Y-%m-%d') + timedelta(days=365)).strftime('%Y-%m-%d')
        except Exception:
            return ''
    dados, modelos = {}, {'chrompack': 'SmartdB', 'inlite': 'DoseMax V2'}
    try:
        import sys
        app_mod = sys.modules.get('app')
        if app_mod and hasattr(app_mod, '_DOSIM_RUIDO'):
            dados = app_mod._DOSIM_RUIDO
            modelos = getattr(app_mod, '_DOSIM_RUIDO_MODELO', modelos)
    except Exception as e:
        print(f'[controle] /dosimetros-ruido erro: {e}')
    hoje = datetime.now().strftime('%Y-%m-%d')
    itens = []
    for marca_key, grupo in dados.items():
        modelo = modelos.get(marca_key, '')
        for k, v in grupo.items():
            val = _validade(v.get('data_calib', ''))
            itens.append({
                'id': k, 'serie': v.get('serie', k),
                'marca': marca_key.upper(), 'modelo': modelo,
                'cert': v.get('cert', ''), 'data_calib': v.get('data_calib', ''),
                'validade': val, 'vencido': bool(val and val < hoje),
            })
    itens.sort(key=lambda x: (x['marca'], x['serie']))
    return jsonify({'dosimetros': itens})


# ── Previsão de estoque baseada em demandas pendentes ────────────────
@controle_bp.route('/previsao_estoque')
def previsao_estoque():
    """Cruza demandas pendentes × guia de métodos × estoque atual.
    Retorna para cada tipo de amostrador: qtd necessária, em estoque e falta.
    """
    init_db()
    with get_db() as conn:
        meds = [row_to_dict(r) for r in conn.execute("""
            SELECT m.id, m.agente, m.tipo_amostrador,
                   m.qtd_pontos_prevista, m.qtd_pontos_feita, m.status,
                   d.numero_os, d.prazo, d.empresa_id,
                   e.nome AS empresa_nome
            FROM medicoes m
            JOIN demandas d ON d.id = m.demanda_id
            JOIN empresas e ON e.id = d.empresa_id
            WHERE m.status != 'realizado'
              AND d.status != 'concluida'
            ORDER BY d.prazo ASC NULLS LAST
        """).fetchall()]

    AGENTES_FISICOS = {
        'Ruído', 'Ruido', 'Calor', 'Frio', 'Iluminação', 'Iluminacao',
        'Radiação Ionizante', 'Radiacao Ionizante', 'Radiação Não Ionizante',
        'Vibração Localizada - Mãos e Braços', 'Vibracao Localizada - Maos e Bracos',
        'Vibração de Corpo Inteiro', 'Vibracao de Corpo Inteiro',
        'Pressão Hiperbárica', 'Pressao Hiperbarica',
    }

    necessidades = {}   # tipo -> {qtd_necessaria, falta, medicoes[]}
    agentes_sem_guia = set()
    agentes_fisicos_presentes = set()

    for m in meds:
        agente = m.get('agente', '')
        pontos_faltam = max(0, (m.get('qtd_pontos_prevista') or 1) -
                                (m.get('qtd_pontos_feita') or 0))
        if pontos_faltam == 0:
            continue

        metodos = _buscar_metodos_agente(agente)
        if not metodos:
            if agente in AGENTES_FISICOS or any(f in agente for f in ['Ruído','Ruido','Calor','Vibração','Vibracao','Frio','Radiação','Radiacao','Iluminação']):
                agentes_fisicos_presentes.add(agente)
                continue
            agentes_sem_guia.add(agente)
            # Tenta usar tipo_amostrador ja cadastrado na medicao
            tipo_raw = (m.get('tipo_amostrador') or '').upper().strip()
            if tipo_raw:
                necessidades.setdefault(tipo_raw, {
                    'qtd_necessaria': 0, 'em_estoque': 0, 'falta': 0,
                    'medicoes': [], 'metodo': '(tipo da planilha)',
                    'vazao': '', 'volume': ''
                })
                necessidades[tipo_raw]['qtd_necessaria'] += pontos_faltam
                necessidades[tipo_raw]['medicoes'].append({
                    'empresa': m['empresa_nome'], 'os': m['numero_os'],
                    'agente': agente, 'prazo': m['prazo'], 'pontos': pontos_faltam
                })
            continue

        tipos_vistos = set()
        for met in metodos:
            tipos = _extrair_tipos_amostrador(met.get('amostradorCod', ''))
            for t in tipos:
                if t in tipos_vistos:
                    continue
                if t in TIPOS_IGNORADOS_PREVISAO:
                    continue  # empresa não usa este tipo
                tipos_vistos.add(t)
                necessidades.setdefault(t, {
                    'qtd_necessaria': 0, 'em_estoque': 0, 'falta': 0,
                    'medicoes': [],
                    'metodo': met.get('metodoCod', ''),
                    'vazao': met.get('vazao', ''),
                    'volume': met.get('volume', ''),
                })
                necessidades[t]['qtd_necessaria'] += pontos_faltam
                necessidades[t]['medicoes'].append({
                    'empresa': m['empresa_nome'], 'os': m['numero_os'],
                    'agente': agente, 'prazo': m['prazo'], 'pontos': pontos_faltam
                })

    # ── Inteligência de pedido: consumo histórico (cadeias) + lead time ──
    import math as _math
    from datetime import date as _date, timedelta as _td
    try:
        from .cadeia_consumo import CONSUMO_MENSAL, JANELA
    except Exception:
        CONSUMO_MENSAL, JANELA = {}, ''
    LEAD = 7        # dias que o laboratório leva para entregar um pedido
    MARGEM = 7      # folga de segurança
    hoje = _date.today()

    # Contar estoque atual por tipo + calcular quando/por que pedir
    with get_db() as conn:
        for tipo, dados in necessidades.items():
            r = conn.execute(
                "SELECT COUNT(*) c FROM amostradores WHERE tipo=? AND status='disponivel'",
                (tipo,)).fetchone()
            estoque = r['c'] if r else 0
            dados['em_estoque'] = estoque
            dados['falta'] = max(0, dados['qtd_necessaria'] - estoque)

            consumo = float(CONSUMO_MENSAL.get(tipo, 0) or 0)
            dados['consumo_mensal'] = consumo
            dias_cob = int(estoque / (consumo / 30.0)) if consumo > 0 else None
            dados['dias_cobertura'] = dias_cob

            if dados['falta'] > 0:
                dados['acao'] = 'pedir_ja'
                dados['quando'] = hoje.isoformat()
                dados['qtd_sugerida'] = max(dados['falta'], _math.ceil(consumo) if consumo else dados['falta'])
                dados['motivo'] = (f"Faltam {dados['falta']} para as demandas pendentes "
                                   f"(necessário {dados['qtd_necessaria']}, em estoque {estoque}).")
            elif dias_cob is not None and dias_cob <= LEAD + MARGEM:
                dados['acao'] = 'pedir_ja'
                dados['quando'] = hoje.isoformat()
                dados['qtd_sugerida'] = max(1, _math.ceil(consumo))
                dados['motivo'] = (f"Estoque ({estoque}) cobre ~{dias_cob} dias; consumo {consumo}/mês; "
                                   f"o lab leva {LEAD} dias. Pedir agora para não faltar.")
            elif dias_cob is not None:
                quando = hoje + _td(days=max(0, dias_cob - LEAD - MARGEM))
                dados['acao'] = 'agendar'
                dados['quando'] = quando.isoformat()
                dados['qtd_sugerida'] = max(1, _math.ceil(consumo))
                dados['motivo'] = (f"Estoque ({estoque}) cobre ~{dias_cob} dias (consumo {consumo}/mês). "
                                   f"Pedir até {quando.strftime('%d/%m/%Y')} — o lab leva {LEAD} dias.")
            else:
                dados['acao'] = 'ok'
                dados['quando'] = ''
                dados['qtd_sugerida'] = 0
                dados['motivo'] = 'Sem falta nas demandas e sem histórico de consumo — sem necessidade imediata.'

    # Ordenar: pedir já primeiro, depois por falta/necessidade
    _ord = {'pedir_ja': 0, 'agendar': 1, 'ok': 2}
    lista = sorted(
        [{'tipo': t, **v} for t, v in necessidades.items()],
        key=lambda x: (_ord.get(x.get('acao'), 3), -x['falta'],
                       x.get('dias_cobertura') if x.get('dias_cobertura') is not None else 99999)
    )

    return jsonify({
        'necessidades': lista,
        'agentes_sem_guia': sorted(agentes_sem_guia),
        'agentes_fisicos': sorted(agentes_fisicos_presentes),
        'total_medicoes_pendentes': len(meds),
        'lead_time_dias': LEAD,
        'consumo_janela': JANELA,
    })


# ── Calculo de tempo (preview, sem persistir) ─────────────────────────
@controle_bp.route('/calc_tempo')
def calc_tempo():
    try:
        vol     = float(request.args.get('volume', 0))
        vazao   = float(request.args.get('vazao', 0))
        if vazao <= 0 or vol <= 0:
            return jsonify({'erro': 'volume e vazao > 0'}), 400
        tempo = vol / vazao
        return jsonify({'tempo_min': round(tempo, 2)})
    except (ValueError, TypeError):
        return jsonify({'erro': 'valores numericos invalidos'}), 400


# ── Devolucao de amostrador ao laboratorio ────────────────────────────
@controle_bp.route('/amostradores/<int:aid>/devolver', methods=['POST'])
def devolver_amostrador(aid):
    """Marca amostrador como Devolvido (sai da contagem de vencimento)."""
    init_db()
    d = request.json or {}
    obs = d.get('observacao', '')
    data_dev = d.get('data_devolucao') or datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        am = conn.execute('SELECT * FROM amostradores WHERE id=?', (aid,)).fetchone()
        if not am:
            return jsonify({'erro': 'nao encontrado'}), 404
        conn.execute("""
            UPDATE amostradores
            SET status='devolvido',
                observacao = CASE WHEN ? != '' THEN ? ELSE observacao END,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?""",
            (obs, obs, aid))
    return jsonify({'ok': True, 'data_devolucao': data_dev})


@controle_bp.route('/amostradores/devolver_lote', methods=['POST'])
def devolver_lote():
    """Marca vários amostradores como Devolvidos de uma vez."""
    init_db()
    d = request.json or {}
    ids = d.get('ids', [])
    if not ids:
        return jsonify({'erro': 'sem ids'}), 400
    obs = d.get('observacao', 'Devolvido em lote')
    placeholders = ','.join(['?'] * len(ids))
    with get_db() as conn:
        cur = conn.execute(f"""
            UPDATE amostradores
            SET status='devolvido', observacao=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})""",
            [obs] + ids)
    return jsonify({'ok': True, 'afetados': cur.rowcount})


# ── Utilitários de limpeza ────────────────────────────────────────────
@controle_bp.route('/amostradores/normalizar_status', methods=['POST'])
def normalizar_status_amostradores():
    """Normaliza todos os status ao vocabulário canônico (db.normalizar_status_amostrador).
    Remove o estado fantasma 'UTILIZADO?' e nomes de empresa gravados por engano."""
    init_db()
    total = 0
    with get_db() as conn:
        rows = conn.execute('SELECT id, status FROM amostradores').fetchall()
        for row in rows:
            sid = row['id']
            st  = row['status'] or ''
            novo = normalizar_status_amostrador(st)
            if novo != st:
                conn.execute('UPDATE amostradores SET status=? WHERE id=?', (novo, sid))
                total += 1
    return jsonify({'ok': True, 'normalizados': total, 'vocabulario': list(STATUS_AMOSTRADOR)})


@controle_bp.route('/amostradores/reconciliar_status', methods=['GET', 'POST'])
def reconciliar_status_amostradores():
    """Alinha o STATUS do amostrador aos FATOS (datas de envio/resultado) p/ deixar o
    inventário confiável. GET = prévia (nada grava); POST = aplica.
    Regras conservadoras:
      R1 'reservado' + envio          -> laboratorio  (reservado não existe no fluxo real)
      R1 'reservado' sem envio        -> disponivel
      R2 'disponivel' + envio s/ result -> laboratorio (foi enviado, status ficou atrasado)
      R3 result presente (disponivel/reservado/laboratorio) -> concluido
    NÃO mexe em 'devolvido' nem em 'concluido' já existentes (são sinais do lab)."""
    init_db()
    aplicar = (request.method == 'POST')
    mudancas = []
    ambiguos = []   # NÃO mexer: disponivel com envio antigo pode ter voltado do lab (reuso)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, codigo, tipo, status, data_envio_lab, data_resultado "
            "FROM amostradores WHERE COALESCE(arquivado,0)=0").fetchall()
        for r in rows:
            d = row_to_dict(r)
            st = (d.get('status') or '').strip()
            envio = (d.get('data_envio_lab') or '').strip()
            result = (d.get('data_resultado') or '').strip()
            novo = None
            # Só transições de ALTA CONFIANÇA (amostradores são reutilizados → datas
            # antigas não indicam o estado atual; por isso disponivel+envio NÃO vira lab).
            if st == 'reservado':
                novo = 'laboratorio' if envio else 'disponivel'   # 'reservado' não existe no fluxo
                motivo = 'reservado ' + ('com envio' if envio else 'sem envio')
            elif st == 'laboratorio' and result:
                novo = 'concluido'                                 # estava no lab e o resultado chegou
                motivo = 'resultado chegou'
            if novo and novo != st:
                mudancas.append({'id': d['id'], 'codigo': d.get('codigo'), 'tipo': d.get('tipo'),
                                 'de': st, 'para': novo, 'motivo': motivo})
            elif st == 'disponivel' and envio and not result:
                ambiguos.append({'codigo': d.get('codigo'), 'tipo': d.get('tipo'), 'envio': envio})
    if aplicar and mudancas:
        with get_db() as conn:
            for m in mudancas:
                conn.execute("UPDATE amostradores SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                             (m['para'], m['id']))
    resumo = {}
    for m in mudancas:
        k = m['de'] + ' → ' + m['para']
        resumo[k] = resumo.get(k, 0) + 1
    return jsonify({'modo': 'APLICADO' if aplicar else 'PREVIEW (nada gravado)',
                    'total': len(mudancas), 'por_transicao': resumo, 'amostra': mudancas[:60],
                    'ambiguos_disponivel_com_envio': {'qtd': len(ambiguos), 'amostra': ambiguos[:30],
                        'nota': 'NÃO alterados: amostrador reutilizado pode ter voltado do lab; revisar manualmente.'}})


@controle_bp.route('/empresas/mesclar_duplicatas', methods=['POST'])
def mesclar_duplicatas():
    """Consolida empresas com mesmo nome em um único registro."""
    init_db()
    mescladas = mesclar_empresas_duplicatas()
    return jsonify({'ok': True, 'mescladas': mescladas})


# ── Reset (cuidado!) ──────────────────────────────────────────────────
@controle_bp.route('/reset', methods=['POST'])
def reset_db():
    """Apaga todos os dados. Requer login admin + header X-Confirm: reset."""
    from flask_login import current_user
    if not current_user.is_authenticated:
        return jsonify({'erro': 'autenticação necessária'}), 401
    if getattr(current_user, 'role', '') != 'admin':
        return jsonify({'erro': 'apenas administradores podem resetar o banco'}), 403
    if request.headers.get('X-Confirm') != 'reset':
        return jsonify({'erro': 'requer header X-Confirm: reset'}), 400
    with get_db() as conn:
        for t in ('baixas', 'medicoes', 'demandas', 'amostradores', 'empresas'):
            conn.execute(f'DELETE FROM {t}')
    log.warning('[reset] banco resetado por %s (%s)', current_user.email, current_user.id)
    return jsonify({'ok': True})


# ── Export Excel ──────────────────────────────────────────────────────
@controle_bp.route('/export/amostradores')
def export_amostradores():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Amostradores'
    ws.append(['Status', 'Tipo', 'Codigo', 'Data Entrada', 'Empresa', 'Avaliador', 'Data Medicao'])
    for a in list_amostradores():
        ws.append([a.get('status',''), a.get('tipo',''), a.get('codigo',''),
                   a.get('data_entrada',''), a.get('empresa_nome',''),
                   a.get('avaliador',''), a.get('data_medicao','')])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f'amostradores_{datetime.now().strftime("%Y%m%d")}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ── Analytics / BI ───────────────────────────────────────────────────
@controle_bp.route('/analytics')
def analytics():
    """Dados consolidados para o painel de BI."""
    init_db()
    # Helpers de SQL compatíveis SQLite/PostgreSQL
    if USE_PG:
        def _mes_fmt(col):
            return f"TO_CHAR(({col})::timestamp, 'YYYY-MM')"
        def _recent_cond(col):
            return f"({col})::timestamp >= NOW() - INTERVAL '18 months'"
        _now_date  = 'CURRENT_DATE'
        _prazo_lt  = lambda d: f"prazo::date < {d}"
        def _clamp_today(expr):   # nunca passa de hoje (conclusão não pode ser futura)
            return f"LEAST(({expr})::date, CURRENT_DATE)"
    else:
        def _mes_fmt(col):
            return f"strftime('%Y-%m', {col})"
        def _recent_cond(col):
            return f"{col} >= date('now','-18 months')"
        _now_date  = "date('now')"
        _prazo_lt  = lambda d: f"prazo < {d}"
        def _clamp_today(expr):
            return f"min({expr}, date('now'))"

    with get_db() as conn:

        # Top agentes medidos (realizados)
        por_agente = [row_to_dict(r) for r in conn.execute("""
            SELECT agente, COUNT(*) AS qtd
            FROM medicoes WHERE status='realizado'
            GROUP BY agente ORDER BY qtd DESC LIMIT 12
        """).fetchall()]

        # Por tecnico (avaliador)
        por_tecnico = [row_to_dict(r) for r in conn.execute("""
            SELECT avaliador, COUNT(*) AS qtd
            FROM baixas
            WHERE avaliador IS NOT NULL AND avaliador != ''
            GROUP BY avaliador ORDER BY qtd DESC LIMIT 10
        """).fetchall()]

        # Top 10 empresas por demandas
        top_empresas = [row_to_dict(r) for r in conn.execute("""
            SELECT e.nome,
                   COUNT(d.id) AS total,
                   SUM(CASE WHEN d.status='concluida' THEN 1 ELSE 0 END) AS concluidas,
                   SUM(CASE WHEN d.status!='concluida' THEN 1 ELSE 0 END) AS pendentes
            FROM demandas d
            JOIN empresas e ON e.id = d.empresa_id
            GROUP BY e.id, e.nome
            ORDER BY total DESC LIMIT 10
        """).fetchall()]

        # Status amostradores
        status_rows = conn.execute("""
            SELECT status, COUNT(*) AS qtd FROM amostradores GROUP BY status
        """).fetchall()
        status_amostr = {r['status']: r['qtd'] for r in status_rows}

        # Status demandas
        dem_rows = conn.execute("""
            SELECT status, COUNT(*) AS qtd FROM demandas GROUP BY status
        """).fetchall()
        dem_por_status = {r['status']: r['qtd'] for r in dem_rows}

        # Evolucao mensal — OS concluídas por mês. Usa a data REAL de conclusão
        # (concluido_em_ms) quando existe; senão cai p/ prazo → criado_em pra a OS
        # concluída não sumir do gráfico (~34% das concluídas não têm data do
        # Planner — o time move pro bucket "Entregue" sem marcar 100%).
        # _clamp_today TRAVA a data em hoje: conclusão nunca cai em mês futuro
        # (era o bug do prazo futuro → OS aparecendo em Jul/26). Assim a soma do
        # gráfico bate com o total de concluídas dos KPIs.
        # NULLIF trata '' (TEXT vazio) como ausente p/ não quebrar ''::timestamp no PG.
        _cd = ("COALESCE(NULLIF(concluido_em_ms,''), NULLIF(prazo,''), "
               "NULLIF(criado_em_ms,''), NULLIF(criado_em,''))")
        _cdc = _clamp_today(_cd)
        evolucao = [row_to_dict(r) for r in conn.execute(f"""
            SELECT {_mes_fmt(_cdc)} AS mes, COUNT(*) AS qtd
            FROM demandas
            WHERE (LOWER(COALESCE(planner_bucket,'')) LIKE '%entregue%'
                   OR LOWER(COALESCE(planner_bucket,'')) LIKE '%conclu%'
                   OR status = 'concluida')
              AND origem = 'planner'
              AND {_cd} IS NOT NULL
              AND {_recent_cond(_cdc)}
            GROUP BY mes ORDER BY mes
        """).fetchall()]

        # Demandas abertas por mês — data real de criação no Planner
        demandas_por_mes = [row_to_dict(r) for r in conn.execute(f"""
            SELECT {_mes_fmt('COALESCE(criado_em_ms, criado_em)')} AS mes, COUNT(*) AS qtd
            FROM demandas
            WHERE COALESCE(criado_em_ms, criado_em) IS NOT NULL
              AND {_recent_cond('COALESCE(criado_em_ms, criado_em)')}
              AND origem = 'planner'
            GROUP BY mes ORDER BY mes
        """).fetchall()]

        # Amostradores por tipo
        por_tipo_amostrador = [row_to_dict(r) for r in conn.execute("""
            SELECT tipo AS tipo_amostrador, COUNT(*) AS qtd
            FROM amostradores GROUP BY tipo ORDER BY qtd DESC
        """).fetchall()]

        # KPIs
        total_dem  = sum(dem_por_status.values()) or 1
        concluidas = dem_por_status.get('concluida', 0)
        taxa_conclusao = round(concluidas / total_dem * 100, 1)

        dem_atrasadas = conn.execute(f"""
            SELECT COUNT(*) AS c FROM demandas
            WHERE status != 'concluida' AND prazo IS NOT NULL AND prazo != ''
              AND {_prazo_lt(_now_date)}
        """).fetchone()['c']

        total_med = conn.execute(
            "SELECT COUNT(*) AS c FROM medicoes WHERE status='realizado'"
        ).fetchone()['c']

        kpis = {
            'taxa_conclusao':     taxa_conclusao,
            'demandas_atrasadas': dem_atrasadas,
            'total_medicoes':     total_med,
            'total_demandas':     sum(dem_por_status.values()),
            'concluidas':         concluidas,
        }

    return jsonify({
        'por_agente':         por_agente,
        'por_tecnico':        por_tecnico,
        'top_empresas':       top_empresas,
        'status_amostradores': status_amostr,
        'dem_por_status':     dem_por_status,
        'evolucao_mensal':    evolucao,
        'demandas_por_mes':   demandas_por_mes,
        'por_tipo_amostrador': por_tipo_amostrador,
        'kpis':               kpis,
    })


# ── Coletas de Campo ──────────────────────────────────────────────────
from .db import (list_coletas_ruido, get_coleta_ruido, save_coleta_ruido,
                 list_coletas_quimico, get_coleta_quimico, save_coleta_quimico,
                 save_coleta_outros)

@controle_bp.route('/coletas/feitas')
def api_coletas_feitas():
    """Lista unificada de planilhas finalizadas (ruído+químico+outros),
    com o técnico de cada uma. Alimenta a aba 'Planilhas Feitas'."""
    init_db()
    from .db import list_coletas_feitas
    try:
        limit = min(int(request.args.get('limit', 300) or 300), 1000)
    except Exception:
        limit = 300
    return jsonify(list_coletas_feitas(limit))


@controle_bp.route('/coletas/visitas')
def api_visitas_feitas():
    """Lista as VISITAS finalizadas — uma linha por ida a campo, com os tipos
    medidos. Alimenta a aba 'Planilhas Feitas' (antes: uma linha por agente)."""
    init_db()
    from .db import list_visitas_feitas
    try:
        limit = min(int(request.args.get('limit', 300) or 300), 1000)
    except Exception:
        limit = 300
    return jsonify(list_visitas_feitas(limit))


def _visita_payload_campo_completo(chave):
    """Remonta, a partir do banco, o MESMO payload que o wizard envia para
    /relatorio/campo-completo ao finalizar a medição.

    É o que faz a aba 'Planilhas Feitas' devolver a planilha da visita inteira
    (ruído + químico + calor + vibração num só PDF) em vez de uma ficha por
    agente. Devolve None se a visita não tem nenhuma coleta."""
    import json as _json
    from .db import coletas_da_visita
    grupos = coletas_da_visita(chave)
    ruidos, quimicos, outros = grupos['ruido'], grupos['quimico'], grupos['outros']
    if not (ruidos or quimicos or outros):
        return None

    # Base da visita: o cabeçalho é o mesmo em todas as coletas; a primeira que
    # tiver o campo preenchido manda (nem toda tabela guarda todos eles).
    todas = ruidos + quimicos + outros
    def _prim(campo, *alts):
        for c in todas:
            for k in (campo,) + alts:
                v = c.get(k)
                if v not in (None, ''):
                    return v
        return ''
    base = {
        'empresa_nome': _prim('empresa_nome'),
        'os':           _prim('os', 'numero_os'),
        'data_coleta':  str(_prim('data_coleta'))[:10],
        'tecnico':      _prim('tecnico', 'avaliador', 'responsavel_coleta', 'tecnico_login'),
        'unidade':      _prim('unidade'),
        'cidade':       _prim('cidade'),
        'resp_empresa': _prim('resp_empresa', 'acompanhante'),
    }
    base['tecnico_mte'] = _mte_do_tecnico(base['tecnico'])

    tipos, d = [], {'base': base}

    if ruidos:
        r = ruidos[0]
        tipos.append('ruido')
        d['ruido'] = {
            'acomp':       r.get('acompanhante') or '',
            'cargo_acomp': r.get('cargo_acompanhante') or '',
            'hora_ini':    r.get('hora_inicio') or '',
            'hora_fim':    r.get('hora_termino') or '',
            'calibrador':  r.get('calibrador') or '',
            'trabalhadores': [{
                'nome':  t.get('nome') or '',
                'cargo': t.get('cargo') or '',
                'setor': t.get('setor') or '',
                'sn':    t.get('serie_dosimetro') or '',
            } for t in (r.get('trabalhadores') or [])],
        }

    # Acessórios e EPIs foram gravados como TEXTO ('Ciclone Nylon, Suporte IOM');
    # o bloco químico do relatório marca caixinhas por flag. Volta texto → flag
    # para o PDF regenerado ficar igual ao gerado em campo.
    _AC_FLAGS = [('ac_ciclone_al', 'ciclone alum'), ('ac_ciclone_ny', 'ciclone nylon'),
                 ('ac_redutor', 'redutor'), ('ac_iom', 'iom'),
                 ('ac_termo', 'termo'), ('ac_supcass', 'cassete')]
    _EPI_FLAGS = [('epi_luvas', 'luva'), ('epi_oculos', 'culos'),
                  ('epi_capacete', 'capacete'), ('epi_prot_auric', 'auricular'),
                  ('epi_resp', 'respirador'), ('epi_avental', 'avental'),
                  ('epi_macacao', 'maca')]

    def _flags(texto, pares):
        t = (texto or '').lower()
        return {k: True for k, agulha in pares if agulha in t}

    if quimicos:
        tipos.append('quimico')
        # Um agente por coleta química (o wizard salva 1 coleta por agente).
        d['quimico'] = {'agentes': [{
            **_flags(q.get('acessorios'), _AC_FLAGS),
            **_flags(q.get('epis'), _EPI_FLAGS),
            'substancias':  q.get('substancias') or '',
            'fracao':       q.get('fracao') or '',
            'tempo_exp':    q.get('tempo_exposto') or '',
            'turno':        q.get('turno') or '',
            'func_nome':    q.get('nome_funcionario') or '',
            'func_funcao':  q.get('funcao') or '',
            'func_setor':   q.get('setor') or '',
            'func_jornada': q.get('jornada') or '',
            'func_local':   q.get('local_atividade') or '',
            'func_atv':     q.get('atividade') or '',
            'ventilacao':   q.get('ventilacao') or '',
            'ambiente':     q.get('ambiente') or '',
            'meteo':        q.get('condicoes_meteo') or '',
            'temperatura':  q.get('temperatura') or '',
            'umidade':      q.get('umidade') or '',
            'outras_cond':  q.get('outras_condicoes') or '',
            'bomba':        q.get('bomba') or '',
            'id_bomba':     q.get('id_bomba') or '',
            'cal_bomba':    q.get('data_cal_bomba') or '',
            'calibrador':   q.get('id_calibrador') or '',
            'epc':          q.get('epc') or '',
            'obs':          q.get('observacao') or '',
            'amostradores': [{
                'id_amostrador': a.get('id_amostrador') or '',
                'vazao_inicial': a.get('vazao_inicial'),
                'vazao_final':   a.get('vazao_final'),
                'hora_inicio':   a.get('hora_inicio') or '',
                'hora_final':    a.get('hora_final') or '',
                'intervalos':    a.get('intervalos') or '',
            } for a in (q.get('amostradores') or [])],
        } for q in quimicos]}

    # Calor / vibração: cada linha de coletas_outros com os detalhes em dados_json
    for o in outros:
        try:
            extras = _json.loads(o.get('dados_json') or '{}') or {}
        except Exception:
            extras = {}
        tp = (o.get('tipo') or '')
        comum = {
            'acomp':    o.get('acompanhante') or '',
            'hora_ini': o.get('hora_inicio') or '',
            'hora_fim': o.get('hora_termino') or '',
            'obs':      o.get('observacao') or '',
        }
        if tp == 'calor':
            if 'calor' not in tipos:
                tipos.append('calor')
            d['calor'] = {**comum, 'setores': extras.get('ibutg_setores') or []}
        elif tp.startswith('vibracao'):
            if 'vibracao' not in tipos:
                tipos.append('vibracao')
            pontos = extras.get('vibr_pontos') or []
            sub = extras.get('tipo_vibr') or ''
            if not sub:
                sub = 'vbma' if tp.endswith('vbma') else ('vci' if tp.endswith('vci') else '')
            # 2ª linha de vibração (VCI + VMB gravados separados): junta os pontos
            ja = d.get('vibracao')
            if ja:
                ja['pontos'] = (ja.get('pontos') or []) + pontos
                if sub and ja.get('subtipo') and sub != ja['subtipo']:
                    ja['subtipo'] = 'ambos'
            else:
                d['vibracao'] = {**comum, 'subtipo': sub,
                                 'aparelho': extras.get('fonte_vibr') or '',
                                 'pontos': pontos}

    # Ordem das seções igual à do relatório gerado em campo
    ordem = ['ruido', 'quimico', 'vibracao', 'calor']
    d['tipos'] = [t for t in ordem if t in tipos]
    return d


@controle_bp.route('/coletas/visita/planilha')
def planilha_da_visita():
    """Regenera a planilha de campo COMPLETA de uma visita já finalizada —
    o mesmo PDF que o sistema gera ao finalizar a medição."""
    init_db()
    chave = (request.args.get('chave') or '').strip()
    if not chave:
        return jsonify({'erro': 'informe a chave da visita'}), 400
    payload = _visita_payload_campo_completo(chave)
    if not payload:
        return jsonify({'erro': 'visita não encontrada'}), 404
    try:
        buf, nome = _campo_completo_pdf(_sanitize_rl(payload))
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500
    # inline: abre no visualizador do browser (a tela abre em aba nova)
    return send_file(buf, as_attachment=False, download_name=nome,
                     mimetype='application/pdf')


def _coletas_dedup_plano():
    """Plano de deduplicação (read-only). Agrupa coletas que representam a MESMA
    medição — mesma OS (demanda_id; fallback no nº da OS) + mesmo tipo; químico
    também separa por substância — e marca para exclusão todas menos a MAIS NOVA
    (maior criado_em; desempate maior id). Não apaga nada."""
    init_db()
    specs = [('coletas_ruido', 'ruido'), ('coletas_quimico', 'quimico'), ('coletas_outros', 'outros')]
    grupos = {}
    with get_db() as conn:
        for tbl, tipo_tbl in specs:
            try:
                rows = conn.execute(f'SELECT * FROM {tbl}').fetchall()
            except Exception:
                rows = []
            for r in rows:
                d = row_to_dict(r)
                ident = str(d.get('demanda_id') or '').strip() or str(d.get('os') or d.get('numero_os') or '').strip()
                if not ident:
                    continue  # sem OS/demanda → não agrupa (não arrisca apagar)
                tipo_real = (d.get('tipo') or tipo_tbl) if tbl == 'coletas_outros' else tipo_tbl
                if tbl == 'coletas_quimico':
                    key = (tbl, ident, tipo_real, str(d.get('substancias') or '').strip())
                else:
                    key = (tbl, ident, tipo_real)
                grupos.setdefault(key, []).append({
                    'id': d.get('id'), 'tabela': tbl,
                    'os': d.get('os') or d.get('numero_os') or '', 'tipo': tipo_real,
                    'data_coleta': d.get('data_coleta') or '', 'criado_em': d.get('criado_em') or '',
                    'tecnico': (d.get('tecnico_login') or d.get('tecnico') or d.get('avaliador') or d.get('responsavel_coleta') or '').strip(),
                    'empresa_nome': d.get('empresa_nome') or '',
                })
    plano = []
    for key, items in grupos.items():
        if len(items) < 2:
            continue
        ordenado = sorted(items, key=lambda x: ((x.get('criado_em') or ''), x.get('id') or 0), reverse=True)
        plano.append({'tabela': key[0], 'os': ordenado[0]['os'], 'tipo': ordenado[0]['tipo'],
                      'manter': ordenado[0], 'excluir': ordenado[1:], 'qtd_excluir': len(ordenado) - 1})
    return plano


@controle_bp.route('/coletas/duplicadas')
def api_coletas_duplicadas():
    """Preview read-only: grupos de coletas duplicadas e quais seriam removidas
    (mantém a mais nova de cada OS+tipo). Não apaga nada."""
    plano = _coletas_dedup_plano()
    return jsonify({'grupos': plano, 'total_grupos': len(plano),
                    'total_excluir': sum(p['qtd_excluir'] for p in plano)})


@controle_bp.route('/coletas/dedup', methods=['POST'])
def api_coletas_dedup():
    """Remove as coletas duplicadas mantendo a MAIS NOVA de cada OS+tipo.
    Admin-only (manutenção destrutiva)."""
    if not current_user.is_authenticated:
        return jsonify({'ok': False, 'erro': 'Faça login para remover duplicadas.'}), 403
    plano = _coletas_dedup_plano()
    removidos = []
    with get_db() as conn:
        for p in plano:
            tbl = p['tabela']
            for item in p['excluir']:
                cid = item.get('id')
                if cid is None:
                    continue
                if tbl == 'coletas_ruido':
                    conn.execute('DELETE FROM coletas_ruido_func WHERE coleta_id=?', (cid,))
                    conn.execute('DELETE FROM coletas_ruido WHERE id=?', (cid,))
                elif tbl == 'coletas_quimico':
                    conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
                    conn.execute('DELETE FROM coletas_quimico WHERE id=?', (cid,))
                else:
                    conn.execute('DELETE FROM coletas_outros WHERE id=?', (cid,))
                removidos.append({'tabela': tbl, 'id': cid, 'os': item.get('os'),
                                  'tipo': item.get('tipo'), 'data': item.get('data_coleta')})
    try:
        registrar_evento('coletas_dedup', f'{len(removidos)} coleta(s) duplicada(s) removida(s) — mantida a mais nova',
                         usuario=current_user.nome if current_user.is_authenticated else 'admin', ip=request.remote_addr)
    except Exception:
        pass
    return jsonify({'ok': True, 'removidos': len(removidos), 'detalhe': removidos})


@controle_bp.route('/coletas/ruido')
def api_list_coletas_ruido():
    init_db()
    return jsonify(list_coletas_ruido(request.args.to_dict()))

def _atualizar_demanda_por_coleta(demanda_id, coleta_status=None, planejamento_id=None):
    """Atualiza status da demanda quando uma coleta é salva.
    aberta → em_andamento ao criar coleta.
    em_andamento → em_andamento (mantém) para coleta sem status.
    Qualquer status → em_andamento quando coleta é concluída (se não era concluída).
    Fecha planejamento quando coleta concluída.
    Não reverte demandas já concluídas.
    """
    # Fechar planejamento ao concluir a coleta — ANTES do guard de demanda.
    # Visita sem OS (ex.: Paraopeba) tem planejamento mas demanda_id=NULL;
    # o return abaixo deixava o planejamento aberto para sempre.
    if planejamento_id and coleta_status in ('concluida', 'concluido'):
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE planejamentos SET status='concluido', atualizado_em=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status != 'cancelado'",
                    (planejamento_id,))
                _liberar_reservas_plano(planejamento_id, conn)
        except Exception as e:
            log.warning('[coleta] erro ao fechar planejamento %s: %s', planejamento_id, e)
    if not demanda_id:
        return
    try:
        with get_db() as conn:
            row = conn.execute(
                'SELECT status, origem FROM demandas WHERE id=?', (demanda_id,)
            ).fetchone()
            if not row:
                return
            atual  = row['status']  if hasattr(row, '__getitem__') else row[0]
            origem = row['origem']  if hasattr(row, '__getitem__') else (row[1] if len(row) > 1 else '')
            # Não regredir demandas já concluídas (Planner é fonte de verdade para origem='planner')
            if atual == 'concluida':
                return
            if atual in ('aberta', 'pendente'):
                novo = 'em_andamento'
            elif coleta_status in ('concluida', 'concluido') and origem != 'planner':
                # Para demandas locais (não-Planner), marcar concluída ao concluir coleta
                novo = 'concluida'
            else:
                novo = 'em_andamento'
            conn.execute(
                f"UPDATE demandas SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                (novo, demanda_id)
            )
    except Exception as e:
        log.warning('[coleta] erro ao atualizar demanda %s: %s', demanda_id, e)


def _norm_txt(s):
    import unicodedata
    s = (s or '').lower()
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


# Palavras-chave por família de agente para casar uma coleta com a medição pendente
_TIPO_KEYWORDS = {
    'ruido':    ['ruido'],
    'calor':    ['calor', 'ibutg', 'ibtug', 'termic', 'termico', 'sobrecarga'],
    'vibracao': ['vibra'],
}
# Química não tem keywords (o agente é o NOME da substância, impossível enumerar)
# → é reconhecida por EXCLUSÃO: medição cujo agente não é de nenhuma família física.
_KW_FISICOS = ['ruido', 'dosimetr', 'calor', 'ibutg', 'ibtug', 'termic',
               'sobrecarga', 'vibra', 'ilumin']


def _baixar_medicao_pendente(demanda_id, tipo, agente_nome=None, aguardar_lab=False):
    """Ao finalizar uma planilha de campo, dá baixa na medição pendente
    correspondente (mesma demanda + tipo de agente) marcando-a como 'realizado'.
    Assim a medição sai do pool de 'medições pendentes' e não fica disponível
    para replanejamento em outro dia.

    aguardar_lab=True (química): a planilha de campo NÃO finaliza a medição —
    ela vai para 'aguardando_lab' e só vira 'realizado' quando o resultado
    chega do laboratório (lab_inbox detecta o RA pelo e-mail). A demanda
    também não conclui enquanto houver medição aguardando resultado.

    Retorna dict:
      {'baixada': id|None, 'duplicada': bool, 'tinha_pendente': bool}
    - duplicada=True quando TODAS as medições correspondentes já estavam
      finalizadas ('realizado' ou 'aguardando_lab') → planilha duplicada (trava).
    """
    res = {'baixada': None, 'duplicada': False, 'tinha_pendente': False, 'sem_match': False}
    _FINALIZADOS = ('realizado', 'aguardando_lab')
    if not demanda_id:
        return res
    fam = tipo or ''
    if fam.startswith('vibracao'):
        fam = 'vibracao'
    kws = _TIPO_KEYWORDS.get(fam)
    alvo_nome = _norm_txt(agente_nome) if agente_nome else None
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, agente, qtd_pontos_prevista, qtd_pontos_feita, status "
                "FROM medicoes WHERE demanda_id=?", (demanda_id,)
            ).fetchall()

            def _g(r, k, i):
                return r[k] if hasattr(r, 'keys') else r[i]

            def _match(ag):
                agn = _norm_txt(ag)
                if alvo_nome and (alvo_nome in agn or agn in alvo_nome):
                    return True
                if kws and any(k in agn for k in kws):
                    return True
                return False

            matched = [r for r in rows if _match(_g(r, 'agente', 1))]
            # Química sem match textual da substância (fix 03/07/2026 — antes a
            # medição ficava pendente EM SILÊNCIO e voltava pro replanejamento):
            # fallback por exclusão — se a demanda tem UMA única substância
            # química pendente, é ela. Com 2+ pendentes não chuta: sinaliza
            # sem_match p/ o caller avisar e a baixa ser feita à mão.
            if not matched and fam == 'quimico':
                pend_q = [r for r in rows
                          if _g(r, 'status', 4) not in _FINALIZADOS
                          and not any(k in _norm_txt(_g(r, 'agente', 1)) for k in _KW_FISICOS)]
                if len({_norm_txt(_g(r, 'agente', 1)) for r in pend_q}) == 1:
                    matched = pend_q
                elif pend_q:
                    res['sem_match'] = True
                    return res
            if not matched:
                return res  # medição avulsa (sem demanda planejada) → não trava
            pendentes = [r for r in matched if _g(r, 'status', 4) not in _FINALIZADOS]
            if not pendentes:
                res['duplicada'] = True
                res['tinha_pendente'] = True
                return res
            alvo = pendentes[0]
            mid = _g(alvo, 'id', 0)
            prev = _g(alvo, 'qtd_pontos_prevista', 2) or 1
            novo_status = 'aguardando_lab' if aguardar_lab else 'realizado'
            # UPDATE condicional: re-afirma o estado pendente no WHERE (mesmo padrão
            # do fix de reserva de amostrador, commit 0ad9508). Duas planilhas da
            # MESMA OS finalizando em paralelo (TOCTOU/CWE-362): ambas leem 'pendente'
            # no SELECT acima, mas só UMA consegue o UPDATE — a segunda bloqueia até o
            # commit da primeira, re-avalia o WHERE já com status finalizado e afeta 0
            # linhas. Quem perde a corrida vira 'duplicada' em vez de gravar 2× coleta.
            cur = conn.execute(
                "UPDATE medicoes SET qtd_pontos_feita=?, status=? "
                "WHERE id=? AND status NOT IN ('realizado','aguardando_lab')",
                (prev, novo_status, mid)
            )
            if (cur.rowcount or 0) == 0:
                res['duplicada'] = True
                res['tinha_pendente'] = True
                return res
            pend = conn.execute(
                "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=? AND status!='realizado'",
                (demanda_id,)
            ).fetchone()
            rest = pend['c'] if hasattr(pend, 'keys') else pend[0]
            if rest == 0:
                conn.execute("UPDATE demandas SET status='concluida' WHERE id=?", (demanda_id,))
            res['baixada'] = mid
            res['tinha_pendente'] = True
            return res
    except Exception as e:
        log.warning('[medicao] erro ao baixar pendente demanda=%s tipo=%s: %s', demanda_id, tipo, e)
        return res


def _baixar_amostradores_quimico(amostradores, empresa_id, avaliador, data_medicao):
    """Dá baixa automática nos amostradores digitados na planilha química.

    O técnico digita o código como texto livre ('EC81053A', 'ec 81053a' ou só
    '81053A') — resolve para amostradores.id com a mesma normalização
    tipo+código do lab_inbox e aplica a transição do /baixa manual
    (disponível/reservado → laboratório + empresa + avaliador + data_medicao).
    data_envio_lab fica vazia de propósito: a data REAL de envio vem do lab
    sync (e-mail enviado ao lab) ou de lançamento manual.

    Retorna {'baixados': [codigos], 'nao_encontrados': [textos digitados]}.
    """
    from .lab_inbox import _sistema_lookup, _norm
    res = {'baixados': [], 'nao_encontrados': []}
    if not amostradores:
        return res
    try:
        look = _sistema_lookup()
        alvos, vistos = [], set()
        for am in (amostradores or []):
            txt = (am or {}).get('id_amostrador') or ''
            cod = _norm(txt)
            tp = _norm((am or {}).get('tipo_amostrador'))
            if not cod:
                continue
            hit = look.get(tp + cod) or look.get(cod)
            if not hit and len(cod) >= 5:
                # técnico digitou só o final do código ('81053A' de 'EC81053A')
                cands = {d['id']: d for k, d in look.items() if k.endswith(cod)}
                if len(cands) == 1:
                    hit = next(iter(cands.values()))
            if not hit:
                res['nao_encontrados'].append(txt.strip())
                continue
            # concluído/descartado não regride — código provavelmente digitado errado
            if (hit.get('status') or '') in ('concluido', 'descartado'):
                res['nao_encontrados'].append(f"{txt.strip()} (já {hit.get('status')})")
                continue
            if hit['id'] in vistos:
                continue
            vistos.add(hit['id'])
            alvos.append(hit)
        if alvos:
            with get_db() as conn:
                for hit in alvos:
                    conn.execute(
                        "UPDATE amostradores SET status='laboratorio', "
                        "reservado_por_plano=NULL, "
                        "empresa_id=COALESCE(?, empresa_id), "
                        "avaliador=COALESCE(NULLIF(?,''), avaliador), "
                        "data_medicao=COALESCE(NULLIF(?,''), data_medicao), "
                        "atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                        (empresa_id, avaliador or '', data_medicao or '', hit['id']))
                    res['baixados'].append(hit.get('codigo'))
    except Exception as e:
        log.warning('[quimico] baixa automatica de amostradores falhou: %s', e)
    return res


def _sync_reserva_plano(pid, agentes, dry=False):
    """Reserva automática de amostradores ao confirmar um planejamento.

    Cada agente do plano com amostrador_codigo: disponível → 'reservado'
    (reservado_por_plano=pid). Reservado por OUTRO plano, em laboratório ou
    concluído/descartado → conflito (trava: quem chega depois não leva).
    'reservado' manual sem dono é adotado pelo plano. Reservas antigas deste
    plano cujo código saiu do plano voltam para 'disponivel'.

    dry=True: só resolve e lista conflitos, não escreve nada (pré-checagem
    antes de criar o plano).

    Retorna {'reservados': [], 'conflitos': [], 'liberados': [], 'nao_encontrados': []}.
    """
    from .lab_inbox import _sistema_lookup, _norm
    res = {'reservados': [], 'conflitos': [], 'liberados': [], 'nao_encontrados': []}
    if isinstance(agentes, str):
        import json as _j
        try:
            agentes = _j.loads(agentes or '[]')
        except Exception:
            agentes = []
    try:
        look = _sistema_lookup()
        alvos, vistos = [], set()
        for a in (agentes or []):
            txt = (a or {}).get('amostrador_codigo') or ''
            cod = _norm(txt)
            tp  = _norm((a or {}).get('amostrador_tipo') or (a or {}).get('tipo_amostrador'))
            if not cod:
                continue
            hit = look.get(tp + cod) or look.get(cod)
            if not hit and len(cod) >= 5:
                cands = {d['id']: d for k, d in look.items() if k.endswith(cod)}
                if len(cands) == 1:
                    hit = next(iter(cands.values()))
            if not hit:
                res['nao_encontrados'].append(txt.strip())
                continue
            if hit['id'] in vistos:
                continue
            vistos.add(hit['id'])
            alvos.append((txt.strip(), hit))
        with get_db() as conn:
            ids_alvo = {h['id'] for _, h in alvos}
            if not dry and pid:
                for r in conn.execute(
                        "SELECT id, codigo FROM amostradores "
                        "WHERE status='reservado' AND reservado_por_plano=?", (pid,)).fetchall():
                    rd = row_to_dict(r)
                    if rd['id'] not in ids_alvo:
                        conn.execute(
                            "UPDATE amostradores SET status='disponivel', reservado_por_plano=NULL, "
                            "atualizado_em=CURRENT_TIMESTAMP WHERE id=? AND status='reservado'",
                            (rd['id'],))
                        res['liberados'].append(rd.get('codigo'))
            for txt, hit in alvos:
                row = conn.execute(
                    'SELECT status, reservado_por_plano, codigo FROM amostradores WHERE id=?',
                    (hit['id'],)).fetchone()
                st = row_to_dict(row) if row else {}
                status = (st.get('status') or '').strip()
                dono = st.get('reservado_por_plano')
                if status == 'reservado' and dono and (not pid or int(dono) != int(pid)):
                    res['conflitos'].append(f"{st.get('codigo') or txt} (já reservado pelo plano #{dono})")
                    continue
                if status not in ('disponivel', 'reservado'):
                    res['conflitos'].append(f"{st.get('codigo') or txt} (status: {status or '?'})")
                    continue
                if not dry:
                    # UPDATE condicional re-afirma o status (anti-corrida TOCTOU):
                    # outro plano confirmado entre o SELECT acima e este UPDATE
                    # não pode ser sobrescrito — rowcount 0 vira conflito.
                    cur = conn.execute(
                        "UPDATE amostradores SET status='reservado', reservado_por_plano=?, "
                        "atualizado_em=CURRENT_TIMESTAMP WHERE id=? AND "
                        "(status='disponivel' OR (status='reservado' AND "
                        "(reservado_por_plano IS NULL OR reservado_por_plano=?)))",
                        (pid, hit['id'], pid))
                    if getattr(cur, 'rowcount', 1) == 0:
                        row2 = conn.execute(
                            'SELECT status, reservado_por_plano, codigo FROM amostradores WHERE id=?',
                            (hit['id'],)).fetchone()
                        st2 = row_to_dict(row2) if row2 else {}
                        dono2 = st2.get('reservado_por_plano')
                        if dono2:
                            res['conflitos'].append(
                                f"{st2.get('codigo') or txt} (já reservado pelo plano #{dono2})")
                        else:
                            res['conflitos'].append(
                                f"{st2.get('codigo') or txt} (status: {(st2.get('status') or '?').strip()})")
                        continue
                res['reservados'].append(st.get('codigo') or txt)
    except Exception as e:
        log.warning('[plano] reserva de amostradores falhou: %s', e)
    return res


def _liberar_reservas_plano(pid, conn=None):
    """Devolve ao estoque os amostradores ainda 'reservado' deste plano
    (cancelado, voltou a rascunho ou concluído sem uso — o utilizado já
    virou 'laboratorio' pela baixa da planilha química)."""
    def _run(c):
        cods = []
        for r in c.execute(
                "SELECT id, codigo FROM amostradores "
                "WHERE status='reservado' AND reservado_por_plano=?", (pid,)).fetchall():
            rd = row_to_dict(r)
            c.execute(
                "UPDATE amostradores SET status='disponivel', reservado_por_plano=NULL, "
                "atualizado_em=CURRENT_TIMESTAMP WHERE id=?", (rd['id'],))
            cods.append(rd.get('codigo'))
        return cods
    try:
        if conn is not None:
            return _run(conn)
        with get_db() as c:
            return _run(c)
    except Exception as e:
        log.warning('[plano] liberar reservas do plano %s falhou: %s', pid, e)
        return []


@controle_bp.route('/coletas/ruido', methods=['POST'])
def api_save_coleta_ruido():
    init_db()
    d = request.json or {}
    cid = save_coleta_ruido(d)
    is_new = not bool(d.get('id'))
    _atualizar_demanda_por_coleta(d.get('demanda_id'), d.get('status'), d.get('planejamento_id'))
    if is_new:
        registrar_evento('coleta_ruido_criada',
                         f'OS: {d.get("os","—")} | Empresa: {d.get("empresa_nome","—")}',
                         cid, 'coleta_ruido',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
    return jsonify({'ok': True, 'id': cid})

@controle_bp.route('/coletas/ruido/<int:cid>')
def api_get_coleta_ruido(cid):
    init_db()
    c = get_coleta_ruido(cid)
    return (jsonify(c), 200) if c else (jsonify({'erro': 'nao encontrada'}), 404)

@controle_bp.route('/coletas/ruido/<int:cid>', methods=['DELETE'])
def api_del_coleta_ruido(cid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_ruido_func WHERE coleta_id=?', (cid,))
        conn.execute('DELETE FROM coletas_ruido WHERE id=?', (cid,))
    return jsonify({'ok': True})

@controle_bp.route('/coletas/quimico')
def api_list_coletas_quimico():
    init_db()
    return jsonify(list_coletas_quimico(request.args.to_dict()))

@controle_bp.route('/coletas/quimico', methods=['POST'])
def api_save_coleta_quimico():
    init_db()
    d = request.json or {}
    cid = save_coleta_quimico(d)
    is_new = not bool(d.get('id'))
    _atualizar_demanda_por_coleta(d.get('demanda_id'), d.get('status'), d.get('planejamento_id'))
    if is_new:
        registrar_evento('coleta_quimico_criada',
                         f'OS: {d.get("os","—")} | Empresa: {d.get("empresa_nome","—")}',
                         cid, 'coleta_quimico',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
    return jsonify({'ok': True, 'id': cid})

@controle_bp.route('/coletas/quimico/<int:cid>')
def api_get_coleta_quimico(cid):
    init_db()
    c = get_coleta_quimico(cid)
    return (jsonify(c), 200) if c else (jsonify({'erro': 'nao encontrada'}), 404)

@controle_bp.route('/coletas/quimico/<int:cid>', methods=['DELETE'])
def api_del_coleta_quimico(cid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
        conn.execute('DELETE FROM coletas_quimico WHERE id=?', (cid,))
    return jsonify({'ok': True})


# ── Coletas Outros (calor, vibração, iluminamento) ────────────────────
# Nota: o GET /coletas/outros (lista com filtros) está definido acima em
# get_coletas_outros_route(). Aqui ficam apenas as rotas por id.

@controle_bp.route('/coletas/outros/<int:cid>')
def api_get_coleta_outros(cid):
    init_db()
    from .db import get_coleta_outros
    c = get_coleta_outros(cid)
    if not c:
        return jsonify({'erro': 'Não encontrado'}), 404
    return jsonify(c)


@controle_bp.route('/coletas/outros/<int:cid>', methods=['DELETE'])
def api_delete_coleta_outros(cid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_outros WHERE id=?', (cid,))
    return jsonify({'ok': True})


# ── PONTE coleta → laudo (prefill) ────────────────────────────────────
def _empresa_prefill(empresa_id):
    """Dados da empresa para o cabeçalho do laudo. Campos que não existem na
    tabela (endereco/cep/bairro/cnae/descricaoCnae) voltam como ''."""
    base = {k: '' for k in ('razaoSocial', 'cnpj', 'cidade', 'uf', 'contato',
                            'telefone', 'email', 'endereco', 'cep', 'bairro',
                            'cnae', 'descricaoCnae')}
    if not empresa_id:
        return base
    try:
        with get_db() as conn:
            row = conn.execute('SELECT * FROM empresas WHERE id=?', (empresa_id,)).fetchone()
        if not row:
            return base
        e = row_to_dict(row)
        base['razaoSocial'] = e.get('nome') or ''
        base['cnpj']        = e.get('cnpj') or ''
        base['cidade']      = e.get('cidade') or ''
        base['uf']          = e.get('uf') or ''
        base['contato']     = e.get('contato') or ''
        base['telefone']    = e.get('telefone') or ''
        base['email']       = e.get('email') or ''
        # campos que podem ter sido adicionados por migração futura
        for k in ('endereco', 'cep', 'bairro', 'cnae', 'descricaoCnae'):
            if e.get(k):
                base[k] = e.get(k)
    except Exception:
        pass
    return base


def _prefill_ruido(cid):
    c = get_coleta_ruido(cid)
    if not c:
        return None, None
    tecnico = (c.get('tecnico') or '').strip()
    trabalhadores = []
    for f in (c.get('trabalhadores') or []):
        trabalhadores.append({
            'nome':   f.get('nome') or '',
            'cargo':  f.get('cargo') or '',
            'setor':  f.get('setor') or '',
            'serie':  f.get('serie_dosimetro') or '',
            'almoco': bool(f.get('almoco')),
        })
    dados = {
        'os':              c.get('os') or '',
        'unidade':         c.get('unidade') or '',
        'cidade':          c.get('cidade') or '',
        'resp_empresa':    c.get('resp_empresa') or '',
        'dataColeta':      _iso_br(c.get('data_coleta')),
        'horaInicio':      c.get('hora_inicio') or '',
        'horaFim':         c.get('hora_termino') or '',
        'calibrador':      c.get('calibrador') or '',
        'calIni':          c.get('calibracao_inicial'),
        'calFim':          c.get('calibracao_final'),
        'desvioCalib':     c.get('desvio_calibracao'),
        'statusCalib':     c.get('status_calibracao') or '',
        'tecnico':         tecnico,
        'tecnicoMte':      _mte_do_tecnico(tecnico),
        'acompanhante':    c.get('acompanhante') or '',
        'cargoAcompanhante': c.get('cargo_acompanhante') or '',
        'trabalhadores':   trabalhadores,
    }
    faltam = ['Dose %, Lavg, NEN e histograma por trabalhador — '
              'importar o PDF do dosímetro no escritório']
    return c, ('ruido', dados, faltam)


def _prefill_quimico(cid):
    c = get_coleta_quimico(cid)
    if not c:
        return None, None
    config = {
        'bomba':      c.get('bomba') or '',
        'bombaSN':    c.get('id_bomba') or '',
        'calibrador': c.get('id_calibrador') or '',
    }
    avaliacoes = []
    for am in (c.get('amostradores') or []):
        avaliacoes.append({
            'cargo':         c.get('funcao') or '',
            'trabalhador':   c.get('nome_funcionario') or '',
            'setor':         c.get('setor') or '',
            'jornada':       c.get('jornada') or '',
            'dataColeta':    _iso_br(c.get('data_coleta')),
            'agente':        am.get('substancia') or '',
            'filtroNumero':  am.get('id_amostrador') or '',
            'vazaoInicial':  am.get('vazao_inicial'),
            'vazaoFinal':    am.get('vazao_final'),
            'vazao':         am.get('vazao_media'),
            'tempoColeta':   am.get('tempo_min'),
            'volume':        am.get('volume_L'),
            'tempoExposicao': c.get('tempo_exposto') or '',
            'acessorios':    c.get('acessorios') or '',
        })
    dados = {'config': config, 'avaliacoes': avaliacoes}
    faltam = ['Concentração e data de análise (vêm do e-mail do laboratório)',
              'PDF do laudo do laboratório']
    return c, ('quimico', dados, faltam)


def _prefill_outros(cid):
    from .db import get_coleta_outros
    c = get_coleta_outros(cid)
    if not c:
        return None, None
    try:
        extras = json.loads(c.get('dados_json') or '{}') or {}
    except Exception:
        extras = {}
    tipo = (c.get('tipo') or '').strip().lower()
    horario = ' – '.join([x for x in (c.get('hora_inicio') or '', c.get('hora_termino') or '') if x])
    if tipo.startswith('vibracao'):
        faltam = ['Vibração não tem laudo Word — use a planilha de campo']
        return c, ('vibracao', {}, faltam)
    # calor: cada item de ibutg_setores vira um setor com 1 ponto
    setores = []
    for s in (extras.get('ibutg_setores') or []):
        if not isinstance(s, dict):
            continue
        nome = s.get('setor') or ''
        ponto = {
            'local':     nome,
            'tempo':     s.get('duracao') or '',
            'tbn':       s.get('tbn'),
            'tbs':       s.get('tbs'),
            'tg':        s.get('tg'),
            'M':         s.get('M'),
            'atividade': s.get('regime') or '',
        }
        setores.append({
            'nome':       nome,
            'horario':    horario,
            'vestimenta': '',
            'pontos':     [ponto],
        })
    dados = {
        'dataAvaliacao': _iso_br(c.get('data_coleta')),
        'cidadeCarta':   '',   # preenchido com a cidade da empresa pelo caller
        'setores':       setores,
    }
    faltam = ['Equipamento/certificado/calibração do termo-higrômetro',
              'ART', 'Logo (se houver)', 'Fotos por setor']
    return c, ('calor', dados, faltam)


@controle_bp.route('/laudo/prefill')
def api_laudo_prefill():
    """PONTE coleta → laudo. Devolve os dados de uma coleta finalizada já
    mapeados para o formato que o formulário de laudo (ruído/químico/calor)
    espera, mais a lista do que ainda falta (vem do escritório/laboratório)."""
    init_db()
    cid = _int_arg('coleta_id')
    tabela = (request.args.get('tabela') or '').strip().lower()
    if cid is None:
        return jsonify({'erro': 'coleta_id inválido'}), 404
    if tabela not in ('ruido', 'quimico', 'outros'):
        return jsonify({'erro': 'tabela inválida (use ruido|quimico|outros)'}), 400

    if tabela == 'ruido':
        coleta, res = _prefill_ruido(cid)
    elif tabela == 'quimico':
        coleta, res = _prefill_quimico(cid)
    else:
        coleta, res = _prefill_outros(cid)

    if not coleta or not res:
        return jsonify({'erro': 'Coleta não encontrada'}), 404

    tipo, dados, faltam = res
    empresa = _empresa_prefill(coleta.get('empresa_id'))
    # calor: a cidade da carta de apresentação usa a cidade da empresa
    if tipo == 'calor' and isinstance(dados, dict) and not dados.get('cidadeCarta'):
        dados['cidadeCarta'] = empresa.get('cidade') or coleta.get('cidade') or ''

    return jsonify({
        'ok': True,
        'tipo': tipo,
        'empresa': empresa,
        'dados': dados,
        'faltam': faltam,
    })


# ── Fichas de Campo (HTML print) ─────────────────────────────────────
_FICHA_CSS = """
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: Arial, sans-serif; font-size: 9pt; color: #000; background:#fff; }
  .page { width:210mm; min-height:297mm; padding:12mm 14mm; margin:0 auto; }
  .logo-bar { display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #333; padding-bottom:6px; margin-bottom:10px; }
  .logo-bar .title { font-size:12pt; font-weight:bold; text-align:center; flex:1; }
  .logo-bar .sub { font-size:8pt; color:#555; text-align:right; min-width:80px; }
  .section { border:1px solid #555; margin-bottom:8px; }
  .section-title { background:#2d5fa6; color:#fff; font-weight:bold; font-size:8pt; padding:3px 8px; letter-spacing:.5px; }
  .row { display:flex; border-top:1px solid #aaa; }
  .row:first-child { border-top:none; }
  .cell { flex:1; padding:3px 6px; border-right:1px solid #ccc; }
  .cell:last-child { border-right:none; }
  .cell label { display:block; font-size:7pt; color:#555; font-weight:bold; margin-bottom:1px; }
  .cell .val { font-size:9pt; min-height:14px; }
  .cell .blank { border-bottom:1px solid #aaa; min-height:14px; }
  .w1 { flex:.5; } .w2 { flex:1; } .w3 { flex:1.5; } .w4 { flex:2; } .w6 { flex:3; }
  table.data { width:100%; border-collapse:collapse; font-size:8pt; }
  table.data th { background:#2d5fa6; color:#fff; padding:4px 6px; text-align:left; font-size:7.5pt; }
  table.data td { padding:3px 6px; border-bottom:1px solid #ccc; vertical-align:top; }
  table.data tr:nth-child(even) td { background:#f5f5f5; }
  table.data .blank-row td { height:22px; background:#fff; }
  .sig-row { display:flex; gap:20px; margin-top:12px; }
  .sig-box { flex:1; border-top:1px solid #333; padding-top:4px; font-size:8pt; text-align:center; }
  .obs-box { border:1px solid #ccc; min-height:30px; padding:4px; margin-top:0; }
  .metodologia { font-size:8pt; padding:6px 8px; line-height:1.4; }
  @media print {
    body { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
    .no-print { display:none !important; }
    .page { padding:8mm 10mm; }
  }
</style>
"""

_FICHA_RUIDO_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><title>Ficha de Campo — Ruído</title>
{{ css }}
</head>
<body>
<div class="no-print" style="background:#1a1a2e;color:#fff;padding:10px 20px;display:flex;gap:16px;align-items:center;">
  <span style="font-weight:bold;">Ficha de Campo — Ruído</span>
  <button onclick="window.print()" style="background:#f97316;color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:bold;">🖨 Imprimir / Salvar PDF</button>
  <button onclick="window.close()" style="background:#374151;color:#fff;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;">✕ Fechar</button>
</div>
<div class="page">
  <div class="logo-bar">
    <div style="font-size:9pt;font-weight:bold;min-width:90px;">OCUPACIONAL SST</div>
    <div class="title">AVALIAÇÃO QUANTITATIVA DE RUÍDO</div>
    <div class="sub">NR-15 An.1 / NHO-01<br>Ficha ID: {{ c.id }}</div>
  </div>

  <div class="section">
    <div class="section-title">IDENTIFICAÇÃO DA EMPRESA</div>
    <div class="row">
      <div class="cell w6"><label>Empresa</label><div class="val">{{ c.empresa_nome or '' }}</div></div>
      <div class="cell w2"><label>Data da Coleta</label><div class="val">{{ c.data_coleta or '' }}</div></div>
      <div class="cell w2"><label>OS / Demanda</label><div class="val">{{ c.os or '' }}</div></div>
    </div>
    <div class="row">
      <div class="cell w4"><label>Acompanhante da Inspeção</label><div class="val">{{ c.acompanhante or '' }}</div></div>
      <div class="cell w3"><label>Cargo do Acompanhante</label><div class="val">{{ c.cargo_acompanhante or '' }}</div></div>
      <div class="cell w3"><label>Profissional Técnico</label><div class="val">{{ c.tecnico or '' }}</div></div>
    </div>
    <div class="row">
      <div class="cell w2"><label>Hora de Início</label><div class="val">{{ c.hora_inicio or '___:___' }}</div></div>
      <div class="cell w2"><label>Término</label><div class="val">{{ c.hora_termino or '___:___' }}</div></div>
      <div class="cell w2"><label>Cidade</label><div class="val">{{ c.cidade or '' }}</div></div>
      <div class="cell w4"><label>Unidade</label><div class="val">{{ c.unidade or '' }}</div></div>
    </div>
    <div class="row">
      <div class="cell" style="height:28px;"><label>Assinatura do Visitado</label></div>
      <div class="cell" style="height:28px;"><label>Assinatura do Responsável Técnico</label></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">METODOLOGIA DE AVALIAÇÃO</div>
    <div class="metodologia">
      Visita técnica realizada com intuito de monitorar a exposição ocupacional ao ruído de acordo com a NR-15 Anexo 1 e NHO-01 (FUNDACENTRO).
      Os dosímetros são posicionados junto ao ouvido do trabalhador, permanecendo durante toda a jornada de trabalho.
      Será utilizado o critério de ação de 80 dB(A) e limite de 85 dB(A) conforme NR-15 An.1.
    </div>
  </div>

  <div class="section">
    <div class="section-title">EQUIPAMENTO UTILIZADO</div>
    <div class="row">
      <div class="cell w3"><label>Calibrador</label><div class="val">{{ c.calibrador or '' }}</div></div>
      <div class="cell w2"><label>Calibração Inicial (dB)</label><div class="val">{{ c.calibracao_inicial or '' }}</div></div>
      <div class="cell w2"><label>Calibração Final (dB)</label><div class="val">{{ c.calibracao_final or '' }}</div></div>
      <div class="cell w2"><label>Desvio (dB)</label><div class="val">{{ c.desvio_calibracao or '' }}</div></div>
      <div class="cell w2"><label>Status Calibração</label><div class="val">{{ c.status_calibracao or '' }}</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">TRABALHADORES AVALIADOS</div>
    <table class="data">
      <thead>
        <tr>
          <th style="width:28px;">#</th>
          <th>Nome do Trabalhador</th>
          <th>Função / Cargo</th>
          <th>Setor</th>
          <th>N° Série Dosímetro</th>
          <th style="width:40px;">Almoço</th>
          <th>Horário Coleta</th>
        </tr>
      </thead>
      <tbody>
        {% for f in c.funcionarios %}
        <tr>
          <td style="text-align:center;">{{ loop.index }}</td>
          <td>{{ f.nome or '' }}</td>
          <td>{{ f.cargo or '' }}</td>
          <td>{{ f.setor or '' }}</td>
          <td>{{ f.serie_dosimetro or '' }}</td>
          <td style="text-align:center;">{{ 'Sim' if f.almoco else 'Não' }}</td>
          <td></td>
        </tr>
        {% endfor %}
        {% for _ in range([5 - c.funcionarios|length, 0]|max) %}
        <tr class="blank-row"><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  {% if c.observacao %}
  <div class="section">
    <div class="section-title">OBSERVAÇÕES</div>
    <div class="obs-box">{{ c.observacao }}</div>
  </div>
  {% endif %}

  <div class="section">
    <div class="section-title">OBSERVAÇÕES DE CAMPO</div>
    <div class="obs-box" style="min-height:40px;"></div>
  </div>

  <div class="sig-row">
    <div class="sig-box">Assinatura do Acompanhante</div>
    <div class="sig-box">Assinatura do Responsável Técnico Ocupacional</div>
    <div class="sig-box">Responsável Técnico Ocupacional</div>
  </div>
</div>
</body></html>"""

_FICHA_QUIMICO_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><title>Ficha de Campo — Químico</title>
{{ css }}
</head>
<body>
<div class="no-print" style="background:#1a1a2e;color:#fff;padding:10px 20px;display:flex;gap:16px;align-items:center;">
  <span style="font-weight:bold;">Ficha de Campo — Agentes Químicos</span>
  <button onclick="window.print()" style="background:#f97316;color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:bold;">🖨 Imprimir / Salvar PDF</button>
  <button onclick="window.close()" style="background:#374151;color:#fff;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;">✕ Fechar</button>
</div>
<div class="page">
  <div class="logo-bar">
    <div style="font-size:9pt;font-weight:bold;min-width:90px;">OCUPACIONAL SST</div>
    <div class="title">FICHA DE AMOSTRAGEM — AGENTES QUÍMICOS</div>
    <div class="sub">NR-15 An.13 / NHO-03<br>Ficha ID: {{ c.id }}</div>
  </div>

  <div class="section">
    <div class="section-title">IDENTIFICAÇÃO DA EMPRESA AMOSTRADA</div>
    <div class="row">
      <div class="cell w6"><label>Empresa Amostrada</label><div class="val">{{ c.empresa_nome or '' }}</div></div>
      <div class="cell w3"><label>Responsável pela Coleta</label><div class="val">{{ c.responsavel_coleta or '' }}</div></div>
    </div>
    <div class="row">
      <div class="cell w3"><label>Cidade</label><div class="val">{{ c.cidade or '' }}</div></div>
      <div class="cell w3"><label>Unidade</label><div class="val">{{ c.unidade or '' }}</div></div>
      <div class="cell w2"><label>Data da Coleta</label><div class="val">{{ c.data_coleta or '' }}</div></div>
      <div class="cell w2"><label>Dia da Semana</label><div class="val">{{ c.dia_semana or '' }}</div></div>
      <div class="cell w2"><label>Turno</label><div class="val">{{ c.turno or '' }}</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">IDENTIFICAÇÃO DO LOCAL / FUNCIONÁRIO AMOSTRADO</div>
    <div class="row">
      <div class="cell w4"><label>Nome do Funcionário</label><div class="blank"></div></div>
      <div class="cell w3"><label>ID do Funcionário</label><div class="blank"></div></div>
      <div class="cell w3"><label>Jornada de Trabalho</label><div class="blank"></div></div>
    </div>
    <div class="row">
      <div class="cell w3"><label>Função</label><div class="val">{{ c.funcao or '' }}<span style="color:#ccc;">{{ '' if c.funcao else '________________' }}</span></div></div>
      <div class="cell w3"><label>Setor</label><div class="val">{{ c.setor or '' }}<span style="color:#ccc;">{{ '' if c.setor else '________________' }}</span></div></div>
      <div class="cell w4"><label>Local Específico da Atividade</label><div class="val">{{ c.local_atividade or '' }}<span style="color:#ccc;">{{ '' if c.local_atividade else '________________' }}</span></div></div>
    </div>
    <div class="row">
      <div class="cell"><label>Atividade de Trabalho</label><div class="val">{{ c.atividade or '' }}</div></div>
    </div>
    <div class="row">
      <div class="cell w3"><label>Substância(s) Avaliada(s)</label><div class="val">{{ c.substancias or '' }}</div></div>
      <div class="cell w2"><label>Fração</label><div class="val">{{ c.fracao or '' }}</div></div>
      <div class="cell w2"><label>Tempo Exposto (h)</label><div class="val">{{ c.tempo_exposto or '' }}</div></div>
      <div class="cell w3"><label>Ventilação</label><div class="val">{{ c.ventilacao or '' }}</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">EQUIPAMENTO DE AMOSTRAGEM</div>
    <div class="row">
      <div class="cell w3"><label>Bomba</label><div class="val">{{ c.bomba or '' }}</div></div>
      <div class="cell w2"><label>ID da Bomba</label><div class="val">{{ c.id_bomba or '' }}</div></div>
      <div class="cell w2"><label>Data Calibração Bomba</label><div class="val">{{ c.data_cal_bomba or '' }}</div></div>
      <div class="cell w2"><label>ID Calibrador</label><div class="val">{{ c.id_calibrador or '' }}</div></div>
      <div class="cell w3"><label>Acessórios</label><div class="val">{{ c.acessorios or '' }}</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">DADOS DA AMOSTRAGEM</div>
    <table class="data">
      <thead>
        <tr>
          <th style="width:24px;">#</th>
          <th>ID Amostrador</th>
          <th>Tipo</th>
          <th>Substância</th>
          <th>Bomba</th>
          <th>Vazão Ini (L/min)</th>
          <th>Vazão Fin (L/min)</th>
          <th>Hora Início</th>
          <th>Hora Final</th>
          <th>Tempo (min)</th>
          <th>Volume (L)</th>
        </tr>
      </thead>
      <tbody>
        {% for a in c.amostradores %}
        <tr>
          <td style="text-align:center;">{{ loop.index }}</td>
          <td>{{ a.id_amostrador or '' }}</td>
          <td>{{ a.tipo_amostrador or '' }}</td>
          <td>{{ a.substancia or '' }}</td>
          <td>{{ a.bomba or '' }}</td>
          <td style="text-align:center;">{{ a.vazao_inicial or '' }}</td>
          <td style="text-align:center;">{{ a.vazao_final or '' }}</td>
          <td style="text-align:center;">{{ a.hora_inicio or '' }}</td>
          <td style="text-align:center;">{{ a.hora_final or '' }}</td>
          <td style="text-align:center;">{{ a.tempo_min or '' }}</td>
          <td style="text-align:center;">{{ a.volume_L or '' }}</td>
        </tr>
        {% endfor %}
        {% for _ in range([4 - c.amostradores|length, 0]|max) %}
        <tr class="blank-row"><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
        {% endfor %}
      </tbody>
    </table>
    <div style="padding:4px 8px;font-size:7.5pt;color:#555;">
      * Tempo = Horário final − inicial − intervalos &nbsp;|&nbsp;
      ** Vm = (Vi + Vf) / 2 &nbsp;|&nbsp;
      *** Vol = Vm × t &nbsp;|&nbsp;
      **** ΔV = (Vi − Vf) / Vi × 100 ≤ ±5%
    </div>
  </div>

  <div class="section">
    <div class="section-title">EPI / EPC</div>
    <div class="row">
      <div class="cell"><label>EPI Utilizado</label><div class="val">{{ c.epis or '' }}</div></div>
      <div class="cell"><label>EPC</label><div class="val">{{ c.epc or '' }}</div></div>
    </div>
  </div>

  {% if c.observacao %}
  <div class="section">
    <div class="section-title">OBSERVAÇÕES</div>
    <div class="obs-box">{{ c.observacao }}</div>
  </div>
  {% endif %}

  <div class="section">
    <div class="section-title">OBSERVAÇÕES DE CAMPO</div>
    <div class="obs-box" style="min-height:35px;"></div>
  </div>

  <div class="sig-row">
    <div class="sig-box">Assinatura do Funcionário</div>
    <div class="sig-box">Assinatura do Supervisor</div>
    <div class="sig-box">Responsável Técnico Ocupacional</div>
  </div>
</div>
</body></html>"""


@controle_bp.route('/coletas/ruido/<int:cid>/ficha')
def ficha_coleta_ruido(cid):
    init_db()
    c = get_coleta_ruido(cid)
    if not c:
        return 'Coleta não encontrada', 404
    c.setdefault('funcionarios', [])
    c['data_coleta'] = _iso_br(c.get('data_coleta'))
    html = _FICHA_RUIDO_HTML.replace('{{ css }}', _FICHA_CSS)
    return render_template_string(html, c=c)


@controle_bp.route('/coletas/quimico/<int:cid>/ficha')
def ficha_coleta_quimico(cid):
    init_db()
    c = get_coleta_quimico(cid)
    if not c:
        return 'Coleta não encontrada', 404
    c.setdefault('amostradores', [])
    c['data_coleta'] = _iso_br(c.get('data_coleta'))
    html = _FICHA_QUIMICO_HTML.replace('{{ css }}', _FICHA_CSS)
    return render_template_string(html, c=c)


_FICHA_OUTROS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><title>Ficha de Campo — {{ 'Calor' if c.eh_calor else 'Vibração' }}</title>
{{ css }}
</head>
<body>
<div class="no-print" style="background:#1a1a2e;color:#fff;padding:10px 20px;display:flex;gap:16px;align-items:center;">
  <span style="font-weight:bold;">Ficha de Campo — {{ 'Calor / IBUTG' if c.eh_calor else 'Vibração' }}</span>
  <button onclick="window.print()" style="background:#f97316;color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:bold;">🖨 Imprimir / Salvar PDF</button>
  <button onclick="window.close()" style="background:#374151;color:#fff;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;">✕ Fechar</button>
</div>
<div class="page">
  <div class="logo-bar">
    <div style="font-size:9pt;font-weight:bold;min-width:90px;">OCUPACIONAL SST</div>
    <div class="title">{{ 'AVALIAÇÃO DE CALOR (IBUTG)' if c.eh_calor else 'AVALIAÇÃO DE VIBRAÇÃO' }}</div>
    <div class="sub">{{ 'NR-15 An.3 / NHO-06' if c.eh_calor else 'NR-09 / NHO-09 e NHO-10' }}<br>Ficha ID: {{ c.id }}</div>
  </div>

  <div class="section">
    <div class="section-title">IDENTIFICAÇÃO DA EMPRESA</div>
    <div class="row">
      <div class="cell w6"><label>Empresa</label><div class="val">{{ c.empresa_nome or '' }}</div></div>
      <div class="cell w2"><label>Data da Coleta</label><div class="val">{{ c.data_coleta or '' }}</div></div>
      <div class="cell w2"><label>OS / Demanda</label><div class="val">{{ c.os or '' }}</div></div>
    </div>
    <div class="row">
      <div class="cell w3"><label>Unidade</label><div class="val">{{ c.unidade or '' }}</div></div>
      <div class="cell w3"><label>Cidade</label><div class="val">{{ c.cidade or '' }}</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">DADOS DA AVALIAÇÃO</div>
    <div class="row">
      <div class="cell w3"><label>Técnico / Avaliador</label><div class="val">{{ c.avaliador or c.tecnico_login or '' }}</div></div>
      <div class="cell w3"><label>Acompanhante</label><div class="val">{{ c.acompanhante or '' }}</div></div>
      <div class="cell w1"><label>Início</label><div class="val">{{ c.hora_inicio or '' }}</div></div>
      <div class="cell w1"><label>Término</label><div class="val">{{ c.hora_termino or '' }}</div></div>
    </div>
    <div class="row">
      {% if c.eh_calor %}
      <div class="cell w3"><label>Regime de trabalho</label><div class="val">{{ c.regime or '' }}</div></div>
      <div class="cell w3"><label>Pontos avaliados</label><div class="val">{{ c.pontos or '' }}</div></div>
      {% else %}
      <div class="cell w2"><label>Tipo de vibração</label><div class="val">{{ c.tipo_vibr or (('Mãos-braços (VMB)' if 'vbma' in c.tipo else 'Corpo inteiro (VCI)') if c.tipo != 'vibracao' else '') }}</div></div>
      <div class="cell w2"><label>Fonte</label><div class="val">{{ c.fonte_vibr or '' }}</div></div>
      <div class="cell w2"><label>Pontos</label><div class="val">{{ c.pontos or '' }}</div></div>
      {% endif %}
    </div>
  </div>

  <div class="section">
    <div class="section-title">{{ c.registro_titulo }}</div>
    {% if c.registro_linhas %}
      {% for ln in c.registro_linhas %}
      <div class="row"><div class="cell w6"><div class="val">{{ ln }}</div></div></div>
      {% endfor %}
    {% else %}
      <div class="row"><div class="cell w6 blank">&nbsp;</div></div>
      <div class="row"><div class="cell w6 blank">&nbsp;</div></div>
      <div class="row"><div class="cell w6 blank">&nbsp;</div></div>
    {% endif %}
  </div>

  <div class="section">
    <div class="section-title">OBSERVAÇÕES</div>
    <div class="obs-box">{{ c.observacao or '' }}</div>
  </div>

  <div class="sig-row">
    <div class="sig-box">Técnico responsável</div>
    <div class="sig-box">Responsável da empresa</div>
  </div>
</div>
</body>
</html>"""


@controle_bp.route('/coletas/outros/<int:cid>/ficha')
def ficha_coleta_outros(cid):
    """Regenera a ficha de campo de uma coleta de calor/vibração a partir do
    que foi salvo (coletas_outros + dados_json)."""
    init_db()
    from .db import get_coleta_outros
    import json as _json
    c = get_coleta_outros(cid)
    if not c:
        return 'Coleta não encontrada', 404
    try:
        extras = _json.loads(c.get('dados_json') or '{}') or {}
    except Exception:
        extras = {}
    c['os']        = c.get('numero_os') or ''
    c['data_coleta'] = _iso_br(c.get('data_coleta'))
    c['regime']    = extras.get('regime') or ''
    c['pontos']    = extras.get('pontos') or ''
    c['tipo_vibr'] = extras.get('tipo_vibr') or ''
    c['fonte_vibr'] = extras.get('fonte_vibr') or ''
    c['tipo']      = c.get('tipo') or ''
    c['eh_calor']  = (c['tipo'] == 'calor')
    c['eh_vibr']   = c['tipo'].startswith('vibracao')

    def _linhas(lst):
        out = []
        for s in (lst or []):
            if isinstance(s, dict):
                txt = ' · '.join(f'{k}: {v}' for k, v in s.items() if v not in (None, '', []))
                if txt:
                    out.append(txt)
            elif s not in (None, ''):
                out.append(str(s))
        return out

    if c['eh_calor']:
        c['registro_titulo'] = 'IBUTG POR SETOR'
        c['registro_linhas'] = _linhas(extras.get('ibutg_setores'))
    else:
        c['registro_titulo'] = 'PONTOS DE VIBRAÇÃO'
        c['registro_linhas'] = _linhas(extras.get('vibr_pontos'))
    html = _FICHA_OUTROS_HTML.replace('{{ css }}', _FICHA_CSS)
    return render_template_string(html, c=c)


# ── Wizard: salvar medicao completa ──────────────────────────────────
def _coleta_duplicada(tipo, demanda_id, data, substancia=None):
    """True se já existe coleta para a MESMA OS (demanda) + tipo + data — químico
    também considera a substância. Evita finalizar a mesma planilha 2×.
    Sem demanda_id → não bloqueia (medição avulsa sem OS)."""
    try:
        did = int(demanda_id)
    except (TypeError, ValueError):
        return False
    d10 = str(data or '')[:10]
    if not d10:
        return False
    with get_db() as conn:
        if tipo == 'ruido':
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM coletas_ruido "
                "WHERE demanda_id=? AND substr(COALESCE(data_coleta,''),1,10)=?",
                (did, d10)).fetchone()
        elif tipo == 'quimico':
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM coletas_quimico "
                "WHERE demanda_id=? AND substr(COALESCE(data_coleta,''),1,10)=? "
                "AND COALESCE(substancias,'')=?",
                (did, d10, substancia or '')).fetchone()
        else:
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM coletas_outros "
                "WHERE demanda_id=? AND COALESCE(tipo,'')=? AND substr(COALESCE(data_coleta,''),1,10)=?",
                (did, tipo, d10)).fetchone()
        return int((row_to_dict(r).get('c') if r else 0) or 0) > 0


@controle_bp.route('/medicoes', methods=['POST'])
def api_salvar_medicao_wizard():
    """Recebe payload do wizard Central Operacional e salva em coletas_ruido ou coletas_quimico."""
    init_db()
    d = request.json or {}
    tipo = d.get('tipo', '')
    # Técnico logado — cada medição é contabilizada para quem a finalizou
    tecnico_login = (current_user.nome if current_user.is_authenticated else '') or ''

    if tipo == 'ruido':
        cr = d.get('campo_ruido') or {}
        payload_ruido = {
            'empresa_id':          d.get('empresa_id'),
            'empresa_nome':        d.get('empresa_nome', ''),
            'demanda_id':          d.get('demanda_id'),
            'acompanhante':        cr.get('acomp', ''),
            'cargo_acompanhante':  cr.get('cargo_acomp', ''),
            'tecnico':             cr.get('tecnico') or d.get('avaliador', ''),
            'tecnico_login':       tecnico_login,
            'data_coleta':         d.get('data', ''),
            'hora_inicio':         cr.get('hora_ini', ''),
            'hora_termino':        cr.get('hora_fim', ''),
            'calibracao_inicial':  _safe_float(cr.get('cal_ini')),
            'calibracao_final':    _safe_float(cr.get('cal_fim')),
            'status':              'concluida',
            'trabalhadores':       cr.get('trabalhadores', []),
            'termos':              cr.get('termos', []),
            # extras para relatório
            'calibrador':          cr.get('calibrador', ''),
            'unidade':             d.get('unidade', ''),
            'cidade':              d.get('cidade', ''),
            'resp_empresa':        d.get('resp_empresa', ''),
            'os':                  d.get('os', ''),
            'itens':               d.get('itens', []),
        }
        if _coleta_duplicada('ruido', d.get('demanda_id'), d.get('data')):
            return jsonify({'ok': False, 'duplicada': True,
                            'aviso': 'Esta planilha de ruído (mesma OS e data) já foi finalizada. Não registrada de novo.'})
        bx = _baixar_medicao_pendente(d.get('demanda_id'), 'ruido')
        if bx['duplicada']:
            return jsonify({'ok': False, 'duplicada': True,
                            'aviso': 'Esta medição de ruído já foi finalizada para esta demanda. Planilha duplicada não registrada.'})
        cid = save_coleta_ruido(payload_ruido)
        _atualizar_demanda_por_coleta(d.get('demanda_id'), 'concluida', d.get('planejamento_id'))
        return jsonify({'ok': True, 'id': cid, 'tipo': 'ruido', 'medicao_baixada': bx['baixada']})

    elif tipo == 'quimico':
        cq = d.get('campo_quimico') or {}
        # IMPORTANTE: as chaves abaixo precisam casar com as COLUNAS lidas por
        # save_coleta_quimico (db.py). Antes vinha func_nome/avaliador → gravava NULL.
        payload_q = {
            'empresa_id':         d.get('empresa_id'),
            'empresa_nome':       d.get('empresa_nome', ''),
            'demanda_id':         d.get('demanda_id'),
            'responsavel_coleta': d.get('avaliador', ''),
            'tecnico_login':      tecnico_login,
            'cidade':             d.get('cidade', ''),
            'unidade':            d.get('unidade', ''),
            'data_coleta':        d.get('data', ''),
            'status':             'concluida',
            'nome_funcionario':   cq.get('func_nome', ''),
            'funcao':             cq.get('func_funcao', ''),
            'setor':              cq.get('func_setor', ''),
            'jornada':            cq.get('func_jornada', ''),
            'local_atividade':    cq.get('func_local', ''),
            'atividade':          cq.get('func_atv', ''),
            'ventilacao':         cq.get('ventilacao', ''),
            'ambiente':           cq.get('ambiente', ''),
            'condicoes_meteo':    cq.get('meteo', ''),
            'temperatura':        cq.get('temperatura', ''),
            'umidade':            cq.get('umidade', ''),
            'outras_condicoes':   cq.get('outras_cond', ''),
            'bomba':              cq.get('bomba', ''),
            'id_bomba':           cq.get('id_bomba', ''),
            'data_cal_bomba':     cq.get('cal_bomba', ''),
            'id_calibrador':      cq.get('calibrador', ''),
            'substancias':        cq.get('substancias', ''),
            'fracao':             cq.get('fracao', ''),
            # Campos antes perdidos no banco (iam só pro PDF):
            'turno':              cq.get('turno', ''),
            'tempo_exposto':      cq.get('tempo_exp', ''),
            'epc':                cq.get('epc', ''),
            'observacao':         cq.get('obs', ''),
            'acessorios':         ', '.join(lbl for k, lbl in (
                ('ac_ciclone_al', 'Ciclone Alumínio'), ('ac_ciclone_ny', 'Ciclone Nylon'),
                ('ac_redutor', 'Redutor de Vazão'), ('ac_iom', 'Suporte IOM'),
                ('ac_termo', 'Termo-Higrômetro'), ('ac_supcass', 'Suporte Cassete'))
                if cq.get(k)),
            'epis':               ', '.join(lbl for k, lbl in (
                ('epi_luvas', 'Luvas'), ('epi_oculos', 'Óculos'), ('epi_capacete', 'Capacete'),
                ('epi_prot_auric', 'Protetor Auricular'), ('epi_resp', 'Respirador'),
                ('epi_avental', 'Avental'), ('epi_macacao', 'Macacão'))
                if cq.get(k)),
            'amostradores':       cq.get('amostradores', []),
        }
        if _coleta_duplicada('quimico', d.get('demanda_id'), d.get('data'), cq.get('substancias', '')):
            return jsonify({'ok': False, 'duplicada': True,
                            'aviso': 'Esta planilha química (mesma OS, data e substância) já foi finalizada. Não registrada de novo.'})
        # Química NÃO finaliza na planilha de campo: fica 'aguardando_lab' e a
        # baixa real acontece quando o resultado (RA) chega por e-mail (lab_inbox).
        bx = _baixar_medicao_pendente(d.get('demanda_id'), 'quimico',
                                      cq.get('substancias', ''), aguardar_lab=True)
        if bx['duplicada']:
            return jsonify({'ok': False, 'duplicada': True,
                            'aviso': 'Esta medição química já foi finalizada para esta demanda. Planilha duplicada não registrada.'})
        cid = save_coleta_quimico(payload_q)
        # Baixa automática dos amostradores usados (antes SÓ existia no botão manual
        # "Dar Baixa" — a planilha gravava o uso mas o estoque não saía do lugar)
        bxa = _baixar_amostradores_quimico(
            cq.get('amostradores') or [], d.get('empresa_id'),
            d.get('avaliador', ''), d.get('data', ''))
        if bxa['baixados'] or bxa['nao_encontrados']:
            registrar_evento(
                'amostrador_baixa_auto',
                f"planilha química #{cid}: {len(bxa['baixados'])} p/ laboratório"
                + (f" ({', '.join(bxa['baixados'])})" if bxa['baixados'] else '')
                + (f"; não reconhecidos: {', '.join(bxa['nao_encontrados'])}" if bxa['nao_encontrados'] else ''),
                cid, 'coleta_quimico', tecnico_login or 'sistema', request.remote_addr)
        if bx.get('sem_match'):
            registrar_evento(
                'medicao_sem_baixa',
                f"planilha química #{cid}: substância '{cq.get('substancias', '') or '—'}' não casou "
                f"com nenhuma medição pendente da OS (2+ substâncias pendentes) — baixa manual necessária",
                cid, 'coleta_quimico', tecnico_login or 'sistema', request.remote_addr)
        if bx.get('baixada'):
            registrar_evento(
                'medicao_aguardando_lab',
                f"planilha química #{cid}: medição aguardando resultado do laboratório "
                f"(baixa automática quando o RA chegar por e-mail)",
                bx['baixada'], 'medicao', tecnico_login or 'sistema', request.remote_addr)
        _atualizar_demanda_por_coleta(d.get('demanda_id'), 'concluida', d.get('planejamento_id'))
        return jsonify({'ok': True, 'id': cid, 'tipo': 'quimico', 'medicao_baixada': bx['baixada'],
                        'medicao_sem_match': bx.get('sem_match', False),
                        'amostradores_baixados': bxa['baixados'],
                        'amostradores_nao_reconhecidos': bxa['nao_encontrados']})

    elif tipo in ('calor', 'vibracao', 'vibracao_vci', 'vibracao_vbma'):
        import json as _json
        from .db import save_coleta_outros
        gen = d.get('campo_generico') or {}
        ibutg_setores = gen.get('ibutg_setores') or []
        payload_out = {
            'tipo':         tipo,
            'empresa_id':   d.get('empresa_id'),
            'empresa_nome': d.get('empresa_nome', ''),
            'demanda_id':   d.get('demanda_id'),
            'numero_os':    d.get('os', ''),
            'avaliador':    d.get('avaliador', ''),
            'tecnico_login': tecnico_login,
            'data_coleta':  d.get('data', ''),
            'acompanhante': gen.get('acomp', ''),
            'hora_inicio':  gen.get('hora_ini', ''),
            'hora_termino': gen.get('hora_fim', ''),
            'unidade':      d.get('unidade', ''),
            'cidade':       d.get('cidade', ''),
            'observacao':   gen.get('obs', ''),
            'status':       'concluida',
            # campos específicos de cada tipo salvos como extras → dados_json
            'pontos':       d.get('pontos') or gen.get('pontos', ''),
            'regime':       d.get('regime') or gen.get('regime', ''),
            'tipo_vibr':    d.get('tipo_vibr') or gen.get('tipo_vibr', ''),
            'fonte_vibr':   d.get('fonte_vibr') or gen.get('fonte_vibr', ''),
            # Dados detalhados da medição → viram extras em dados_json (save_coleta_outros)
            'ibutg_setores': ibutg_setores,                 # calor: TBS/TBN/TG/IBUTG por setor
            'vibr_pontos':   gen.get('vibr_pontos', []),    # vibração: trabalhadores/pontos
        }
        if _coleta_duplicada(tipo, d.get('demanda_id'), d.get('data')):
            _lbl = 'de vibração' if tipo.startswith('vibracao') else 'de calor'
            return jsonify({'ok': False, 'duplicada': True,
                            'aviso': f'Esta planilha {_lbl} (mesma OS e data) já foi finalizada. Não registrada de novo.'})
        bx = _baixar_medicao_pendente(d.get('demanda_id'), tipo)
        if bx['duplicada']:
            _lbl = 'vibração' if tipo.startswith('vibracao') else 'de calor'
            return jsonify({'ok': False, 'duplicada': True,
                            'aviso': f'Esta medição {_lbl} já foi finalizada para esta demanda. Planilha duplicada não registrada.'})
        cid = save_coleta_outros(payload_out)
        _atualizar_demanda_por_coleta(d.get('demanda_id'), 'concluida', d.get('planejamento_id'))
        return jsonify({'ok': True, 'id': cid, 'tipo': tipo, 'medicao_baixada': bx['baixada']})

    # Tipo não reconhecido: NUNCA responder ok:true (o front interpretaria como
    # planilha salva). ok:false + status 200 — o ctrlFetch lança em não-2xx, então
    # mantemos 200 e sinalizamos a falha no corpo p/ o front avisar sem quebrar.
    return jsonify({'ok': False, 'erro': f'Tipo de medição não reconhecido: {tipo!r}. Nada foi salvo.',
                    'tipo': tipo})


def _safe_float(v):
    try: return float(str(v).replace(',', '.'))
    except: return None


def _fotos_pdf_flowables(fotos, W=None):
    """Seção 'Registro Fotográfico da Atividade' a partir de uma lista de
    fotos (data-URLs base64 ou dicts {data, legenda}). Retorna flowables
    reportlab prontos para .append/.extend. Lista vazia => [] (não desenha)."""
    out = []
    if not fotos:
        return out
    try:
        import base64 as _b64
        from io import BytesIO as _BIO
        from reportlab.platypus import (Image as _RLImg, Table as _Tbl,
                                         TableStyle as _TS, Paragraph as _P, Spacer as _Sp)
        from reportlab.lib.units import cm as _cm
        from reportlab.lib.styles import ParagraphStyle as _PS
        from reportlab.lib import colors as _col
    except Exception:
        return out

    if W is None:
        try:
            from reportlab.lib.pagesizes import A4 as _A4
            W = _A4[0] - 3.6 * _cm
        except Exception:
            W = 17.4 * _cm

    cap_sty = _PS('fcap', fontName='Helvetica', fontSize=7, leading=9,
                  textColor=_col.HexColor('#555555'))
    crg_sty = _PS('fcrg', fontName='Helvetica-Bold', fontSize=7.5, leading=10,
                  textColor=_col.HexColor('#1E3A8A'))
    hdr_sty = _PS('fhdr', fontName='Helvetica-Bold', fontSize=9, leading=12)

    # Cabeçalho do grupo de cargo (mais destacado que a legenda da foto)
    grp_sty = _PS('fgrp', fontName='Helvetica-Bold', fontSize=9, leading=12,
                  textColor=_col.HexColor('#FFFFFF'))

    col_w = W / 2.0
    img_w = col_w - 0.5 * _cm

    # Agrupa as fotos por cargo, preservando a ordem de chegada
    grupos = {}   # cargo -> [cells]
    ordem = []    # ordem dos cargos
    for f in fotos:
        if isinstance(f, dict):
            src = f.get('data') or f.get('src') or ''
            leg = (f.get('legenda') or f.get('caption') or '').strip()
            crg = (f.get('cargo') or f.get('funcao') or '').strip()
        else:
            src = f or ''
            leg = ''
            crg = ''
        if not src:
            continue
        try:
            raw = _b64.b64decode(str(src).split(',')[-1])
            im = _RLImg(_BIO(raw), width=img_w, height=6.0 * _cm, kind='proportional')
        except Exception:
            continue
        parts = [im]
        if leg:
            parts += [_Sp(1, 2), _P(leg, cap_sty)]
        cell = parts if leg else im
        key = crg or ' '   # sem cargo => grupo genérico (não imprime título)
        if key not in grupos:
            grupos[key] = []
            ordem.append(key)
        grupos[key].append(cell)

    if not ordem:
        return out

    def _tabela_fotos(cells):
        rows = []
        for i in range(0, len(cells), 2):
            pair = cells[i:i + 2]
            if len(pair) == 1:
                pair.append('')
            rows.append(pair)
        t = _Tbl(rows, colWidths=[col_w, col_w])
        t.setStyle(_TS([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        return t

    def _faixa_cargo(txt):
        t = _Tbl([[_P(txt, grp_sty)]], colWidths=[W])
        t.setStyle(_TS([
            ('BACKGROUND', (0, 0), (-1, -1), _col.HexColor('#1E3A8A')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return t

    out.append(_Sp(1, 10))
    out.append(_P('REGISTRO FOTOGRÁFICO DA ATIVIDADE', hdr_sty))
    out.append(_Sp(1, 4))
    for key in ordem:
        if key.strip():   # tem cargo => imprime faixa-título do cargo
            out.append(_Sp(1, 4))
            out.append(_faixa_cargo(key.upper()))
            out.append(_Sp(1, 3))
        out.append(_tabela_fotos(grupos[key]))
    return out


def _pdf_header(W, titulo, sub_norma=''):
    """Cabeçalho padrão das planilhas de campo PDF.
    Logo Ocupacional à esquerda (com subtítulo "Medicina e Segurança do
    Trabalho") e título do documento à direita. Se o PNG do logo não
    existir, cai no texto "OCUPACIONAL"."""
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    AZUL = colors.HexColor('#1E3A8A')
    _base = getSampleStyleSheet()['Normal']
    sty_esq = ParagraphStyle('_pdfhdr_l', parent=_base, fontSize=10,
                             fontName='Helvetica-Bold', textColor=AZUL)
    sty_dir = ParagraphStyle('_pdfhdr_r', parent=_base, fontSize=10,
                             fontName='Helvetica-Bold', textColor=AZUL,
                             alignment=TA_RIGHT)
    esq = None
    try:
        import os as _os
        logo_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            'static', 'logo-ocupacional-print.png')
        if _os.path.exists(logo_path):
            from reportlab.platypus import Image as RLImage
            from reportlab.lib.utils import ImageReader
            _iw, _ih = ImageReader(logo_path).getSize()   # proporção real do PNG
            _h = 0.95 * cm
            _w = (_iw / float(_ih)) * _h
            logo = RLImage(logo_path, width=_w, height=_h)
            logo.hAlign = 'LEFT'
            sub_p = Paragraph('<font size="7" color="#64748B">Medicina e '
                              'Segurança do Trabalho</font>', _base)
            # coluna mais larga que o logo p/ o subtítulo caber em 1 linha
            esq = Table([[logo], [sub_p]], colWidths=[max(_w, 6.2 * cm)])
            esq.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ]))
    except Exception:
        esq = None
    if esq is None:
        esq = Paragraph('<b>OCUPACIONAL</b><br/><font size="7" '
                        'color="#64748B">Medicina e Segurança do '
                        'Trabalho</font>', sty_esq)
    dir_txt = f'<b>{titulo}</b>'
    if sub_norma:
        dir_txt += f'<br/><font size="7">{sub_norma}</font>'
    t = Table([[esq, Paragraph(dir_txt, sty_dir)]],
              colWidths=[W * 0.55, W * 0.45])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 0), (-1, -1), 1.5, AZUL),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
    ]))
    return t


def _calor_setores_flowables(setores, W):
    """Tabela de setores p/ planilha de CALOR (IBUTG) + legenda.
    Reutilizada pela planilha individual de calor e pela seção calor da
    planilha de campo completa. Pad até no mínimo 6 linhas (preenchimento
    à mão em campo)."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    AZUL_CLR = colors.HexColor('#DBEAFE')
    CINZA    = colors.HexColor('#F3F4F6')
    BORDA    = colors.HexColor('#CBD5E1')
    BRANCO   = colors.white
    PRETO    = colors.black
    cab_sty = ParagraphStyle('_cal_hdr', fontName='Helvetica-Bold', fontSize=7,
                             textColor=PRETO, alignment=1)
    cel_sty = ParagraphStyle('_cal_cel', fontName='Helvetica', fontSize=7,
                             textColor=PRETO, alignment=1)
    leg_sty = ParagraphStyle('_cal_leg', fontName='Helvetica', fontSize=6.5,
                             textColor=colors.HexColor('#555555'))
    # Campo = só o que se mede em campo. As temperaturas (TBN/TBS/TG) e o IBUTG
    # saem no histograma do equipamento e são lançados no escritório (Laudo de
    # Calor) — por isso não entram na planilha de campo.
    heads = ['#', 'Setor', 'Duração (min)', 'Taxa Metab. (W)', 'Regime']
    keys = ['setor', 'duracao', 'M', 'regime']
    rows = [[Paragraph(h, cab_sty) for h in heads]]
    pl = list(setores or [])   # só os setores preenchidos (sem linhas em branco)
    for i, s in enumerate(pl, 1):
        row = [Paragraph(str(i), cel_sty)]
        for k in keys:
            v = s.get(k, '') if isinstance(s, dict) else ''
            row.append(Paragraph('' if v in (None, '') else str(v), cel_sty))
        rows.append(row)
    colw = [W*0.05, W*0.33, W*0.15, W*0.18, W*0.29]
    t = Table(rows, colWidths=colw, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), AZUL_CLR),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDA),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRANCO, CINZA]),
    ]))
    out = [t, Spacer(1, 3)]
    out.append(Paragraph(
        'As temperaturas (TBN/TBS/TG) e o IBUTG saem no histograma do equipamento '
        'e são lançados no escritório (Laudo de Calor).  ·  NR-15 Anexo 3 / NHO-06 FUNDACENTRO',
        leg_sty))
    return out


def _quimico_agente_flowables(ag, idx, W, data_fmt='', dia_semana=''):
    """Blocos por agente da planilha de campo QUÍMICA (identificação do
    local/funcionário, condições ambientais, equipamentos, dados da
    amostragem c/ tabela t/Vm/Vol/ΔV, EPI/EPC, observação).

    Reutilizada pela planilha individual (gerar_relatorio_quimico) e pela
    seção química da planilha de campo completa, para não divergirem.
    Retorna lista de flowables. `idx` é base-0."""
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    AZUL    = colors.HexColor('#1E3A8A')
    AZUL_C  = colors.HexColor('#DBEAFE')
    CINZA   = colors.HexColor('#F3F4F6')
    BORDA   = colors.HexColor('#CBD5E1')
    BRANCO  = colors.white
    PRETO   = colors.black

    styles = getSampleStyleSheet()
    def _s(name, **kw):
        s = styles[name].clone(name + '_qa_' + str(id(kw)))
        for k, v in kw.items():
            setattr(s, k, v)
        return s
    norm  = _s('Normal', fontSize=8, leading=11)
    small = _s('Normal', fontSize=7, leading=10, textColor=colors.HexColor('#555555'))
    bold  = _s('Normal', fontSize=8, leading=11, fontName='Helvetica-Bold')
    ctr   = _s('Normal', fontSize=8, alignment=TA_CENTER)

    def _hdr_tbl(label):
        return Table([[Paragraph(label, _s('Normal', fontSize=9,
                      fontName='Helvetica-Bold', textColor=BRANCO))]],
                     colWidths=[W],
                     style=TableStyle([
                         ('BACKGROUND', (0, 0), (-1, -1), AZUL),
                         ('LEFTPADDING', (0, 0), (-1, -1), 6),
                         ('TOPPADDING', (0, 0), (-1, -1), 4),
                         ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                     ]))

    def _row2(l1, v1, l2='', v2=''):
        def cell(lbl, val):
            return Paragraph(f'<b>{lbl}</b> {val or "___________"}', norm)
        return Table([[cell(l1, v1), cell(l2, v2)]],
                     colWidths=[W/2, W/2],
                     style=TableStyle([
                         ('LEFTPADDING', (0, 0), (-1, -1), 4),
                         ('TOPPADDING', (0, 0), (-1, -1), 2),
                         ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                         ('BOX', (0, 0), (-1, -1), 0.3, BORDA),
                         ('INNERGRID', (0, 0), (-1, -1), 0.3, BORDA),
                     ]))

    def _full(label, val):
        return Table([[Paragraph(f'<b>{label}</b> {val or "___________"}', norm)]],
            colWidths=[W], style=TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('BOX', (0, 0), (-1, -1), 0.3, BORDA)]))

    def _norm(s):
        return (s or '').lower().replace('–', '-').replace('—', '-').replace(' ', '').replace('ã', 'a')

    def _chk(options, selected_list):
        parts = []
        sel_norm = [_norm(s) for s in selected_list]
        for opt in options:
            mark = '[X]' if _norm(opt) in sel_norm else '[  ]'
            parts.append(f'<b>{mark}</b> {opt}')
        return '   '.join(parts)

    out = []

    # Título
    _subst   = (ag.get('substancias') or ag.get('agente') or '').strip()
    _func_nm = (ag.get('func_nome') or '').strip()
    ag_func  = ag.get('func_funcao', '')
    if _subst and _func_nm:
        ag_titulo = f'{_subst} — {_func_nm}' + (f' ({ag_func})' if ag_func else '')
    elif _subst:
        ag_titulo = _subst
    else:
        ag_titulo = (_func_nm or f'Agente {idx+1}') + (f' — {ag_func}' if ag_func else '')
    out.append(Paragraph(f'AGENTE {idx+1}: {ag_titulo}', _s('Heading1',
        fontSize=10, textColor=AZUL, spaceBefore=6, spaceAfter=4)))

    # ── Identificação do Local / Funcionário ──
    out.append(_hdr_tbl('IDENTIFICAÇÃO DO LOCAL / FUNCIONÁRIO AMOSTRADO'))
    dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    dia_sel = [dia_semana] if dia_semana else []
    out.append(Table([
        [Paragraph(f'<b>Data da coleta:</b> {data_fmt}', norm),
         Paragraph(_chk(dias, dia_sel), norm)]
    ], colWidths=[W*0.29, W*0.71],
    style=TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 4), ('TOPPADDING', (0, 0), (-1, -1), 2),
                      ('BOTTOMPADDING', (0, 0), (-1, -1), 2), ('BOX', (0, 0), (-1, -1), 0.3, BORDA),
                      ('INNERGRID', (0, 0), (-1, -1), 0.3, BORDA)])))
    out.append(_row2('Turno:', ag.get('turno') or ag.get('func_turno', ''),
                     'Nome do Funcionário:', ag.get('func_nome', '')))
    out.append(_row2('Função:', ag.get('func_funcao', ''), 'Setor:', ag.get('func_setor', '')))
    out.append(_row2('Local específico:', ag.get('local') or ag.get('func_local', ''),
                     'Jornada de Trabalho:', ag.get('jornada') or ag.get('func_jornada', '')))
    out.append(_full('Atividade de Trabalho:', ag.get('atividade') or ag.get('func_atv', '')))

    # ── Condições ambientais ──
    vent_ops  = ['Natural', 'Motor', 'Refrigerada']
    amb_ops   = ['Aberto', 'Fechado', 'Semi Aberto']
    meteo_ops = ['Sol', 'Chuva', 'Nublado']
    vent_val  = _norm(ag.get('ventilacao', ''))
    amb_val   = _norm(ag.get('ambiente', ''))
    met_val   = _norm(ag.get('meteo', ''))
    vent_sel  = [k for k in vent_ops  if _norm(k) == vent_val]
    amb_sel   = [k for k in amb_ops   if _norm(k) == amb_val]
    met_sel   = [k for k in meteo_ops if _norm(k) == met_val]
    out.append(Table([
        [Paragraph(f'<b>Ventilação:</b> {_chk(list(vent_ops), vent_sel)}', norm),
         Paragraph(f'<b>Ambiente:</b> {_chk(list(amb_ops), amb_sel)}', norm),
         Paragraph(f'<b>Meteorologia:</b> {_chk(list(meteo_ops), met_sel)}', norm)]
    ], colWidths=[W/3, W/3, W/3],
    style=TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 4), ('TOPPADDING', (0, 0), (-1, -1), 2),
                      ('BOTTOMPADDING', (0, 0), (-1, -1), 2), ('BOX', (0, 0), (-1, -1), 0.3, BORDA),
                      ('INNERGRID', (0, 0), (-1, -1), 0.3, BORDA)])))
    out.append(_row2('Temperatura (°C):', ag.get('temperatura', ''),
                     'Umidade Relativa (%):', ag.get('umidade', '')))
    out.append(_full('Outras condições ambientais:', ag.get('outras_cond', '')))
    out.append(Spacer(1, 5))

    # ── Equipamentos ──
    out.append(_hdr_tbl('EQUIPAMENTOS UTILIZADOS PARA AMOSTRAGEM'))
    bomba_nome = ag.get('bomba', '')
    bombas_todos = ['BDX–II–GILLIAN', 'AIRLITE–SKC', 'FORMIS–TURAM', 'INLITE–VENTUSPRO']
    _BOMBA_KEYS = {'BDX–II–GILLIAN': ('bdx', 'gilian', 'gillian'),
                   'AIRLITE–SKC': ('skc', 'airlite'),
                   'FORMIS–TURAM': ('formis', 'turam'),
                   'INLITE–VENTUSPRO': ('inlite', 'ventus')}
    _bn = _norm(bomba_nome) if bomba_nome else ''
    bomba_sel = [b for b in bombas_todos if _bn and any(k in _bn for k in _BOMBA_KEYS[b])]
    bomba_outro = bomba_nome if (bomba_nome and not bomba_sel) else ''
    out.append(Table([[
        Paragraph(f'<b>Bomba Utilizada:</b><br/>{_chk(bombas_todos, bomba_sel)}'
                  + (f'<br/><font size="7">Outro: {bomba_outro}</font>' if bomba_outro else ''), norm),
        Table([
            [Paragraph(f'<b>ID Bomba:</b> {ag.get("id_bomba", "") or "___________"}', norm)],
            [Paragraph(f'<b>Data de calibração:</b> {ag.get("cal_bomba", "") or "___/___/____"}', norm)],
        ], colWidths=[W*0.483], style=TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    ]], colWidths=[W*0.517, W*0.483],
    style=TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 4), ('TOPPADDING', (0, 0), (-1, -1), 2),
                      ('BOTTOMPADDING', (0, 0), (-1, -1), 2), ('BOX', (0, 0), (-1, -1), 0.3, BORDA),
                      ('INNERGRID', (0, 0), (-1, -1), 0.3, BORDA),
                      ('VALIGN', (0, 0), (-1, -1), 'TOP')])))

    cal_nome = ag.get('calibrador', '')
    cal_todos = ['Defender 510M S/N: 126958', 'TSI 4143F – 41432019013']
    cal_sel   = [c for c in cal_todos if cal_nome and c[:8].lower() in cal_nome.lower()]
    out.append(_full('ID Calibrador:', _chk(cal_todos, cal_sel)
                     + (f'  Outro: {cal_nome}' if cal_nome and not cal_sel else '')))

    acs = ['CICLONE DE ALUMÍNIO', 'CICLONE DE NYLON', 'REDUTOR DE VAZÃO',
           'SUPORTE IOM', 'TERMO-HIGRÔMETRO', 'SUPORTE PARA CASSETE']
    ac_sel = []
    if ag.get('ac_ciclone_al'): ac_sel.append('CICLONE DE ALUMÍNIO')
    if ag.get('ac_ciclone_ny'): ac_sel.append('CICLONE DE NYLON')
    if ag.get('ac_redutor'):    ac_sel.append('REDUTOR DE VAZÃO')
    if ag.get('ac_iom'):        ac_sel.append('SUPORTE IOM')
    if ag.get('ac_termo'):      ac_sel.append('TERMO-HIGRÔMETRO')
    if ag.get('ac_supcass'):    ac_sel.append('SUPORTE PARA CASSETE')
    out.append(_full('Acessórios:', _chk(acs, ac_sel)))
    out.append(Spacer(1, 5))

    # ── Dados da Amostragem ──
    out.append(_hdr_tbl('DADOS DA AMOSTRAGEM'))
    fracoes = ['TOTAL', 'RESPIRÁVEL', 'TORÁCICA', 'INALÁVEL']
    frac_val = ag.get('fracao', '')
    frac_sel = [f for f in fracoes if frac_val and f.lower() in frac_val.lower()]
    out.append(_full('Substância(s) amostrada(s):', ag.get('substancias', '')))
    out.append(_full('Fração amostrada:', _chk(fracoes, frac_sel)))
    out.append(_full('Tempo exposto ao agente:', ag.get('tempo_exp', '')))

    # Tabela de amostradores (t/Vm/Vol/ΔV com subtração de intervalos)
    amostr = ag.get('amostradores', [])
    th_style = [('BACKGROUND', (0, 0), (-1, 0), AZUL_C), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 6.5), ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('GRID', (0, 0), (-1, -1), 0.3, BORDA),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRANCO, CINZA]),
                ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]
    tbl_rows = [[
        Paragraph('#', bold), Paragraph('ID Amostrador', bold),
        Paragraph('Vi\n(L/min)', bold), Paragraph('Vf\n(L/min)', bold),
        Paragraph('Início', bold), Paragraph('Final', bold),
        Paragraph('Intervalos', bold), Paragraph('t\n(min)', bold),
        Paragraph('Vm\n(L/min)', bold), Paragraph('Vol\n(L)', bold),
        Paragraph('ΔV%', bold),
    ]]
    if amostr:
        def _hhmm_to_min(s):
            try:
                h, m = str(s).split(':')[:2]
                return int(h) * 60 + int(m)
            except Exception:
                return None
        for ai, am in enumerate(amostr):
            id_am  = am.get('id_amostr') or am.get('id_amostrador') or ''
            inicio = am.get('inicio') or am.get('hora_inicio') or ''
            fim    = am.get('fim') or am.get('hora_final') or ''
            _vi_raw = am.get('vi') if am.get('vi') not in (None, '') else am.get('vazao_inicial')
            _vf_raw = am.get('vf') if am.get('vf') not in (None, '') else am.get('vazao_final')
            try:    vi = float(str(_vi_raw).replace(',', '.')) if _vi_raw not in (None, '') else None
            except Exception: vi = None
            try:    vf = float(str(_vf_raw).replace(',', '.')) if _vf_raw not in (None, '') else None
            except Exception: vf = None
            t_min = am.get('t')
            if t_min in (None, ''):
                a, b = _hhmm_to_min(inicio), _hhmm_to_min(fim)
                t_min = (b - a) if (a is not None and b is not None and b >= a) else ''
                if t_min != '':
                    t_min = max(0, t_min - _parse_intervalos_min(am.get('intervalos')))
            vm = am.get('vm')
            if vm in (None, ''):
                vm = round((vi + vf) / 2, 3) if (vi and vf) else ''
            vol = am.get('vol')
            if vol in (None, '') and vm not in (None, '') and t_min not in (None, ''):
                try: vol = round(float(vm) * float(t_min), 1)
                except Exception: vol = ''
            dv = am.get('dv')
            if dv in (None, '') and vi and vf:
                dv = f'{abs(vi - vf) / vi * 100:.1f}'
            _fmt = lambda x: ('' if x in (None, '') else str(x))
            # ΔV fora de ±5% (NR/NHO) sai em vermelho + negrito no laudo
            _dv_str = _fmt(dv)
            try:
                _dv_fora = _dv_str != '' and float(str(_dv_str).replace('%', '').replace(',', '.')) > 5
            except Exception:
                _dv_fora = False
            _dv_cell = Paragraph(f'<font color="#C0142C"><b>{_dv_str}</b></font>' if _dv_fora else _dv_str, norm)
            tbl_rows.append([
                Paragraph(str(ai + 1), norm),
                Paragraph(_fmt(id_am), norm),
                Paragraph(_fmt(_vi_raw if _vi_raw not in (None, '') else ''), norm),
                Paragraph(_fmt(_vf_raw if _vf_raw not in (None, '') else ''), norm),
                Paragraph(_fmt(inicio), norm),
                Paragraph(_fmt(fim), norm),
                Paragraph(_fmt(am.get('intervalos', '')), norm),
                Paragraph(_fmt(t_min), norm),
                Paragraph(_fmt(vm), norm),
                Paragraph(_fmt(vol), norm),
                _dv_cell,
            ])
    else:
        tbl_rows.append([Paragraph('—', ctr)] * 11)
    col_ws = [W*0.029, W*0.149, W*0.086, W*0.086, W*0.080,
              W*0.080, W*0.109, W*0.080, W*0.086, W*0.086, W*0.086]
    out.append(Table(tbl_rows, colWidths=col_ws, style=TableStyle(th_style)))
    out.append(Spacer(1, 3))
    out.append(Paragraph(
        '* t = hora final − hora inicial − intervalos (min) &nbsp;&nbsp;'
        '** Vm = (Vi+Vf)/2 &nbsp;&nbsp;'
        '*** Vol = Vm × t &nbsp;&nbsp;'
        '**** ΔV = |Vi−Vf|/Vi × 100 (máx ±5%)', small))
    out.append(Spacer(1, 5))

    # ── EPI / EPC ──
    out.append(_hdr_tbl('EQUIPAMENTO DE PROTEÇÃO INDIVIDUAL UTILIZADO'))
    epis = ['LUVAS', 'ÓCULOS DE SEGURANÇA', 'CAPACETE', 'PROTETOR AURICULAR',
            'RESPIRADOR', 'AVENTAL', 'MACACÃO IMPERMEÁVEL']
    epi_sel = []
    if ag.get('epi_luvas'):      epi_sel.append('LUVAS')
    if ag.get('epi_oculos'):     epi_sel.append('ÓCULOS DE SEGURANÇA')
    if ag.get('epi_capacete'):   epi_sel.append('CAPACETE')
    if ag.get('epi_prot_auric'): epi_sel.append('PROTETOR AURICULAR')
    if ag.get('epi_resp'):       epi_sel.append('RESPIRADOR')
    if ag.get('epi_avental'):    epi_sel.append('AVENTAL')
    if ag.get('epi_macacao'):    epi_sel.append('MACACÃO IMPERMEÁVEL')
    out.append(Table([[Paragraph(_chk(epis, epi_sel), norm)]],
        colWidths=[W], style=TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('BOX', (0, 0), (-1, -1), 0.3, BORDA)])))
    out.append(_full('EQUIPAMENTO DE PROTEÇÃO COLETIVA:', ag.get('epc', '')))
    out.append(_full('OBSERVAÇÕES:', ag.get('obs', '')))
    out.append(Spacer(1, 10))
    return out


# ── Planilha de Campo Completa (multi-tipo: ruído + químico + vibração) ──
@controle_bp.route('/relatorio/campo-completo', methods=['POST'])
def gerar_relatorio_campo_completo():
    """Gera UM PDF com seções de todos os tipos medidos na visita."""
    try:
        buf, nome = _campo_completo_pdf(_sanitize_rl(request.json or {}))
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500
    return send_file(buf, as_attachment=True, download_name=nome,
                     mimetype='application/pdf')


def _campo_completo_pdf(d):
    """Monta o PDF da planilha de campo completa e devolve (buffer, nome_arquivo).

    Extraído da rota POST para ser reaproveitado pela aba 'Planilhas Feitas',
    que regenera a MESMA planilha a partir do que está no banco — em vez de uma
    ficha separada por agente. `d` já deve vir por _sanitize_rl.
    Levanta ImportError se o reportlab não estiver instalado."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable, KeepTogether)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    tipos   = [t for t in d.get('tipos', []) if t in ('ruido', 'quimico', 'vibracao', 'calor')]
    base    = d.get('base', d)   # compatibilidade: base pode vir no topo

    empresa_nome  = base.get('empresa_nome', '—')
    os_num        = base.get('os', '')
    data_coleta   = base.get('data_coleta', base.get('data', ''))
    tecnico       = base.get('tecnico', '')
    tecnico_mte   = base.get('tecnico_mte', '') or _mte_do_tecnico(tecnico)
    unidade       = base.get('unidade', '')
    cidade        = base.get('cidade', '')
    resp_empresa  = base.get('resp_empresa', '')
    sig_avaliado  = base.get('sig_avaliado') or d.get('sig_avaliado')
    sig_empresa   = base.get('sig_empresa')  or d.get('sig_empresa')

    dia_semana = ''
    if data_coleta and '-' in str(data_coleta):
        try:
            from datetime import datetime as _dt
            _dd = _dt.strptime(data_coleta, '%Y-%m-%d')
            data_fmt = _dd.strftime('%d/%m/%Y')
            dia_semana = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'][_dd.weekday()]
        except Exception:
            data_fmt = _iso_br(data_coleta)
    else:
        data_fmt = data_coleta or '___/___/______'

    AZUL     = colors.HexColor('#1E3A8A')
    AZUL_CLR = colors.HexColor('#DBEAFE')
    CINZA    = colors.HexColor('#F3F4F6')
    BORDA    = colors.HexColor('#CBD5E1')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f'Planilha de Campo — {empresa_nome}')
    styles = getSampleStyleSheet()

    def _sty(name, **kw):
        return ParagraphStyle(f'_cp_{name}_{id(kw)}', parent=styles.get(name, styles['Normal']), **kw)

    tit  = _sty('Normal', fontSize=11, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER)
    sub  = _sty('Normal', fontSize=8,  fontName='Helvetica',      textColor=colors.HexColor('#64748B'), alignment=TA_CENTER)
    norm = _sty('Normal', fontSize=8,  fontName='Helvetica',      textColor=colors.black)
    bold = _sty('Normal', fontSize=8,  fontName='Helvetica-Bold', textColor=colors.black)
    sec  = _sty('Normal', fontSize=9,  fontName='Helvetica-Bold', textColor=AZUL)
    W    = A4[0] - 3.6*cm

    def _hdr(txt):
        t = Table([[Paragraph(txt, _sty('Normal', fontSize=9, fontName='Helvetica-Bold',
                   textColor=colors.white))]], colWidths=[W])
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),AZUL),
            ('LEFTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),4),
            ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        return t

    def _row2(l1, v1, l2='', v2=''):
        row = [Paragraph(f'<b>{l1}</b> {v1 or "—"}', norm)]
        if l2:
            row.append(Paragraph(f'<b>{l2}</b> {v2 or "—"}', norm))
        cw = [W/2, W/2] if l2 else [W]
        t = Table([row], colWidths=cw)
        t.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),2),
            ('BOTTOMPADDING',(0,0),(-1,-1),2),('BOX',(0,0),(-1,-1),0.3,BORDA),
            ('INNERGRID',(0,0),(-1,-1),0.3,BORDA)]))
        return t

    def _tabela(cabecalho, linhas, col_widths):
        data = [[Paragraph(h, _sty('Normal', fontSize=7, fontName='Helvetica-Bold',
                            textColor=colors.white)) for h in cabecalho]]
        for row in linhas:
            data.append([Paragraph(str(c) if c is not None else '', norm) for c in row])
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),AZUL),
            ('GRID',(0,0),(-1,-1),0.3,BORDA),
            ('FONTSIZE',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),12),
            ('LEFTPADDING',(0,0),(-1,-1),3),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, CINZA])
        ]))
        return t

    def _sig_img(b64_str, w=4.5*cm, h=1.1*cm):
        if not b64_str:
            return None
        try:
            import base64 as _b64
            from io import BytesIO as _BIO
            from reportlab.platypus import Image as _RI
            raw = _b64.b64decode(b64_str.split(',')[-1])
            return _RI(_BIO(raw), width=w, height=h)
        except Exception:
            return None

    story = []

    # Nome do técnico exibido (com MTE quando houver) — usado no cabeçalho e na assinatura
    tecnico_disp = f'{tecnico} — MTE {tecnico_mte}' if tecnico_mte else tecnico

    # Cabeçalho — padronizado com as planilhas individuais
    story.append(_pdf_header(W, 'PLANILHA DE CAMPO',
                             'Multi-tipo: Ruído / Químicos / Vibração'))
    story.append(Spacer(1, 6))
    story.append(_row2('Empresa:', empresa_nome, 'OS Nº:', os_num))
    story.append(_row2('Data:', data_fmt, 'Técnico:', tecnico_disp))
    story.append(_row2('Unidade / Obra:', unidade, 'Cidade:', cidade))
    story.append(_row2('Responsável empresa:', resp_empresa, '', ''))
    story.append(Spacer(1, 8))

    # ── Seção Ruído ──────────────────────────────────────────────────────
    if 'ruido' in tipos:
        r = d.get('ruido', {})
        story.append(_hdr('🔊  RUÍDO'))
        story.append(Spacer(1, 4))
        story.append(_row2('Acompanhante:', r.get('acomp',''), 'Cargo:', r.get('cargo_acomp','')))
        story.append(_row2('Horário:', f'{r.get("hora_ini","___")} – {r.get("hora_fim","___")}',
                           'Calibrador:', r.get('calibrador','')))
        # Aferição (inicial/final/desvio/status) removida da planilha de campo —
        # fica só o nº do calibrador, útil p/ o laudo (pedido Helbert 17/06).
        story.append(Spacer(1, 4))

        trabs = r.get('trabalhadores', [])
        # SN do dosímetro vem do form como 'sn'; Início/Fim por trabalhador
        # nunca eram preenchidos (form só tem horário global) → colunas removidas.
        cab_r = ['#', 'Nome', 'Cargo / Função', 'Setor', 'Dosímetro',
                 'Almoço (início–fim)']
        cw_r  = [W*0.04, W*0.26, W*0.20, W*0.18, W*0.16, W*0.16]
        linhas_r = []
        for i, t in enumerate(trabs, 1):
            _almoco = ' – '.join([x for x in [t.get('pausa','') or t.get('almoco',''),
                                              t.get('almoco_fim','')] if x])
            linhas_r.append([i, t.get('nome',''), t.get('cargo','') or t.get('funcao',''),
                              t.get('setor',''),
                              t.get('sn','') or t.get('serie_dosimetro','') or
                              t.get('dosimetro','') or t.get('amostrador',''),
                              _almoco])
        # Só os trabalhadores preenchidos (sem linhas em branco de padding).
        story.append(_tabela(cab_r, linhas_r, cw_r))
        # Linha GHEs (agentes do planejamento) — FORA da tabela, igual à individual
        itens_ghe_r = r.get('itens', [])
        if itens_ghe_r:
            ghe_txt = ', '.join([it.get('ghe','') or it.get('agente','')
                                 for it in itens_ghe_r if it.get('ghe') or it.get('agente')])
            if ghe_txt:
                story.append(Spacer(1, 3))
                story.append(Paragraph(f'<b>GHEs:</b> {ghe_txt[:200]}', norm))
        story.append(Spacer(1, 10))

    # ── Seção Químico ─────────────────────────────────────────────────────
    if 'quimico' in tipos:
        agentes = d.get('quimico', {}).get('agentes', [])
        story.append(_hdr('⚗️  AGENTES QUÍMICOS'))
        story.append(Spacer(1, 4))
        if not agentes:
            story.append(Paragraph('(sem agentes informados)', _sty('Normal', fontSize=8,
                         textColor=colors.grey, leftIndent=6)))
        # Blocos completos por agente — mesmos da planilha individual
        # (helper compartilhado _quimico_agente_flowables p/ não divergirem)
        for idx, ag in enumerate(agentes):
            story.extend(_quimico_agente_flowables(ag, idx, W, data_fmt, dia_semana))
        story.append(Spacer(1, 4))

    # ── Seção Vibração ────────────────────────────────────────────────────
    if 'vibracao' in tipos:
        v = d.get('vibracao', {})
        sub_lbl = {'vci':'Corpo Inteiro (VCI)','vbma':'Mãos e Braços (VBMA)','ambos':'VCI + VBMA'}
        apa_lbl = {'chrompack_1':'SmartVib — S/N 0779','chrompack_2':'SmartVib — S/N 1241'}
        story.append(_hdr('〰️  VIBRAÇÃO'))
        story.append(Spacer(1, 4))
        story.append(_row2('Subtipo:', sub_lbl.get((v.get('subtipo','') or '').lower(), v.get('subtipo','')),
                           'Aparelho:', apa_lbl.get((v.get('aparelho','') or '').lower(), v.get('aparelho',''))))
        story.append(_row2('Acompanhante:', v.get('acomp',''),
                           'Horário:', f'{v.get("hora_ini","___")} – {v.get("hora_fim","___")}'))
        story.append(Spacer(1, 4))

        pontos = v.get('pontos', [])
        _glob_vmb = (v.get('subtipo','') or '').lower() in ('vbma','ambos','vmb')
        def _pt_vmb(p):
            t = (p.get('tipo') or '').lower()
            if t in ('vmb','vbma'): return True
            if t == 'vci': return False
            return _glob_vmb
        vci_p = [p for p in pontos if not _pt_vmb(p)]
        vmb_p = [p for p in pontos if _pt_vmb(p)]
        def _equip_full(p):
            # Equipamento/marca/ano por trabalhador (Helbert 11/06); legado: obs
            full = ' / '.join(x for x in (
                str(p.get('equip') or '').strip(),
                str(p.get('marca') or '').strip(),
                str(p.get('ano') or '').strip()) if x)
            return full or p.get('obs', '')
        def _tab_vib(is_vmb_t, pts_t, titulo=None):
            if is_vmb_t:
                cab_v = ['#','Trabalhador','Função','Setor','T. exp. (h)','T. não exp. (h)','Equipamento / Marca / Ano']
                cw_v  = [W*0.04,W*0.20,W*0.15,W*0.12,W*0.11,W*0.11,W*0.27]
            else:
                cab_v = ['#','Trabalhador','Função','Setor','T. exp. (h)','T. não exp. (h)','Trajeto','Tipo de terreno','Veículo / Modelo / Ano']
                cw_v  = [W*0.04,W*0.16,W*0.12,W*0.10,W*0.08,W*0.08,W*0.11,W*0.11,W*0.20]
            linhas_v = []
            for i, p in enumerate(pts_t, 1):
                row = [i, p.get('nome',''), p.get('funcao','') or p.get('cargo',''),
                       p.get('setor',''), p.get('tempo',''), p.get('tempo_nexp','')]
                if not is_vmb_t:
                    row.append(p.get('trajeto',''))
                    row.append(p.get('terreno',''))
                row.append(_equip_full(p))
                linhas_v.append(row)
            # Só os trabalhadores preenchidos (sem linhas em branco).
            if titulo:
                story.append(Paragraph(f'<b>{titulo}</b>', bold)); story.append(Spacer(1, 2))
            story.append(_tabela(cab_v, linhas_v, cw_v)); story.append(Spacer(1, 6))
        _both = bool(vci_p) and bool(vmb_p)
        if vci_p or (not pontos and not _glob_vmb):
            _tab_vib(False, vci_p, 'VCI — Corpo Inteiro' if _both else None)
        if vmb_p or (not pontos and _glob_vmb):
            _tab_vib(True, vmb_p, 'VMB — Mãos e Braços' if _both else None)

        # Registro fotográfico — marcar em campo (igual à planilha individual)
        _has_vci_cc = bool(vci_p) or (not pontos and not _glob_vmb)
        _has_vmb_cc = bool(vmb_p) or (not pontos and _glob_vmb)
        _fotos_cc = []
        if _has_vci_cc:
            _fotos_cc.append('<b>VCI:</b> Foto do veículo ( )   Foto do equipamento posicionado ( )   Foto do documento do veículo ( )')
        if _has_vmb_cc:
            _fotos_cc.append('<b>VMB:</b> Foto do equipamento ( )   Foto do equipamento + acelerômetro ( )   Foto do funcionário executando a atividade ( )')
        if _fotos_cc:
            story.append(Paragraph(
                '<font size="7" color="#64748B"><b>REGISTRO FOTOGRÁFICO (marcar)</b></font><br/>'
                + '<br/>'.join(_fotos_cc), norm))
        story.append(Spacer(1, 4))

    # ── Seção Calor (IBUTG) ───────────────────────────────────────────────
    if 'calor' in tipos and d.get('calor', {}).get('setores'):
        c = d.get('calor', {})
        story.append(_hdr('🌡️  CALOR (IBUTG)'))
        story.append(Spacer(1, 4))
        story.append(_row2('Acompanhante:', c.get('acomp',''),
                           'Horário:', f'{c.get("hora_ini","___")} – {c.get("hora_fim","___")}'))
        story.append(Spacer(1, 4))
        # Tabela de setores + legenda (helper compartilhado c/ a planilha individual)
        story.extend(_calor_setores_flowables(c.get('setores', []), W))
        story.append(Spacer(1, 4))
        _obs_calor = c.get('obs') or c.get('observacoes') or ''
        if _obs_calor:
            story.append(Paragraph(f'<font size="7" color="#64748B">OBSERVAÇÕES (CALOR)</font><br/>{_obs_calor}', norm))
            story.append(Spacer(1, 4))

    # ── Observações de campo ──────────────────────────────────────────────
    obs_campo = (base.get('obs') or base.get('observacoes') or
                 d.get('obs') or d.get('observacoes') or '')
    story.append(Paragraph('OBSERVAÇÕES DE CAMPO', sec))
    story.append(Spacer(1, 2))
    if obs_campo:
        story.append(Paragraph(str(obs_campo), norm))
    else:
        story.append(Paragraph('_' * 110, norm))
        story.append(Spacer(1, 4))
        story.append(Paragraph('_' * 110, norm))
    story.append(Spacer(1, 8))

    # ── Assinaturas ───────────────────────────────────────────────────────
    story.append(HRFlowable(width=W, thickness=0.5, color=BORDA))
    story.append(Spacer(1, 6))
    img_tec  = _sig_img(sig_empresa)   # Técnico Ocupacional
    img_resp = _sig_img(sig_avaliado)  # Responsável empresa
    col_tec  = img_tec  if img_tec  else Paragraph('_________________________________', norm)
    col_resp = img_resp if img_resp else Paragraph('_________________________________', norm)
    _resp_nome = (resp_empresa or base.get('acomp') or
                  (d.get('ruido') or {}).get('acomp') or
                  (d.get('vibracao') or {}).get('acomp') or '')
    assin = Table([
        [col_tec, col_resp, Paragraph('_________________________________', norm)],
        [Paragraph(f'<font size="7">Técnico da Ocupacional<br/>{tecnico_disp}</font>', norm),
         Paragraph('<font size="7">Responsável da Empresa / Acompanhante</font>'
                   + (f'<br/><font size="7">{_resp_nome}</font>' if _resp_nome else ''), norm),
         Paragraph(f'<font size="7">Data: {data_fmt}</font>', norm)],
    ], colWidths=[W/3, W/3, W/3])
    assin.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4)]))
    story.append(assin)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        'Declaro estar ciente das atividades realizadas, atividades não realizadas, '
        'observações registradas e eventuais impedimentos descritos neste relatório.',
        _sty('Normal', fontSize=6.5, fontName='Helvetica-Oblique',
             textColor=colors.HexColor('#475569'), alignment=TA_CENTER)))
    story.append(Spacer(1, 4))

    # Rodapé — normas conforme as seções presentes
    _normas_cc = []
    if 'ruido' in tipos:    _normas_cc.append('Ruído: NR-15 Anexo 1 · NHO-01')
    if 'quimico' in tipos:  _normas_cc.append('Químicos: NR-15 Anexo 13 · NHO-03')
    if 'vibracao' in tipos: _normas_cc.append('Vibração: NR-15 Anexo 8 · NHO-09/NHO-10')
    if 'calor' in tipos and d.get('calor', {}).get('setores'):
        _normas_cc.append('Calor: NR-15 Anexo 3 · NHO-06')
    _normas_txt = ('Normas: ' + ' | '.join(_normas_cc) + ' — FUNDACENTRO | ') if _normas_cc else ''
    story.append(Paragraph(
        f'{_normas_txt}Gerado em: {agora_brt().strftime("%d/%m/%Y %H:%M")} | Ocupacional — Medicina e Segurança do Trabalho',
        _sty('Normal', fontSize=6.5, textColor=colors.HexColor('#94A3B8'), alignment=TA_CENTER)))

    story.extend(_fotos_pdf_flowables(base.get('fotos') or [], W))

    doc.build(story)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa_nome)[:35]
    data_safe = data_fmt.replace('/', '-')
    return buf, f'planilha_campo_{nome_safe}_{data_safe}.pdf'


# ── Relatório PDF de Ruído ────────────────────────────────────────────
@controle_bp.route('/relatorio/ruido', methods=['POST'])
def gerar_relatorio_ruido():
    """Gera PDF do relatório/planilha de campo de ruído.
    Aceita payload direto (do wizard) ou coleta_id para buscar do banco.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable, KeepTogether)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = request.json or {}
    cid = d.get('coleta_id')

    # Buscar do banco se fornecido ID
    if cid:
        coleta = get_coleta_ruido(int(cid))
        if coleta:
            d = coleta
            d['trabalhadores'] = coleta.get('trabalhadores', [])

    d = _sanitize_rl(d)   # escapa &<> em todo texto (anti-quebra/injeção reportlab)

    # Extrair campos
    empresa_nome   = d.get('empresa_nome', d.get('empresa', {}).get('nome', '—'))
    cnpj           = d.get('cnpj', d.get('empresa', {}).get('cnpj', ''))
    unidade        = d.get('unidade', '')
    cidade         = d.get('cidade', '')
    resp_empresa   = d.get('resp_empresa', '')
    os_num         = d.get('os', '')
    data_coleta    = d.get('data_coleta', d.get('data', ''))
    hora_ini       = d.get('hora_inicio', d.get('hora_ini', ''))
    hora_fim       = d.get('hora_termino', d.get('hora_fim', ''))
    tecnico        = d.get('tecnico', '')
    tecnico_mte    = d.get('tecnico_mte', '') or _mte_do_tecnico(tecnico)
    if tecnico_mte:
        tecnico = f'{tecnico} — MTE {tecnico_mte}'
    acomp          = d.get('acompanhante', d.get('acomp', ''))
    cargo_acomp    = d.get('cargo_acompanhante', d.get('cargo_acomp', ''))
    calibrador     = d.get('calibrador', '')
    cal_ini        = d.get('calibracao_inicial', d.get('cal_ini', ''))
    cal_fim        = d.get('calibracao_final', d.get('cal_fim', ''))
    desvio         = d.get('desvio_calibracao', '')
    status_cal     = d.get('status_calibracao', '')
    trabalhadores  = d.get('trabalhadores', [])
    termos         = d.get('termos', [])
    itens_ghe      = d.get('itens', [])

    # Calcular desvio se não veio calculado
    if not desvio and cal_ini and cal_fim:
        try:
            desvio = round(float(str(cal_fim).replace(',','.')) - float(str(cal_ini).replace(',','.')), 2)
        except: desvio = ''

    # Formatar data
    if data_coleta and '-' in str(data_coleta):
        try:
            from datetime import datetime as _dt
            data_fmt = _dt.strptime(data_coleta, '%Y-%m-%d').strftime('%d/%m/%Y')
        except: data_fmt = _iso_br(data_coleta)
    else:
        data_fmt = data_coleta or '___/___/______'

    # ─── Estilos ───────────────────────────────────────────────────
    AZUL      = colors.HexColor('#1E3A8A')
    AZUL_CLR  = colors.HexColor('#DBEAFE')
    CINZA     = colors.HexColor('#F3F4F6')
    BORDA     = colors.HexColor('#CBD5E1')
    VERDE     = colors.HexColor('#16A34A')
    LARANJA   = colors.HexColor('#D97706')
    PRETO     = colors.black
    BRANCO    = colors.white

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f'Relatório de Ruído — {empresa_nome}')

    styles = getSampleStyleSheet()

    def sty(name, **kw):
        base = styles.get(name, styles['Normal'])
        return ParagraphStyle(f'_sty_{name}_{id(kw)}', parent=base, **kw)

    titulo_pg = sty('Title', fontSize=13, textColor=AZUL, alignment=TA_CENTER,
                    fontName='Helvetica-Bold', spaceAfter=2)
    subtit_pg = sty('Normal', fontSize=8.5, textColor=colors.HexColor('#475569'),
                    alignment=TA_CENTER, spaceAfter=10)
    sec_hdr   = sty('Normal', fontSize=8, fontName='Helvetica-Bold', textColor=BRANCO)
    cell_bold = sty('Normal', fontSize=8, fontName='Helvetica-Bold', textColor=PRETO)
    cell_reg  = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    cell_sml  = sty('Normal', fontSize=7.5, fontName='Helvetica', textColor=PRETO)
    assin_sty = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    footer_sty= sty('Normal', fontSize=6.5, textColor=colors.HexColor('#64748B'),
                    alignment=TA_CENTER)

    W = A4[0] - 3.6*cm   # largura útil

    elements = []

    # ─── Cabeçalho ─────────────────────────────────────────────────
    elements.append(_pdf_header(W, 'PLANILHA DE CAMPO — RUÍDO',
        'Dosimetria de Ruído | NR-15 Anexo 1 | NHO-01 FUNDACENTRO'))
    elements.append(Spacer(1, 8))

    # ─── Identificação da Empresa ───────────────────────────────────
    def sec_label(txt):
        t = Table([[Paragraph(txt, sec_hdr)]], colWidths=[W])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),AZUL),
            ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3),
            ('LEFTPADDING',(0,0),(-1,-1),6),
        ]))
        return t

    def info_row(pairs, widths=None):
        """pairs = [(label, value), ...] numa linha"""
        n = len(pairs)
        if not widths:
            widths = [W/n]*n
        cells = []
        for lbl, val in pairs:
            cells.append(Paragraph(f'<b>{lbl}:</b> {val or "—"}', cell_reg))
        t = Table([cells], colWidths=widths)
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),CINZA),
            ('GRID',(0,0),(-1,-1),0.4,BORDA),
            ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),
            ('LEFTPADDING',(0,0),(-1,-1),5),
        ]))
        return t

    elements.append(sec_label('1. IDENTIFICAÇÃO DA EMPRESA'))
    elements.append(Spacer(1, 2))
    elements.append(info_row([('Empresa', empresa_nome), ('CNPJ', cnpj or ''), ('OS', os_num)],
                              [W*0.50, W*0.30, W*0.20]))
    elements.append(Spacer(1, 1))
    elements.append(info_row([('Unidade/Obra', unidade), ('Cidade', cidade), ('Responsável', resp_empresa)],
                              [W*0.40, W*0.25, W*0.35]))
    elements.append(Spacer(1, 8))

    # ─── Dados da Medição ───────────────────────────────────────────
    elements.append(sec_label('2. DADOS DA MEDIÇÃO'))
    elements.append(Spacer(1, 2))
    elements.append(info_row([('Data', data_fmt), ('Hora Início', hora_ini), ('Hora Término', hora_fim)],
                              [W*0.35, W*0.25, W*0.40]))
    elements.append(Spacer(1, 1))
    elements.append(info_row([('Profissional Técnico', tecnico), ('Acompanhante', acomp), ('Cargo Acompanhante', cargo_acomp)],
                              [W*0.35, W*0.35, W*0.30]))
    elements.append(Spacer(1, 8))

    # ─── Equipamento / Calibrador ───────────────────────────────────
    # Aferição inicial/final + desvio removidos da planilha de campo;
    # fica só a identificação do calibrador, útil p/ o laudo (Helbert 17/06).
    elements.append(sec_label('3. EQUIPAMENTO / CALIBRADOR'))
    elements.append(Spacer(1, 2))
    elements.append(info_row([('Calibrador / N° Série', calibrador)], [W]))
    elements.append(Spacer(1, 8))

    # ─── Trabalhadores ───────────────────────────────────────────────
    elements.append(sec_label('4. IDENTIFICAÇÃO DOS TRABALHADORES AVALIADOS'))
    elements.append(Spacer(1, 2))

    trab_head = [
        Paragraph('<b>N°</b>', cell_bold),
        Paragraph('<b>N° Série Dosímetro</b>', cell_bold),
        Paragraph('<b>Nome Completo</b>', cell_bold),
        Paragraph('<b>Cargo/Função</b>', cell_bold),
        Paragraph('<b>Setor/GHE</b>', cell_bold),
        Paragraph('<b>Almoço (início–fim)</b>', cell_bold),
    ]
    trab_rows = [trab_head]

    # Só os trabalhadores preenchidos (sem linhas em branco de padding).
    trabs_fill = list(trabalhadores) if trabalhadores else []

    for i, tr in enumerate(trabs_fill, 1):
        trab_rows.append([
            Paragraph(str(i), cell_reg),
            Paragraph(tr.get('sn','') or tr.get('serie_dosimetro','') or '', cell_reg),
            Paragraph(tr.get('nome','') or '', cell_reg),
            Paragraph(tr.get('cargo','') or '', cell_sml),
            Paragraph(tr.get('setor','') or '', cell_sml),
            Paragraph(' – '.join([x for x in [tr.get('pausa','') or tr.get('almoco',''), tr.get('almoco_fim','')] if x]) or '', cell_reg),
        ])

    # Linha GHE (agentes do planejamento)
    if itens_ghe:
        ghe_txt = '; '.join([it.get('ghe','') or it.get('agente','') for it in itens_ghe if it.get('ghe') or it.get('agente')])
        trab_rows.append([
            Paragraph('GHEs:', cell_bold),
            Paragraph(ghe_txt[:120], cell_sml),
            '', '', '', '',
        ])

    trab_tbl = Table(trab_rows, colWidths=[W*0.05, W*0.17, W*0.26, W*0.20, W*0.18, W*0.14],
                     repeatRows=1)
    trab_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL_CLR),
        ('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0),(-1,-1),4),
        ('ALIGN',(0,0),(0,-1),'CENTER'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[BRANCO, CINZA]),
        ('SPAN',(1,-1),(5,-1)) if itens_ghe else ('NOP',(0,0),(0,0)),
    ]))
    elements.append(trab_tbl)
    elements.append(Spacer(1, 8))

    # (Seção "5. Resultados das medições" removida da planilha de campo —
    #  o resultado só existe depois, no escritório, via histograma.)

    # (Seção "Termo de Responsabilidade — Dosímetros" removida da planilha
    #  de campo a pedido. O input de entrega/devolução no wizard segue existindo.)

    # ─── Assinaturas finais ──────────────────────────────────────────
    # Assinatura digital do responsável técnico (obrigatória no app) → coluna 1
    sig_avaliado = d.get('sig_avaliado')
    sig_empresa  = d.get('sig_empresa')

    def _sig_img_ruido(b64_str, w=4.6*cm, h=1.2*cm):
        if not b64_str:
            return None
        try:
            import base64 as _b64
            from io import BytesIO as _BIO
            from reportlab.platypus import Image as _RLImg
            raw = _b64.b64decode(b64_str.split(',')[-1])
            return _RLImg(_BIO(raw), width=w, height=h)
        except Exception:
            return None

    img_tec  = _sig_img_ruido(sig_empresa)    # Técnico da Ocupacional (obrigatória no app)
    img_resp = _sig_img_ruido(sig_avaliado)   # Responsável / acompanhante da empresa cliente
    col_tec  = (img_tec  if img_tec  else
                Paragraph('_________________________________', assin_sty))
    col_resp = (img_resp if img_resp else
                Paragraph('_________________________________', assin_sty))
    assin_rows = [
        [col_tec, col_resp, ''],
        [Paragraph('<font size="7">Técnico da Ocupacional (Responsável pela medição)</font><br/>'
                   f'<font size="7">{tecnico}</font>', assin_sty),
         Paragraph('<font size="7">Responsável da Empresa / Acompanhante</font><br/>'
                   f'<font size="7">{acomp}</font>', assin_sty),
         Paragraph(f'_________________________________<br/><font size="7">Data: {data_fmt}</font>',
                   assin_sty)],
    ]
    assin_tbl = Table(assin_rows, colWidths=[W/3, W/3, W/3])
    assin_tbl.setStyle(TableStyle([
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('TOPPADDING',(0,0),(-1,-1),4),
    ]))
    elements.append(assin_tbl)
    elements.append(Spacer(1, 10))

    # ─── Registro fotográfico ────────────────────────────────────────
    elements.extend(_fotos_pdf_flowables(d.get('fotos') or [], W))

    # ─── Rodapé ──────────────────────────────────────────────────────
    elements.append(HRFlowable(width=W, thickness=0.5, color=BORDA))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        'Normas: NR-15 Anexo 1 (Portaria 3214/78) — NHO-01 FUNDACENTRO '
        '— ABNT NBR 10151 — Portaria MTb 1.297/2017 | '
        f'Gerado em: {agora_brt().strftime("%d/%m/%Y %H:%M")} | Ocupacional — Medicina e Segurança do Trabalho',
        footer_sty))

    doc.build(elements)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa_nome)[:40]
    data_safe = data_fmt.replace('/', '-')
    return send_file(buf, as_attachment=True,
        download_name=f'planilha_ruido_{nome_safe}_{data_safe}.pdf',
        mimetype='application/pdf')


# ── Relatório PDF de Campo — Vibração ─────────────────────────────────
@controle_bp.route('/relatorio/vibracao', methods=['POST'])
def gerar_relatorio_vibracao():
    """Gera PDF da planilha de campo de Vibração (VCI / VBMA).
    Mesma estrutura da planilha de ruído: cabeçalho, dados, tabela de
    medições (linhas em branco p/ campo) e assinatura do responsável técnico.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = _sanitize_rl(request.json or {})
    empresa_nome = d.get('empresa_nome', d.get('empresa', {}).get('nome', '—') if isinstance(d.get('empresa'), dict) else '—')
    cnpj         = d.get('cnpj', '')
    unidade      = d.get('unidade', '')
    cidade       = d.get('cidade', '')
    resp_empresa = d.get('resp_empresa', '')
    os_num       = d.get('os', '')
    data_coleta  = d.get('data_coleta', d.get('data', ''))
    hora_ini     = d.get('hora_ini', d.get('hora_inicio', ''))
    hora_fim     = d.get('hora_fim', d.get('hora_termino', ''))
    tecnico      = d.get('tecnico', '')
    tecnico_mte  = d.get('tecnico_mte', '') or _mte_do_tecnico(tecnico)
    if tecnico_mte:
        tecnico = f'{tecnico} — MTE {tecnico_mte}'
    acomp        = d.get('acomp', d.get('acompanhante', ''))
    obs          = d.get('obs', d.get('observacoes', ''))
    sig_empresa  = d.get('sig_empresa')
    sig_avaliado = d.get('sig_avaliado')
    pontos       = d.get('pontos', d.get('trabalhadores', []))

    # subtipo/aparelho podem vir como lista de itens (do planejamento) ou direto
    itens = d.get('itens', [])
    def _label_subtipo(s):
        return {'vci':'Corpo Inteiro (VCI)','vbma':'Mãos e Braços (VBMA)',
                'ambos':'VCI + VBMA'}.get((s or '').lower(), s or '—')
    def _label_aparelho(a):
        return {'chrompack_1':'SmartVib — S/N 0779','chrompack_2':'SmartVib — S/N 1241'}.get((a or '').lower(), a or '—')
    subtipo  = d.get('subtipo')  or (itens[0].get('subtipo')  if itens else '') or ''
    aparelho = d.get('aparelho') or (itens[0].get('aparelho') if itens else '') or ''

    if data_coleta and '-' in str(data_coleta):
        try:
            from datetime import datetime as _dt
            data_fmt = _dt.strptime(data_coleta, '%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception:
            data_fmt = _iso_br(data_coleta)
    else:
        data_fmt = data_coleta or '___/___/______'

    AZUL     = colors.HexColor('#1E3A8A')
    AZUL_CLR = colors.HexColor('#DBEAFE')
    CINZA    = colors.HexColor('#F3F4F6')
    BORDA    = colors.HexColor('#CBD5E1')
    PRETO    = colors.black
    BRANCO   = colors.white

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f'Planilha de Campo Vibração — {empresa_nome}')

    styles = getSampleStyleSheet()
    def sty(name, **kw):
        base = styles.get(name, styles['Normal'])
        return ParagraphStyle(f'_vib_{name}_{id(kw)}', parent=base, **kw)

    cell_bold = sty('Normal', fontSize=8, fontName='Helvetica-Bold', textColor=PRETO)
    cell_reg  = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    assin_sty = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    footer_sty= sty('Normal', fontSize=6.5, textColor=colors.HexColor('#64748B'), alignment=TA_CENTER)

    W = A4[0] - 3.6*cm
    elements = []

    # Cabeçalho
    elements.append(_pdf_header(W, 'PLANILHA DE CAMPO — VIBRAÇÃO',
        'NR-15 Anexo 8 | NHO-09 (VCI) / NHO-10 (VBMA) FUNDACENTRO'))
    elements.append(Spacer(1, 8))

    # Bloco de dados
    def _info(lbl, val):
        return Paragraph(f'<font size="7" color="#64748B">{lbl}</font><br/><b>{val or "—"}</b>', cell_reg)
    info_rows = [
        [_info('EMPRESA', empresa_nome), _info('CNPJ', cnpj), _info('OS', os_num)],
        [_info('UNIDADE', unidade), _info('CIDADE', cidade), _info('DATA', data_fmt)],
        [_info('TÉCNICO', tecnico), _info('ACOMPANHANTE', acomp), _info('HORÁRIO', f'{hora_ini}–{hora_fim}' if (hora_ini or hora_fim) else '—')],
        [_info('TIPO DE VIBRAÇÃO', _label_subtipo(subtipo)), _info('APARELHO', _label_aparelho(aparelho)), _info('RESP. EMPRESA', resp_empresa)],
    ]
    info_tbl = Table(info_rows, colWidths=[W/3, W/3, W/3])
    info_tbl.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4),
        ('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),5)]))
    elements.append(info_tbl); elements.append(Spacer(1, 10))

    # ── Tipo por funcionário (VCI/VMB); fallback no subtipo global ──
    _sub = (subtipo or '').lower()
    _glob_vmb = _sub in ('vbma', 'vmb')
    def _pt_is_vmb(p):
        t = (p.get('tipo') or '').lower()
        if t in ('vmb', 'vbma'): return True
        if t == 'vci': return False
        return _glob_vmb
    pts_all = list(pontos) if pontos else []
    # Veículo/equipamento por trabalhador (equip/marca/ano) — coluna combinada
    for _p in pts_all:
        if isinstance(_p, dict):
            _p['equip_full'] = ' / '.join(x for x in (
                str(_p.get('equip') or '').strip(),
                str(_p.get('marca') or '').strip(),
                str(_p.get('ano') or '').strip()) if x)
            # Horário de medição por trabalhador (início–fim)
            _hi, _hf = str(_p.get('hora_inicio') or '').strip(), str(_p.get('hora_final') or '').strip()
            _p['horario'] = (f'{_hi}–{_hf}' if (_hi and _hf) else (_hi or _hf or ''))
    vci_pts = [p for p in pts_all if not _pt_is_vmb(p)]
    vmb_pts = [p for p in pts_all if _pt_is_vmb(p)]
    has_vci = bool(vci_pts) or (not pts_all and not _glob_vmb)
    has_vmb = bool(vmb_pts) or (not pts_all and _glob_vmb)

    # ── Veículo/Ferramenta global — só legado (planilhas novas trazem
    #    equipamento POR TRABALHADOR; bloco não aparece se vazio) ──
    def _bloco_equip(lbl, campos):
        elements.append(Paragraph(f'<font size="7" color="#64748B"><b>{lbl}</b></font>', cell_reg))
        t = Table([[_info(l, v) for l, v in campos]], colWidths=[W/3, W/3, W/3])
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,BORDA),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),8),('LEFTPADDING',(0,0),(-1,-1),5)]))
        elements.append(t); elements.append(Spacer(1, 8))
    _tem_glob = any(d.get(k) for k in ('placa', 'equipamento', 'modelo', 'ano'))
    if _tem_glob and has_vci:
        _bloco_equip('VEÍCULO AVALIADO (VCI)', [('Placa', d.get('placa','')), ('Modelo', d.get('modelo','')), ('Ano', d.get('ano',''))])
    if _tem_glob and has_vmb:
        _bloco_equip('FERRAMENTA / EQUIPAMENTO AVALIADO (VMB)', [('Equipamento', d.get('equipamento','')), ('Modelo', d.get('modelo','')), ('Ano', d.get('ano',''))])
    elements.append(Spacer(1, 4))

    # Tabela de medições — uma por tipo presente (VCI tem trajeto/terreno; VMB não).
    # Equipamento/marca/ano por trabalhador (pedido Helbert 11/06).
    cell_bold7 = sty('Normal', fontSize=7, fontName='Helvetica-Bold', textColor=PRETO)
    cell_reg7  = sty('Normal', fontSize=7, fontName='Helvetica', textColor=PRETO)
    def _vibr_med_tbl(is_vmb_t, pts_t):
        if is_vmb_t:
            _heads = ['#', 'Trabalhador', 'Função', 'Setor', 'Horário', 'T. exp. (h)', 'T. não exp. (h)', 'Equipamento / Marca / Ano']
            _keys  = [None, 'nome', ('funcao','cargo'), 'setor', 'horario', 'tempo', 'tempo_nexp', ('equip_full','obs')]
            _colw  = [W*0.04, W*0.19, W*0.14, W*0.11, W*0.10, W*0.08, W*0.08, W*0.26]
        else:
            _heads = ['#', 'Trabalhador', 'Função', 'Setor', 'Horário', 'T. exp. (h)', 'T. não exp. (h)', 'Trajeto', 'Tipo de terreno', 'Veículo / Modelo / Ano']
            _keys  = [None, 'nome', ('funcao','cargo'), 'setor', 'horario', 'tempo', 'tempo_nexp', 'trajeto', 'terreno', ('equip_full','obs')]
            _colw  = [W*0.04, W*0.14, W*0.11, W*0.09, W*0.10, W*0.07, W*0.07, W*0.10, W*0.10, W*0.18]
        rows = [[Paragraph(h, cell_bold7) for h in _heads]]
        pl = list(pts_t)   # só os trabalhadores preenchidos
        for i, p in enumerate(pl, 1):
            row = []
            for k in _keys:
                if k is None:
                    row.append(Paragraph(str(i), cell_reg7))
                elif isinstance(k, tuple):
                    row.append(Paragraph(p.get(k[0],'') or p.get(k[1],'') or '', cell_reg7))
                else:
                    row.append(Paragraph(p.get(k,'') or '', cell_reg7))
            rows.append(row)
        t = Table(rows, colWidths=_colw, repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),AZUL_CLR),
            ('GRID',(0,0),(-1,-1),0.4,BORDA),('FONTSIZE',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),14),
            ('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[BRANCO, CINZA])]))
        return t
    _both = has_vci and has_vmb
    if has_vci:
        if _both:
            elements.append(Paragraph('<font size="8" color="#1E3A8A"><b>VCI — Vibração de Corpo Inteiro</b></font>', cell_bold)); elements.append(Spacer(1, 3))
        elements.append(_vibr_med_tbl(False, vci_pts)); elements.append(Spacer(1, 8))
    if has_vmb:
        if _both:
            elements.append(Paragraph('<font size="8" color="#1E3A8A"><b>VMB — Vibração Mãos e Braços</b></font>', cell_bold)); elements.append(Spacer(1, 3))
        elements.append(_vibr_med_tbl(True, vmb_pts)); elements.append(Spacer(1, 8))

    # Registro fotográfico — marcar em campo (igual aos formulários oficiais VCI/VMB)
    _fotos_parts = []
    if has_vci:
        _fotos_parts.append('<b>VCI:</b> Foto do veículo ( )   Foto do equipamento posicionado ( )   Foto do documento do veículo ( )')
    if has_vmb:
        _fotos_parts.append('<b>VMB:</b> Foto do equipamento ( )   Foto do equipamento + acelerômetro ( )   Foto do funcionário executando a atividade ( )')
    _fotos = '<br/>'.join(_fotos_parts) if _fotos_parts else ''
    elements.append(Paragraph(f'<font size="7" color="#64748B"><b>REGISTRO FOTOGRÁFICO (marcar)</b></font><br/>{_fotos}', cell_reg))
    elements.append(Spacer(1, 10))

    if obs:
        elements.append(Paragraph(f'<font size="7" color="#64748B">OBSERVAÇÕES DE CAMPO</font><br/>{obs}', cell_reg))
        elements.append(Spacer(1, 10))

    # Assinaturas — responsável técnico (imagem) na 1ª coluna
    def _sig_img_vib(b64_str, w=4.6*cm, h=1.2*cm):
        if not b64_str:
            return None
        try:
            import base64 as _b64
            from io import BytesIO as _BIO
            from reportlab.platypus import Image as _RLImg
            raw = _b64.b64decode(b64_str.split(',')[-1])
            return _RLImg(_BIO(raw), width=w, height=h)
        except Exception:
            return None
    img_tec  = _sig_img_vib(sig_empresa)    # Técnico da Ocupacional (obrigatória no app)
    img_resp = _sig_img_vib(sig_avaliado)   # Responsável / acompanhante da empresa cliente
    col_tec  = (img_tec  if img_tec  else Paragraph('_________________________________', assin_sty))
    col_resp = (img_resp if img_resp else Paragraph('_________________________________', assin_sty))
    assin_rows = [
        [col_tec, col_resp, ''],
        [Paragraph('<font size="7">Técnico da Ocupacional (Responsável pela medição)</font><br/>'
                   f'<font size="7">{tecnico}</font>', assin_sty),
         Paragraph('<font size="7">Responsável da Empresa / Acompanhante</font><br/>'
                   f'<font size="7">{acomp}</font>', assin_sty),
         Paragraph(f'_________________________________<br/><font size="7">Data: {data_fmt}</font>', assin_sty)],
    ]
    assin_tbl = Table(assin_rows, colWidths=[W/3, W/3, W/3])
    assin_tbl.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4)]))
    elements.append(assin_tbl); elements.append(Spacer(1, 10))

    # ─── Registro fotográfico ────────────────────────────────────────
    elements.extend(_fotos_pdf_flowables(d.get('fotos') or [], W))

    elements.append(HRFlowable(width=W, thickness=0.5, color=BORDA))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        'Normas: NR-15 Anexo 8 — NHO-09 (Corpo Inteiro) / NHO-10 (Mãos e Braços) FUNDACENTRO '
        '— ISO 2631 / ISO 5349 | '
        f'Gerado em: {agora_brt().strftime("%d/%m/%Y %H:%M")} | Ocupacional — Medicina e Segurança do Trabalho',
        footer_sty))

    doc.build(elements)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa_nome)[:40]
    data_safe = data_fmt.replace('/', '-')
    return send_file(buf, as_attachment=True,
        download_name=f'planilha_vibracao_{nome_safe}_{data_safe}.pdf',
        mimetype='application/pdf')


# ── Relatório PDF de Campo — Calor (IBUTG) ────────────────────────────
@controle_bp.route('/relatorio/calor', methods=['POST'])
def gerar_relatorio_calor():
    """Gera PDF da planilha de campo de CALOR (IBUTG).
    Mesma estrutura das planilhas de ruído/vibração: cabeçalho, bloco de
    dados, tabela de setores (linhas em branco p/ campo), registro
    fotográfico, observações e assinaturas.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = request.json or {}
    return gerar_relatorio_calor_pdf(d)


def gerar_relatorio_calor_pdf(d):
    """Implementação do PDF de calor (separada da rota p/ testes/reuso)."""
    d = _sanitize_rl(d or {})
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    empresa_nome = d.get('empresa_nome', d.get('empresa', {}).get('nome', '—') if isinstance(d.get('empresa'), dict) else '—')
    cnpj         = d.get('cnpj', '')
    unidade      = d.get('unidade', '')
    cidade       = d.get('cidade', '')
    resp_empresa = d.get('resp_empresa', '')
    os_num       = d.get('os', '')
    data_coleta  = d.get('data_coleta', d.get('data', ''))
    hora_ini     = d.get('hora_ini', d.get('hora_inicio', ''))
    hora_fim     = d.get('hora_fim', d.get('hora_termino', ''))
    tecnico      = d.get('tecnico', '')
    tecnico_mte  = d.get('tecnico_mte', '') or _mte_do_tecnico(tecnico)
    if tecnico_mte:
        tecnico = f'{tecnico} — MTE {tecnico_mte}'
    acomp        = d.get('acomp', d.get('acompanhante', ''))
    obs          = d.get('obs', d.get('observacoes', ''))
    sig_empresa  = d.get('sig_empresa')
    sig_avaliado = d.get('sig_avaliado')
    setores      = d.get('setores', [])

    if data_coleta and '-' in str(data_coleta):
        try:
            from datetime import datetime as _dt
            data_fmt = _dt.strptime(data_coleta, '%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception:
            data_fmt = _iso_br(data_coleta)
    else:
        data_fmt = data_coleta or '___/___/______'

    AZUL     = colors.HexColor('#1E3A8A')
    AZUL_CLR = colors.HexColor('#DBEAFE')
    CINZA    = colors.HexColor('#F3F4F6')
    BORDA    = colors.HexColor('#CBD5E1')
    PRETO    = colors.black
    BRANCO   = colors.white

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f'Planilha de Campo Calor — {empresa_nome}')

    styles = getSampleStyleSheet()
    def sty(name, **kw):
        base = styles.get(name, styles['Normal'])
        return ParagraphStyle(f'_cal_{name}_{id(kw)}', parent=base, **kw)

    cell_bold = sty('Normal', fontSize=8, fontName='Helvetica-Bold', textColor=PRETO)
    cell_reg  = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    assin_sty = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    footer_sty= sty('Normal', fontSize=6.5, textColor=colors.HexColor('#64748B'), alignment=TA_CENTER)
    sec_sty   = sty('Normal', fontSize=9, fontName='Helvetica-Bold', textColor=AZUL)

    W = A4[0] - 3.6*cm
    elements = []

    # Cabeçalho
    elements.append(_pdf_header(W, 'PLANILHA DE CAMPO — CALOR (IBUTG)',
        'NR-15 Anexo 3 · NHO-06'))
    elements.append(Spacer(1, 8))

    # Bloco de dados (grid)
    def _info(lbl, val):
        return Paragraph(f'<font size="7" color="#64748B">{lbl}</font><br/><b>{val or "—"}</b>', cell_reg)
    info_rows = [
        [_info('EMPRESA', empresa_nome), _info('CNPJ', cnpj), _info('OS', os_num)],
        [_info('UNIDADE', unidade), _info('CIDADE', cidade), _info('DATA', data_fmt)],
        [_info('TÉCNICO', tecnico), _info('ACOMPANHANTE', acomp),
         _info('HORÁRIO', f'{hora_ini}–{hora_fim}' if (hora_ini or hora_fim) else '—')],
        [_info('RESP. EMPRESA', resp_empresa), '', ''],
    ]
    info_tbl = Table(info_rows, colWidths=[W/3, W/3, W/3])
    info_tbl.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4),
        ('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),5),
        ('SPAN',(0,3),(2,3))]))
    elements.append(info_tbl); elements.append(Spacer(1, 10))

    # Tabela de setores + legenda (helper compartilhado c/ a planilha completa)
    elements.extend(_calor_setores_flowables(setores, W))
    elements.append(Spacer(1, 10))

    # Registro fotográfico — marcar em campo
    elements.append(Paragraph(
        '<font size="7" color="#64748B"><b>REGISTRO FOTOGRÁFICO (marcar)</b></font><br/>'
        '( ) Foto do ambiente   ( ) Foto do termômetro posicionado   ( ) Foto da atividade',
        cell_reg))
    elements.append(Spacer(1, 10))

    # Observações de campo
    elements.append(Paragraph('OBSERVAÇÕES DE CAMPO', sec_sty))
    elements.append(Spacer(1, 2))
    if obs:
        elements.append(Paragraph(str(obs), cell_reg))
    else:
        elements.append(Paragraph('_' * 110, cell_reg))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph('_' * 110, cell_reg))
    elements.append(Spacer(1, 10))

    # Assinaturas — responsável técnico (imagem) na 1ª coluna
    def _sig_img_cal(b64_str, w=4.6*cm, h=1.2*cm):
        if not b64_str:
            return None
        try:
            import base64 as _b64
            from io import BytesIO as _BIO
            from reportlab.platypus import Image as _RLImg
            raw = _b64.b64decode(b64_str.split(',')[-1])
            return _RLImg(_BIO(raw), width=w, height=h)
        except Exception:
            return None
    img_tec  = _sig_img_cal(sig_empresa)    # Técnico da Ocupacional
    img_resp = _sig_img_cal(sig_avaliado)   # Responsável / acompanhante da empresa
    col_tec  = (img_tec  if img_tec  else Paragraph('_________________________________', assin_sty))
    col_resp = (img_resp if img_resp else Paragraph('_________________________________', assin_sty))
    _resp_nome = resp_empresa or acomp or ''
    assin_rows = [
        [col_tec, col_resp, ''],
        [Paragraph('<font size="7">Técnico da Ocupacional (Responsável pela medição)</font><br/>'
                   f'<font size="7">{tecnico}</font>', assin_sty),
         Paragraph('<font size="7">Responsável da Empresa / Acompanhante</font>'
                   + (f'<br/><font size="7">{_resp_nome}</font>' if _resp_nome else ''), assin_sty),
         Paragraph(f'_________________________________<br/><font size="7">Data: {data_fmt}</font>', assin_sty)],
    ]
    assin_tbl = Table(assin_rows, colWidths=[W/3, W/3, W/3])
    assin_tbl.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4)]))
    elements.append(assin_tbl); elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        'Declaro estar ciente das atividades realizadas, atividades não realizadas, '
        'observações registradas e eventuais impedimentos descritos neste relatório.',
        sty('Normal', fontSize=6.5, fontName='Helvetica-Oblique',
            textColor=colors.HexColor('#475569'), alignment=TA_CENTER)))
    elements.append(Spacer(1, 6))

    # Registro fotográfico (fotos anexadas)
    elements.extend(_fotos_pdf_flowables(d.get('fotos') or [], W))

    elements.append(HRFlowable(width=W, thickness=0.5, color=BORDA))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        'Normas: NR-15 Anexo 3 · NHO-06 FUNDACENTRO | '
        f'Gerado em: {agora_brt().strftime("%d/%m/%Y %H:%M")} | Ocupacional — Medicina e Segurança do Trabalho',
        footer_sty))

    doc.build(elements)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa_nome)[:40]
    data_safe = data_fmt.replace('/', '-')
    return send_file(buf, as_attachment=True,
        download_name=f'planilha_calor_{nome_safe}_{data_safe}.pdf',
        mimetype='application/pdf')


# ── Relatório PDF de Campo — Agentes Químicos ─────────────────────────
@controle_bp.route('/relatorio/quimico', methods=['POST'])
def gerar_relatorio_quimico():
    """Gera PDF do Relatório de Campo de Agentes Químicos (multi-agente)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable,
                                        PageBreak, KeepTogether)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = _sanitize_rl(request.json or {})
    agentes        = d.get('agentes', [])
    empresa_nome   = d.get('empresa_nome', '—')
    unidade        = d.get('unidade', '')
    cidade         = d.get('cidade', '')
    resp_empresa   = d.get('resp_empresa', '')
    os_num         = d.get('os', '')
    data_coleta    = d.get('data_coleta', '')
    tecnico        = d.get('tecnico', '')
    tecnico_mte    = d.get('tecnico_mte', '') or _mte_do_tecnico(tecnico)
    if tecnico_mte:
        tecnico = f'{tecnico} — MTE {tecnico_mte}'
    sig_avaliado   = d.get('sig_avaliado')   # base64 PNG ou None
    sig_empresa    = d.get('sig_empresa')    # base64 PNG ou None

    # Formatar data
    if data_coleta and '-' in str(data_coleta):
        try:
            from datetime import datetime as _dt
            _d = _dt.strptime(data_coleta, '%Y-%m-%d')
            data_fmt     = _d.strftime('%d/%m/%Y')
            dia_semana   = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'][_d.weekday()]
        except:
            data_fmt   = _iso_br(data_coleta)
            dia_semana = ''
    else:
        data_fmt   = data_coleta or '___/___/______'
        dia_semana = ''

    # ─── Cores ────────────────────────────────────────────────────────
    AZUL    = colors.HexColor('#1E3A8A')
    AZUL_C  = colors.HexColor('#DBEAFE')
    CINZA   = colors.HexColor('#F3F4F6')
    BORDA   = colors.HexColor('#CBD5E1')
    BRANCO  = colors.white
    PRETO   = colors.black

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f'Relatório de Campo Químico — {empresa_nome}')

    styles = getSampleStyleSheet()
    def _s(name, **kw):
        s = styles[name].clone(name + str(id(kw)))
        for k,v in kw.items(): setattr(s, k, v)
        return s

    hdr1  = _s('Heading1', fontSize=11, textColor=AZUL, spaceAfter=2, spaceBefore=4)
    hdr2  = _s('Heading2', fontSize=9,  textColor=AZUL, spaceAfter=2, spaceBefore=6)
    norm  = _s('Normal',   fontSize=8,  leading=11)
    small = _s('Normal',   fontSize=7,  leading=10, textColor=colors.HexColor('#555555'))
    bold  = _s('Normal',   fontSize=8,  leading=11, fontName='Helvetica-Bold')
    ctr   = _s('Normal',   fontSize=8,  alignment=TA_CENTER)

    def _hdr_tbl(label):
        return Table([[Paragraph(label, _s('Normal', fontSize=9, fontName='Helvetica-Bold',
                                           textColor=BRANCO))]], colWidths=['*'],
                     style=TableStyle([
                         ('BACKGROUND',(0,0),(-1,-1),AZUL),
                         ('LEFTPADDING',(0,0),(-1,-1),6),
                         ('TOPPADDING',(0,0),(-1,-1),4),
                         ('BOTTOMPADDING',(0,0),(-1,-1),4),
                     ]))

    def _row2(l1, v1, l2='', v2='', w1=3.5*cm, w2=None):
        """Linha de 2 campos label+valor."""
        pw = 17.4*cm
        w2 = w2 or (pw - w1*2)
        def cell(lbl, val):
            return Paragraph(f'<b>{lbl}</b> {val or "___________"}', norm)
        return Table([[cell(l1,v1), cell(l2,v2)]],
                     colWidths=[pw/2, pw/2],
                     style=TableStyle([
                         ('LEFTPADDING',(0,0),(-1,-1),4),
                         ('TOPPADDING',(0,0),(-1,-1),2),
                         ('BOTTOMPADDING',(0,0),(-1,-1),2),
                         ('BOX',(0,0),(-1,-1),0.3,BORDA),
                         ('INNERGRID',(0,0),(-1,-1),0.3,BORDA),
                     ]))

    def _norm(s):
        return s.lower().replace('–','-').replace('—','-').replace(' ','').replace('ã','a')

    def _chk(options, selected_list):
        """Linha de checkboxes. selected_list = list of values que foram selecionados."""
        parts = []
        sel_norm = [_norm(s) for s in selected_list]
        for opt in options:
            mark = '[X]' if _norm(opt) in sel_norm else '[  ]'
            parts.append(f'<b>{mark}</b> {opt}')
        return '   '.join(parts)

    story = []

    # ─── Capa / Cabeçalho geral ──────────────────────────────────────
    story.append(_pdf_header(17.4*cm, 'RELATÓRIO DE CAMPO — AGENTES QUÍMICOS',
                             'NR-15 Anexo 13 · NHO-03 FUNDACENTRO'))
    story.append(Spacer(1, 8))

    # Identificação da empresa (seção comum)
    story.append(_hdr_tbl('IDENTIFICAÇÃO DA EMPRESA AMOSTRADA'))
    story.append(_row2('Empresa amostrada:', empresa_nome, 'OS Nº:', os_num))
    story.append(_row2('Responsável pela coleta:', tecnico, 'Cidade:', cidade))
    story.append(_row2('Unidade / Obra:', unidade, 'Responsável na empresa:', resp_empresa))
    story.append(Spacer(1, 10))

    # ─── Seção por agente (blocos compartilhados c/ a planilha completa) ──
    for idx, ag in enumerate(agentes):
        if idx > 0:
            story.append(PageBreak())
        story.extend(_quimico_agente_flowables(ag, idx, 17.4*cm, data_fmt, dia_semana))

    # ─── Assinaturas (uma vez, ao final do documento) ─────────────────
    import base64 as _b64
    from io import BytesIO as _BIO
    from reportlab.platypus import Image as _RLImg

    def _sig_img(b64_str, w=8.3*cm, h=1.8*cm):
        if not b64_str:
            return None
        try:
            raw = _b64.b64decode(b64_str.split(',')[-1])
            return _RLImg(_BIO(raw), width=w, height=h)
        except Exception:
            return None

    img_av = _sig_img(sig_avaliado)
    img_em = _sig_img(sig_empresa)

    sig_style2 = TableStyle([
        ('LINEABOVE', (0,0), (-1,0), 0.5, PRETO),
        ('ALIGN',     (0,0), (-1,-1), 'CENTER'),
        ('FONTSIZE',  (0,0), (-1,-1), 7),
        ('TOPPADDING',(0,0), (-1,-1), 4),
        ('BOTTOMPADDING',(0,0), (-1,-1), 4),
    ])
    story.append(Spacer(1, 16))
    story.append(Table([
        [img_em or '', img_av or ''],
        [Paragraph('Técnico da Ocupacional<br/><font size="6">(Responsável pela medição)</font>', small),
         Paragraph('Responsável da Empresa / Acompanhante', small)],
    ], colWidths=[8.5*cm, 8.5*cm], style=sig_style2))

    # ─── Registro fotográfico ─────────────────────────────────────────
    story.extend(_fotos_pdf_flowables(d.get('fotos') or [], 17*cm))

    # ─── Gerar PDF ────────────────────────────────────────────────────
    doc.build(story)
    buf.seek(0)
    nome_safe = empresa_nome.replace(' ','_').replace('/','_')[:30]
    data_safe = data_fmt.replace('/','-')
    return send_file(buf, as_attachment=True,
        download_name=f'relatorio_campo_quimico_{nome_safe}_{data_safe}.pdf',
        mimetype='application/pdf')


# ══════════════════════════════════════════════════════════════════════
# MICROSOFT GRAPH — Planner, Outlook, SharePoint
# ══════════════════════════════════════════════════════════════════════

@controle_bp.route('/graph/status')
def graph_status():
    """Verifica conexão com Microsoft Graph."""
    try:
        from .graph import graph_ok, CLIENT_ID, TENANT_ID
        from .planner_sync import get_sync_status
        ok   = graph_ok()
        sync = get_sync_status()
        return jsonify({
            'configurado':  ok,
            'client_id':   (CLIENT_ID[:8] + '...') if CLIENT_ID else '',
            'tenant_id':   (TENANT_ID[:8] + '...') if TENANT_ID else '',
            'last_sync':   sync.get('last_sync'),
            'last_stats':  sync.get('stats', {}),
        })
    except Exception as e:
        return jsonify({'configurado': False, 'erro': str(e)}), 500


# ── Pipeline / Operational Demands ───────────────────────────────────

@controle_bp.route('/operacional')
def get_operacional():
    """Demandas operacionais limpas (só clientes reais, sem interna/administrativa)."""
    init_db()
    return jsonify(list_operational_demands(request.args.to_dict()))


@controle_bp.route('/operacional/por_empresa')
def get_operacional_por_empresa():
    """Demandas operacionais agrupadas por empresa."""
    init_db()
    return jsonify(list_operational_por_empresa(request.args.to_dict()))


@controle_bp.route('/operacional/buckets')
def get_operacional_buckets():
    """Retorna lista de planner_buckets distintos para filtro."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT planner_bucket FROM demandas
            WHERE planner_bucket IS NOT NULL AND planner_bucket != ''
              AND origem = 'planner'
            ORDER BY planner_bucket
        """).fetchall()
    buckets = [r['planner_bucket'] if isinstance(r, dict) else r[0] for r in rows]
    return jsonify(buckets)


@controle_bp.route('/pipeline/raw_tasks')
def get_raw_tasks():
    """Raw tasks do Planner (staging — todas, com status do pipeline)."""
    init_db()
    limit = _qint('limit', 200, 1000)
    filtros = {k: v for k, v in request.args.to_dict().items() if k != 'limit'}
    return jsonify(list_raw_tasks(filtros, limit=limit))


@controle_bp.route('/pipeline/stats')
def get_pipeline_stats():
    """Estatísticas do pipeline: quantas ignoradas por bucket, etc."""
    init_db()
    return jsonify(stats_raw_pipeline())


@controle_bp.route('/relatorio/mensal')
def api_relatorio_mensal():
    """Resumo executivo do mês: visitas, agentes medidos, empresas atendidas."""
    init_db()
    import calendar
    mes = request.args.get('mes', '')  # formato YYYY-MM
    if not mes:
        from datetime import date
        hoje = date.today()
        mes = f'{hoje.year}-{hoje.month:02d}'
    try:
        ano, num_mes = mes.split('-')
        ano, num_mes = int(ano), int(num_mes)
    except Exception:
        return jsonify({'erro': 'mes deve ser YYYY-MM'}), 400

    with get_db() as conn:
        # Visitas no mês
        visitas = [row_to_dict(r) for r in conn.execute("""
            SELECT vt.id, vt.tecnico, vt.data_visita, vt.resultado, vt.retrabalho,
                   e.nome AS empresa_nome, d.numero_os
            FROM visitas_tecnicas vt
            LEFT JOIN empresas e ON e.id = vt.empresa_id
            LEFT JOIN demandas d ON d.id = vt.demanda_id
            WHERE vt.data_visita LIKE ?
            ORDER BY vt.data_visita
        """, (f'{ano}-{num_mes:02d}%',)).fetchall()]

        # Coletas ruído no mês
        coletas_ruido = conn.execute(
            "SELECT COUNT(*) AS c FROM coletas_ruido WHERE data_coleta LIKE ?",
            (f'{ano}-{num_mes:02d}%',)
        ).fetchone()['c']

        # Coletas químico no mês
        coletas_quimico = conn.execute(
            "SELECT COUNT(*) AS c FROM coletas_quimico WHERE data_coleta LIKE ?",
            (f'{ano}-{num_mes:02d}%',)
        ).fetchone()['c']

        # Amostradores enviados ao lab no mês
        enviados_lab = conn.execute(
            "SELECT COUNT(*) AS c FROM amostradores WHERE data_envio_lab LIKE ?",
            (f'{ano}-{num_mes:02d}%',)
        ).fetchone()['c']

        # Demandas concluídas no mês
        concluidas = conn.execute(
            "SELECT COUNT(*) AS c FROM demandas WHERE status='concluida' AND data_conclusao LIKE ?",
            (f'{ano}-{num_mes:02d}%',)
        ).fetchone()['c']

        # Empresas únicas atendidas
        empresas_set = set(v['empresa_nome'] for v in visitas if v.get('empresa_nome'))

    return jsonify({
        'mes': mes,
        'visitas': visitas,
        'resumo': {
            'total_visitas': len(visitas),
            'visitas_concluidas': sum(1 for v in visitas if v.get('resultado') == 'concluido'),
            'retrabalhos': sum(1 for v in visitas if v.get('retrabalho')),
            'empresas_atendidas': len(empresas_set),
            'coletas_ruido': coletas_ruido,
            'coletas_quimico': coletas_quimico,
            'amostradores_enviados_lab': enviados_lab,
            'demandas_concluidas': concluidas,
        },
    })


@controle_bp.route('/metricas/calcular', methods=['POST'])
def api_calcular_metricas():
    """Recalcula metricas_operacionais para todas as demandas."""
    init_db()
    from .db import calcular_metricas_lote
    resultado = calcular_metricas_lote()
    return jsonify({'ok': True, **resultado})


@controle_bp.route('/metricas')
def api_metricas():
    """KPIs calculados: lead time médio, delay médio, top retrabalho."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.demanda_id, m.lead_time_dias, m.delay_dias, m.retrabalho,
                   m.visitas_total, m.calculado_em,
                   COALESCE(d.titulo, d.nome_tarefa) AS titulo,
                   e.nome AS empresa_nome
            FROM metricas_operacionais m
            LEFT JOIN demandas d ON d.id = m.demanda_id
            LEFT JOIN empresas e ON e.id = d.empresa_id
            ORDER BY m.retrabalho DESC, m.delay_dias DESC LIMIT 100
        """).fetchall()
        stats = conn.execute("""
            SELECT ROUND(AVG(lead_time_dias),1) AS lead_medio,
                   ROUND(AVG(CASE WHEN delay_dias > 0 THEN delay_dias END),1) AS delay_medio,
                   SUM(retrabalho) AS total_retrabalho,
                   COUNT(*) AS total
            FROM metricas_operacionais
        """).fetchone()
    return jsonify({
        'stats': row_to_dict(stats) if stats else {},
        'demandas': [row_to_dict(r) for r in rows],
    })



@controle_bp.route('/db/status')
def db_status():
    """Informa qual backend de banco está ativo e testa conectividade."""
    import os
    backend = 'postgresql' if USE_PG else 'sqlite'
    db_url_hint = ''
    if USE_PG:
        url = os.environ.get('DATABASE_URL', '')
        # Oculta senha
        import re as _re
        db_url_hint = _re.sub(r':([^@]+)@', ':***@', url)
    else:
        from .db import DB_PATH
        db_url_hint = DB_PATH
    try:
        with get_db() as conn:
            cnt = conn.execute('SELECT COUNT(*) AS c FROM empresas').fetchone()['c']
        ok = True
        empresas = cnt
    except Exception as e:
        ok = False
        empresas = str(e)
    return jsonify({
        'backend': backend,
        'persistent': USE_PG,
        'connection': db_url_hint,
        'ok': ok,
        'empresas_count': empresas,
    })


@controle_bp.route('/graph/sync', methods=['POST'])
def graph_sync_manual():
    """Dispara sync manual do Planner em background (evita timeout do gunicorn)."""
    init_db()
    try:
        from .planner_sync import sync_planner
        import threading
        from flask import current_app
        d = request.json or {}
        group_filter = d.get('group_id')
        label_filter = d.get('label_filter', 'Medições')  # padrão: só tasks com label Medições

        app = current_app._get_current_object()

        def _run():
            with app.app_context():
                try:
                    sync_planner(group_filter=group_filter, label_filter=label_filter)
                except Exception as ex:
                    import logging, traceback
                    logging.getLogger(__name__).error('[graph/sync] erro no background: %s', ex)
                    # Salva erro no DB para debug
                    try:
                        with get_db() as c:
                            c.execute("""INSERT INTO ms_sync_state (chave,valor,atualizado_em) VALUES ('last_sync_error',?,CURRENT_TIMESTAMP)
                                         ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=EXCLUDED.atualizado_em""",
                                      (traceback.format_exc()[:2000],))
                    except Exception:
                        pass

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'mensagem': 'Sync iniciado em background — verifique /graph/status em alguns minutos'})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/admin/limpar_demandas_invalidas', methods=['POST'])
@login_required
def limpar_demandas_invalidas():
    """Remove demandas vinculadas a tasks do Planner que foram marcadas como 'ignored'
    (sem label Medições). Usado para corrigir syncs com bug de filtro."""
    init_db()
    try:
        with get_db() as conn:
            # Conta antes
            total_antes = conn.execute('SELECT COUNT(*) as n FROM demandas').fetchone()['n']
            # Deleta demandas de grupos incorretos (sync com bug processou grupos fora do Ocupacional)
            # O grupo correto é o Ocupacional: 4c80214b-6801-414a-9fc7-27feff0b3de6
            GRUPO_CORRETO = '4c80214b-6801-414a-9fc7-27feff0b3de6'
            conn.execute("""
                DELETE FROM demandas
                WHERE planner_task_id IS NOT NULL
                  AND origem = 'planner'
                  AND (planner_group_id != ? OR planner_group_id IS NULL)
            """, (GRUPO_CORRETO,))
            total_depois = conn.execute('SELECT COUNT(*) as n FROM demandas').fetchone()['n']
        deletadas = total_antes - total_depois
        registrar_evento('limpeza_demandas', f'{deletadas} demandas inválidas removidas',
                         usuario=current_user.nome, ip=request.remote_addr)
        return jsonify({'ok': True, 'antes': total_antes, 'depois': total_depois, 'deletadas': deletadas})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/debug_labels')
def graph_debug_labels():
    """Debug: retorna os labels (categoryDescriptions) de todos os planos conhecidos.
    Útil para validar qual categoryN corresponde a 'Medições'."""
    init_db()
    try:
        from .graph import graph_ok, get_plans_for_group, get_teams_groups, get_plan_category_map
        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503
        grupos = get_teams_groups()
        resultado = []
        for g in grupos[:10]:  # limita a 10 grupos para não demorar
            gid   = g.get('id', '')
            gnome = g.get('displayName', '')
            try:
                planos = get_plans_for_group(gid)
            except Exception:
                continue
            for p in planos[:5]:
                pid   = p.get('id', '')
                pnome = p.get('title', '')
                try:
                    cat_map = get_plan_category_map(pid)
                    # Filtrar só labels não-nulos
                    labels = {k: v for k, v in cat_map.items() if v}
                    medicoes_cats = [k for k, v in cat_map.items() if v and 'medic' in v.lower().replace('ç','c').replace('õ','o')]
                    resultado.append({
                        'grupo':         gnome,
                        'plano':         pnome,
                        'plan_id':       pid,
                        'labels':        labels,
                        'medicoes_cats': medicoes_cats,
                    })
                except Exception as e:
                    resultado.append({'grupo': gnome, 'plano': pnome, 'erro': str(e)})
        return jsonify(resultado)
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/debug_plan')
def graph_debug_plan():
    """Debug: inspeciona labels de um plano específico por plan_id.
    Uso: /controle/graph/debug_plan?plan_id=<ID>"""
    plan_id = request.args.get('plan_id', '').strip()
    if not plan_id:
        return jsonify({'erro': 'Informe ?plan_id=<ID do plano no Planner>'}), 400
    try:
        from .graph import graph_ok, get_plan_category_map, get_plan_buckets, get_plan_tasks
        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503
        cat_map = get_plan_category_map(plan_id)
        labels_nao_nulos = {k: v for k, v in cat_map.items() if v}
        medicoes_cats = [k for k, v in cat_map.items() if v and 'medic' in v.lower().replace('ç', 'c').replace('õ', 'o')]
        buckets = get_plan_buckets(plan_id)
        tasks_sample = get_plan_tasks(plan_id)
        # Mostra applied categories de até 5 tarefas
        tasks_info = [
            {'titulo': t.get('title', '')[:80],
             'appliedCategories': t.get('appliedCategories', {}),
             'bucket_id': t.get('bucketId', '')}
            for t in tasks_sample[:10]
        ]
        return jsonify({
            'plan_id': plan_id,
            'all_labels': cat_map,
            'labels_nao_nulos': labels_nao_nulos,
            'medicoes_cats': medicoes_cats,
            'buckets': [{'id': b['id'], 'nome': b.get('name', '')} for b in buckets],
            'total_tasks': len(tasks_sample),
            'tasks_sample': tasks_info,
        })
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/debug_groups')
def graph_debug_groups():
    """Debug: lista TODOS os grupos M365, com contagem de planos Planner."""
    try:
        from .graph import graph_ok, get_teams_groups, get_plans_for_group
        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503
        grupos = get_teams_groups()
        resultado = []
        for g in grupos:
            gid   = g.get('id', '')
            gnome = g.get('displayName', '')
            try:
                planos = get_plans_for_group(gid)
                if planos:
                    resultado.append({
                        'id': gid, 'nome': gnome,
                        'planos': [{'id': p['id'], 'titulo': p.get('title', '')} for p in planos]
                    })
            except Exception:
                pass  # grupo sem Planner
        return jsonify({'total_grupos': len(grupos), 'grupos_com_planner': resultado})
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/debug/demandas_fantasmas')
def debug_demandas_fantasmas():
    """Debug: demandas sem numero_os e sem titulo — mostra campos brutos para investigação."""
    try:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT d.id, d.numero_os, d.titulo, d.nome_tarefa, d.status,
                       d.empresa_id, e.nome AS empresa_nome,
                       d.planner_task_id, d.planner_bucket, d.origem,
                       d.descricao, d.checklist, d.extracao_json,
                       d.responsavel, d.prazo,
                       (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id) AS n_medicoes
                FROM demandas d
                LEFT JOIN empresas e ON e.id = d.empresa_id
                WHERE (d.numero_os IS NULL OR d.numero_os = '')
                  AND (d.titulo   IS NULL OR d.titulo   = '')
                ORDER BY d.id DESC
                LIMIT 50
            """).fetchall()
        result = []
        for r in rows:
            row = dict(r)
            # Tentar extrair checklist
            import json as _j
            try:
                row['checklist_parsed'] = _j.loads(row.get('checklist') or '[]')
            except Exception:
                row['checklist_parsed'] = []
            try:
                row['extracao'] = _j.loads(row.get('extracao_json') or '{}')
            except Exception:
                row['extracao'] = {}
            result.append(row)
        return jsonify({'total': len(result), 'demandas': result})
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/demandas/limpar-fantasmas', methods=['POST'])
def api_limpar_fantasmas():
    """Remove demandas sem OS, sem título e sem medições (entradas fantasma do Planner).
    Aceita ?dry_run=1 para listar sem deletar.
    """
    from flask_login import current_user
    if not current_user.is_authenticated or getattr(current_user, 'role', '') != 'admin':
        return jsonify({'erro': 'apenas admin'}), 403
    dry_run = request.args.get('dry_run', '0') == '1'
    init_db()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT d.id, d.numero_os, d.titulo, d.nome_tarefa
            FROM demandas d
            WHERE (d.numero_os IS NULL OR d.numero_os = '' OR d.numero_os LIKE 'AET-%')
              AND (d.titulo IS NULL OR d.titulo = '')
              AND NOT EXISTS (SELECT 1 FROM medicoes m WHERE m.demanda_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM coletas_ruido cr WHERE cr.demanda_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM coletas_quimico cq WHERE cq.demanda_id = d.id)
              AND d.origem = 'planner'
        """).fetchall()
        ids = [r['id'] for r in rows]
        if not dry_run and ids:
            conn.execute(f"DELETE FROM demandas WHERE id IN ({','.join('?'*len(ids))})", ids)
    return jsonify({
        'ok': True,
        'dry_run': dry_run,
        'total': len(ids),
        'ids': ids[:100],
    })


@controle_bp.route('/graph/debug_empresa_titulos')
def graph_debug_empresa_titulos():
    """Debug: retorna títulos das demandas vinculadas a uma empresa (por id ou nome parcial)."""
    try:
        empresa_id = request.args.get('empresa_id', type=int)
        search = request.args.get('search', '')
        with get_db() as conn:
            if empresa_id:
                rows = conn.execute(
                    'SELECT d.id, d.titulo, d.empresa_id, e.nome empresa_nome, d.origem FROM demandas d LEFT JOIN empresas e ON e.id=d.empresa_id WHERE d.empresa_id=? LIMIT 20',
                    (empresa_id,)
                ).fetchall()
            elif search:
                rows = conn.execute(
                    "SELECT d.id, d.titulo, d.empresa_id, e.nome empresa_nome, d.origem FROM demandas d LEFT JOIN empresas e ON e.id=d.empresa_id WHERE LOWER(COALESCE(d.titulo,'')) LIKE LOWER(?) OR LOWER(COALESCE(e.nome,'')) LIKE LOWER(?) LIMIT 20",
                    (f'%{search}%', f'%{search}%')
                ).fetchall()
            else:
                return jsonify({'erro': 'passe empresa_id= ou search='})
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/sync_error')
def graph_sync_error():
    """Retorna o último erro de sync para debug."""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT valor, atualizado_em FROM ms_sync_state WHERE chave='last_sync_error'").fetchone()
        if row:
            return jsonify({'erro': row['valor'], 'quando': row['atualizado_em']})
        return jsonify({'erro': None, 'msg': 'Nenhum erro registrado'})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/test_mail')
def graph_test_mail():
    """Verifica se o APP (identidade de Aplicação) tem permissão Mail.Read.All:
    tenta ler 1 e-mail de uma caixa. 403 = permissão NÃO concedida no Azure."""
    init_db()
    from .graph import list_emails
    mailbox = request.args.get('mailbox', 'engenharia19@ocupacional.com.br')
    try:
        msgs = list_emails(mailbox, top=1)
        return jsonify({
            'ok': True,
            'mailbox': mailbox,
            'permissao_mail_read': 'CONCEDIDA ✓',
            'lidos': len(msgs),
            'amostra_assunto': (msgs[0].get('subject') if msgs else '(caixa vazia)'),
            'conclusao': 'O app PODE ler e-mails — dá para construir a ingestão automática.'
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        msg = str(e)
        code = getattr(e, 'code', None)
        sem_perm = (code in (401, 403)) or ('403' in msg) or ('Forbidden' in msg) or ('Access' in msg) or ('Authorization' in msg)
        return jsonify({
            'ok': False,
            'mailbox': mailbox,
            'permissao_mail_read': 'NEGADA ✗' if sem_perm else 'erro ao testar',
            'erro': msg,
            'codigo_http': code,
            'conclusao': ('Falta a permissão Mail.Read.All (Aplicação) com consentimento do admin no Azure '
                          'para o app registrado (AZURE_CLIENT_ID). Sem isso, a ingestão de e-mails não roda.')
                         if sem_perm else 'Erro inesperado — ver o campo "erro".'
        })


@controle_bp.route('/graph/lab_preview')
def graph_lab_preview():
    """PREVIEW (dry-run) da ingestão dos e-mails do laboratório — NÃO grava nada.
    Mostra, por categoria, quantos e-mails/códigos e quantos casam com o inventário."""
    init_db()
    from .lab_inbox import preview
    try:
        return jsonify(preview())
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/lab_sync', methods=['POST'])
def graph_lab_sync():
    """APLICA a ingestão dos e-mails do lab em BACKGROUND (varre a caixa oficial +
    a de cada técnico cadastrado; evita timeout do gunicorn). Atualiza status,
    pendentes e resultados; o resumo fica em ms_sync_state['lab_sync_result']."""
    init_db()
    try:
        from .lab_inbox import sincronizar_lab
        import threading
        from flask import current_app
        app = current_app._get_current_object()
        quem = current_user.nome if current_user.is_authenticated else 'sistema'
        ip = request.remote_addr

        def _run():
            with app.app_context():
                try:
                    r = sincronizar_lab(apply=True)
                    registrar_evento('sync_planner',
                                     f"Ingestão lab: {r.get('aplicadas', 0)} status, "
                                     f"{r.get('resultados_total', 0)} resultados, "
                                     f"{r.get('mailboxes_lidas', 0)} caixas varridas",
                                     None, 'amostrador', quem, ip)
                except Exception as ex:
                    import logging, traceback
                    logging.getLogger(__name__).error('[lab_sync] erro no background: %s', ex)
                    try:
                        with get_db() as c:
                            c.execute("INSERT INTO ms_sync_state (chave,valor,atualizado_em) VALUES ('last_sync_error',?,CURRENT_TIMESTAMP) "
                                      "ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=EXCLUDED.atualizado_em",
                                      (traceback.format_exc()[:2000],))
                    except Exception:
                        pass

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'async': True,
                        'mensagem': 'Varredura iniciada em background — o painel atualiza em ~1 min.'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/ra_backfill_preview')
def graph_ra_backfill_preview():
    """PREVIEW (dry-run) do backfill histórico de RAs — NÃO grava nada. Varre TODO
    o histórico do lab (via $search), casa cada laudo pelo código do amostrador e
    relata quantos concluiriam (amostradores parados no laboratório com RA já emitido)."""
    init_db()
    from .lab_inbox import backfill_ras
    try:
        return jsonify(backfill_ras(apply=False))
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/ra_backfill', methods=['POST'])
def graph_ra_backfill():
    """APLICA o backfill histórico de RAs em BACKGROUND: lê o PDF de cada laudo,
    extrai amostrador/funcionário/resultados, seta data_resultado + conclui o
    amostrador que ainda está no laboratório e guarda o laudo em ra_laudos."""
    init_db()
    try:
        from .lab_inbox import backfill_ras
        import threading
        from flask import current_app
        app = current_app._get_current_object()
        quem = current_user.nome if current_user.is_authenticated else 'sistema'
        ip = request.remote_addr

        def _run():
            with app.app_context():
                try:
                    r = backfill_ras(apply=True)
                    registrar_evento('sync_planner',
                                     f"Backfill RAs: {r.get('concluiriam', 0)} amostradores concluídos, "
                                     f"{r.get('casaram', 0)} laudos casados, "
                                     f"{r.get('medicoes_baixadas', 0)} medições baixadas",
                                     None, 'amostrador', quem, ip)
                except Exception as ex:
                    import logging, traceback
                    logging.getLogger(__name__).error('[ra_backfill] erro no background: %s', ex)
                    try:
                        with get_db() as c:
                            c.execute("INSERT INTO ms_sync_state (chave,valor,atualizado_em) VALUES ('last_sync_error',?,CURRENT_TIMESTAMP) "
                                      "ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=EXCLUDED.atualizado_em",
                                      (traceback.format_exc()[:2000],))
                    except Exception:
                        pass

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'async': True,
                        'mensagem': 'Backfill de RAs iniciado em background — pode levar alguns minutos (baixa os PDFs). O painel atualiza ao terminar.'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/ra_backfill_result')
def graph_ra_backfill_result():
    """Relatório da última execução APLICADA do backfill de RAs, incluindo os
    erros por laudo (report['erros']) — sem isto o erro real de um laudo que
    falhou ficava só no stdout do Railway e a UI nunca mostrava nada."""
    init_db()
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state "
                         "(chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
            row = conn.execute("SELECT valor, atualizado_em FROM ms_sync_state "
                               "WHERE chave='ra_backfill_result'").fetchone()
        if not row:
            return jsonify({'info': 'backfill ainda não rodou em modo aplicado'})
        d = row_to_dict(row)
        out = json.loads(d.get('valor') or '{}')
        out['quando'] = str(d.get('atualizado_em'))
        return jsonify(out)
    except Exception as e:
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/groups')
def graph_list_groups():
    """Lista grupos Teams/Microsoft 365 disponíveis."""
    try:
        from .graph import get_teams_groups
        grupos = get_teams_groups()
        return jsonify([{
            'id':        g['id'],
            'nome':      g.get('displayName', ''),
            'descricao': g.get('description', ''),
            'email':     g.get('mail', ''),
        } for g in grupos])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/plans')
def graph_list_plans():
    """Lista planos do Planner de um grupo (query param: group_id)."""
    gid = request.args.get('group_id', '')
    if not gid:
        return jsonify({'erro': 'group_id obrigatorio'}), 400
    try:
        from .graph import get_plans_for_group, get_plan_buckets
        planos = get_plans_for_group(gid)
        result = []
        for p in planos:
            buckets = []
            try:
                buckets = [{'id': b['id'], 'nome': b.get('name', '')}
                           for b in get_plan_buckets(p['id'])]
            except Exception:
                pass
            result.append({'id': p['id'], 'titulo': p.get('title', ''), 'buckets': buckets})
        return jsonify(result)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/debug_envio')
def graph_debug_envio():
    """DEBUG: lista e-mails ENVIADOS (sentitems) de uma caixa PARA o laboratório,
    p/ descobrir o formato da cadeia de custódia (data real do envio + códigos +
    se vêm no texto ou só em anexo)."""
    init_db()
    from .graph import graph_get
    from .lab_inbox import LAB_DOM, _codes
    mailbox = request.args.get('mailbox', 'engenharia7@ocupacional.com.br')
    try:
        data = graph_get(
            f"/users/{mailbox}/mailFolders/sentitems/messages"
            f"?$top=40&$orderby=sentDateTime desc"
            f"&$select=subject,toRecipients,sentDateTime,hasAttachments,bodyPreview,body")
        out = []
        for m in data.get('value', []):
            tos = [(((t or {}).get('emailAddress') or {}).get('address') or '').lower()
                   for t in (m.get('toRecipients') or [])]
            if not any(LAB_DOM in t for t in tos):
                continue
            body_txt = (m.get('body') or {}).get('content', '')
            out.append({
                'subject': m.get('subject', ''),
                'to': tos,
                'data': (m.get('sentDateTime') or '')[:10],
                'anexos': m.get('hasAttachments'),
                'codigos_no_texto': _codes(((m.get('subject', '') or '') + ' ' + body_txt))[:25],
                'preview': (m.get('bodyPreview') or '')[:180],
            })
        return jsonify({'mailbox': mailbox, 'enviados_ao_lab': len(out), 'amostra': out[:15]})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/debug_anexo')
def graph_debug_anexo():
    """DEBUG Fase 2: baixa os ANEXOS dos e-mails enviados ao lab e tenta extrair
    os códigos (PDF via pymupdf, xlsx via openpyxl). Mostra se dá p/ parsear."""
    init_db()
    from .graph import graph_get
    from .lab_inbox import LAB_DOM, _norm, _extrair_texto_anexo, _codigos_no_texto
    mailbox = request.args.get('mailbox', 'engenharia7@ocupacional.com.br')
    limite = _qint('limite', 2)
    try:
        data = graph_get(
            f"/users/{mailbox}/mailFolders/sentitems/messages"
            f"?$top=40&$orderby=sentDateTime desc"
            f"&$select=id,subject,toRecipients,sentDateTime,hasAttachments")
        out, vistos = [], 0
        for m in data.get('value', []):
            if not m.get('hasAttachments'):
                continue
            tos = [(((t or {}).get('emailAddress') or {}).get('address') or '').lower()
                   for t in (m.get('toRecipients') or [])]
            if not any(LAB_DOM in t for t in tos):
                continue
            if vistos >= limite:
                break
            vistos += 1
            # Lista anexos SEM baixar o conteúdo (rápido); baixa só o documento.
            metas = graph_get(f"/users/{mailbox}/messages/{m['id']}/attachments"
                              f"?$select=id,name,contentType,size").get('value', [])
            anexos_info = []
            for meta in metas:
                nome = meta.get('name', ''); ct = meta.get('contentType', ''); low = nome.lower()
                if not (low.endswith(('.pdf', '.xlsx', '.xlsm')) or 'pdf' in ct or 'spreadsheet' in ct):
                    continue  # pula imagens de assinatura
                full = graph_get(f"/users/{mailbox}/messages/{m['id']}/attachments/{meta['id']}")
                texto = _extrair_texto_anexo(nome, ct, full.get('contentBytes'))
                cods = _codigos_no_texto(texto)  # casa contra o inventário (reverso)
                # tokens alfanuméricos (letra+dígito) p/ ver como os códigos aparecem
                seen, cand = set(), []
                for t in re.findall(r'[A-Za-z0-9]{4,14}', texto or ''):
                    tu = t.upper()
                    if tu in seen:
                        continue
                    if re.search(r'[A-Z]', tu) and re.search(r'\d', tu):
                        seen.add(tu); cand.append(tu)
                anexos_info.append({'nome': nome, 'tipo': ct.split(';')[0],
                                    'kb': round((meta.get('size', 0) or 0) / 1024),
                                    'texto_len': len(texto), 'qtd_codigos': len(cods),
                                    'codigos': '|'.join(cods[:30]),
                                    'tokens': '|'.join(cand[:70])})
            if anexos_info:
                out.append({'data': (m.get('sentDateTime') or '')[:10],
                            'assunto': (m.get('subject') or '')[:60], 'anexos': anexos_info})
        return jsonify({'mailbox': mailbox, 'emails_com_anexo': len(out), 'amostra': out})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/debug_resultado')
def graph_debug_resultado():
    """DEBUG: pega e-mails de RESULTADO (RA) na inbox, baixa o PDF do laudo e
    extrai texto p/ ver se dá p/ casar os códigos do amostrador e ler valores."""
    init_db()
    from .graph import graph_get
    from .lab_inbox import _classificar, _extrair_texto_anexo, _codigos_no_texto
    mailbox = request.args.get('mailbox', 'engenharia19@ocupacional.com.br')
    limite = _qint('limite', 2)
    try:
        data = graph_get(
            f"/users/{mailbox}/mailFolders/inbox/messages"
            f"?$top=40&$orderby=receivedDateTime desc"
            f"&$select=id,subject,from,receivedDateTime,hasAttachments")
        out, vistos = [], 0
        for m in data.get('value', []):
            frm = (((m.get('from') or {}).get('emailAddress') or {}).get('address') or '').lower()
            if _classificar(frm, m.get('subject', '')) != 'resultado' or not m.get('hasAttachments'):
                continue
            if vistos >= limite:
                break
            vistos += 1
            metas = graph_get(f"/users/{mailbox}/messages/{m['id']}/attachments"
                              f"?$select=id,name,contentType,size").get('value', [])
            anexos = []
            for meta in metas:
                nome = meta.get('name', ''); ct = (meta.get('contentType') or ''); low = nome.lower()
                if not (low.endswith('.pdf') or 'pdf' in ct):
                    continue
                if (meta.get('size', 0) or 0) > 12 * 1024 * 1024:
                    continue
                full = graph_get(f"/users/{mailbox}/messages/{m['id']}/attachments/{meta['id']}")
                txt = _extrair_texto_anexo(nome, ct, full.get('contentBytes'))
                cods = _codigos_no_texto(txt)
                seen, cand = set(), []
                for t in re.findall(r'[A-Za-z0-9]{4,14}', txt or ''):
                    tu = t.upper()
                    if tu in seen:
                        continue
                    if re.search(r'[A-Z]', tu) and re.search(r'\d', tu):
                        seen.add(tu); cand.append(tu)
                anexos.append({'nome': nome, 'kb': round((meta.get('size', 0) or 0) / 1024),
                               'texto_len': len(txt), 'qtd_codigos': len(cods),
                               'codigos': '|'.join(cods[:20]), 'alfanum': '|'.join(cand[:70]),
                               'excerto': re.sub(r'\s+', ' ', txt)[:700]})
            if anexos:
                out.append({'data': (m.get('receivedDateTime') or '')[:10],
                            'assunto': (m.get('subject') or '')[:70], 'de': frm, 'anexos': anexos})
        return jsonify({'mailbox': mailbox, 'ra_com_pdf': len(out), 'amostra': out})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/debug_ra_search')
def graph_debug_ra_search():
    """DEBUG: procura RAs do lab em TODO o histórico (via $search from:LAB_DOM) e
    lista assunto + os códigos de amostrador extraídos do NOME dos PDFs (não baixa
    o conteúdo — rápido). ?termo=Morro filtra o assunto (empresa avaliada). Serve p/
    achar o resultado de um amostrador cujo código no laudo diverge do inventário."""
    init_db()
    from .graph import graph_get
    from .lab_inbox import _mailboxes, _norm, _codigo_do_anexo_ra, LAB_DOM
    termo = (request.args.get('termo', '') or '').strip().lower()
    out, vistos = [], set()
    for box in _mailboxes():
        try:
            data = graph_get(f"/users/{box}/messages"
                             f'?$search="from:{LAB_DOM}"&$top=200'
                             f"&$select=id,subject,receivedDateTime,hasAttachments")
        except Exception:
            continue
        for m in data.get('value', []):
            sub = (m.get('subject', '') or '')
            if not sub.strip().upper().startswith('RA '):
                continue
            if termo and termo not in sub.lower():
                continue
            key = sub.strip().lower()
            if key in vistos:
                continue
            vistos.add(key)
            cods = []
            if m.get('hasAttachments'):
                try:
                    metas = graph_get(f"/users/{box}/messages/{m['id']}/attachments"
                                      f"?$select=name").get('value', [])
                except Exception:
                    metas = []
                for meta in metas:
                    nome = meta.get('name', '')
                    if nome.lower().endswith('.pdf'):
                        c = _norm(_codigo_do_anexo_ra(nome))
                        if c:
                            cods.append(c)
            out.append({'assunto': sub[:130], 'data': (m.get('receivedDateTime') or '')[:10],
                        'codigos': list(dict.fromkeys(cods))})
    out.sort(key=lambda x: x['data'])
    return jsonify({'termo': termo, 'total': len(out), 'ras': out})


@controle_bp.route('/graph/auditoria_lab', methods=['GET', 'POST'])
def graph_auditoria_lab():
    """AUDITORIA: reconcilia o que o lab ENVIOU (RAs na inbox + cadeias nos enviados)
    com o que o site CAPTUROU (data_resultado / data_envio_lab). GET lê o último
    resultado; POST roda em background (varre todas as caixas) e persiste."""
    init_db()
    if request.method == 'GET':
        try:
            with get_db() as conn:
                row = conn.execute("SELECT valor, atualizado_em FROM ms_sync_state WHERE chave='lab_auditoria'").fetchone()
            if row:
                d = row_to_dict(row); out = json.loads(d.get('valor') or '{}'); out['quando'] = d.get('atualizado_em')
                return jsonify(out)
            return jsonify({'status': 'nunca rodou — faça POST p/ gerar'})
        except Exception as e:
            return jsonify({'erro': str(e)}), 200

    import threading
    from flask import current_app
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            import json as _json
            try:
                from .lab_inbox import (_mailboxes, _sistema_lookup, _norm, _codigo_do_anexo_ra,
                                        _extrair_texto_anexo, _codigos_no_texto, _classificar, LAB_DOM, _kv_set)
                from .graph import graph_get
                look = _sistema_lookup()
                with get_db() as conn:
                    rows = conn.execute("SELECT id, data_resultado, data_envio_lab FROM amostradores WHERE COALESCE(arquivado,0)=0").fetchall()
                datas = {row_to_dict(r)['id']: row_to_dict(r) for r in rows}
                boxes = _mailboxes()
                ra_emails = ra_pdfs = 0; ra_cods = set(); ra_inv = set(); ra_fora = set(); ra_dated = set()
                cad_emails = 0; cad_sem_cod = []; cad_cods = set(); cad_inv = set(); cad_dated = set()
                for box in boxes:
                    try:
                        inbox = graph_get(f"/users/{box}/mailFolders/inbox/messages?$top=100&$orderby=receivedDateTime desc&$select=id,subject,from,hasAttachments").get('value', [])
                    except Exception:
                        inbox = []
                    for m in inbox:
                        frm = (((m.get('from') or {}).get('emailAddress') or {}).get('address') or '').lower()
                        if _classificar(frm, m.get('subject', '')) != 'resultado' or not m.get('hasAttachments'):
                            continue
                        ra_emails += 1
                        try:
                            metas = graph_get(f"/users/{box}/messages/{m['id']}/attachments?$select=name").get('value', [])
                        except Exception:
                            metas = []
                        for meta in metas:
                            nome = meta.get('name', '')
                            if not nome.lower().endswith('.pdf'):
                                continue
                            ra_pdfs += 1
                            cod = _norm(_codigo_do_anexo_ra(nome))
                            if not cod:
                                continue
                            ra_cods.add(cod)
                            a = look.get(cod)
                            if a:
                                ra_inv.add(cod)
                                if (datas.get(a['id']) or {}).get('data_resultado'):
                                    ra_dated.add(cod)
                            else:
                                ra_fora.add(cod)
                    try:
                        sent = graph_get(f"/users/{box}/mailFolders/sentitems/messages?$top=100&$orderby=sentDateTime desc&$select=id,subject,toRecipients,sentDateTime,hasAttachments,body").get('value', [])
                    except Exception:
                        sent = []
                    for m in sent:
                        tos = [(((t or {}).get('emailAddress') or {}).get('address') or '').lower() for t in (m.get('toRecipients') or [])]
                        if not any(LAB_DOM in t for t in tos):
                            continue
                        cad_emails += 1
                        body = (m.get('body') or {}).get('content', '')
                        cods = _codigos_no_texto(((m.get('subject', '') or '') + ' ' + body), look)
                        if not cods and m.get('hasAttachments'):
                            try:
                                metas = graph_get(f"/users/{box}/messages/{m['id']}/attachments?$select=id,name,contentType,size").get('value', [])
                            except Exception:
                                metas = []
                            for meta in metas:
                                nome = meta.get('name', ''); ct = (meta.get('contentType') or ''); low = nome.lower()
                                if not (low.endswith(('.pdf', '.xlsx', '.xlsm')) or 'pdf' in ct or 'spreadsheet' in ct):
                                    continue
                                if (meta.get('size', 0) or 0) > 8 * 1024 * 1024:
                                    continue
                                try:
                                    full = graph_get(f"/users/{box}/messages/{m['id']}/attachments/{meta['id']}")
                                    cods += _codigos_no_texto(_extrair_texto_anexo(nome, ct, full.get('contentBytes')), look)
                                except Exception:
                                    continue
                        cods = list(dict.fromkeys(cods))
                        if not cods:
                            cad_sem_cod.append({'data': (m.get('sentDateTime') or '')[:10], 'assunto': (m.get('subject') or '')[:60]})
                        for c in cods:
                            cad_cods.add(c); a = look.get(c)
                            if a:
                                cad_inv.add(c)
                                if (datas.get(a['id']) or {}).get('data_envio_lab'):
                                    cad_dated.add(c)
                out = {
                    'resultados': {'ra_emails': ra_emails, 'ra_pdfs': ra_pdfs, 'codigos_distintos': len(ra_cods),
                                   'no_inventario': len(ra_inv), 'datados': len(ra_dated),
                                   'gap_inv_sem_data': sorted(ra_inv - ra_dated)[:60], 'fora_inventario': sorted(ra_fora)[:60]},
                    'cadeias': {'emails_enviados': cad_emails, 'codigos_detectados': len(cad_cods),
                                'no_inventario': len(cad_inv), 'datados': len(cad_dated),
                                'gap_inv_sem_data': sorted(cad_inv - cad_dated)[:60],
                                'fora_inventario': sorted(cad_cods - cad_inv)[:60], 'emails_sem_codigo': cad_sem_cod[:30]},
                    'caixas': boxes,
                }
                with get_db() as conn:
                    _kv_set(conn, 'lab_auditoria', _json.dumps(out, ensure_ascii=False))
            except Exception as ex:
                import traceback, logging
                logging.getLogger(__name__).error('[auditoria_lab] %s', ex)
                try:
                    from .lab_inbox import _kv_set
                    with get_db() as conn:
                        _kv_set(conn, 'lab_auditoria', _json.dumps({'erro': traceback.format_exc()[:1200]}))
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'async': True, 'mensagem': 'Auditoria rodando em background — GET /controle/graph/auditoria_lab em ~1-2 min.'})


@controle_bp.route('/graph/debug_sharepoint')
def graph_debug_sharepoint():
    """DEBUG: descobre sites/drives do SharePoint p/ guardar os certificados.
    ?q=nome busca sites; ?site_id=X lista os drives (bibliotecas) do site."""
    init_db()
    from .graph import graph_get
    out = {}
    try:
        try:
            r = graph_get('/sites/root')
            out['root'] = {'id': r.get('id'), 'nome': r.get('displayName'), 'url': r.get('webUrl')}
        except Exception as e:
            out['root'] = {'erro': str(e)[:200]}
        q = request.args.get('q', '')
        if q:
            try:
                sr = graph_get(f"/sites?search={q}")
                out['busca'] = [{'id': s.get('id'), 'nome': s.get('displayName'), 'url': s.get('webUrl')}
                                for s in sr.get('value', [])[:20]]
            except Exception as e:
                out['busca'] = {'erro': str(e)[:200]}
        sid = request.args.get('site_id', '')
        if sid:
            try:
                dr = graph_get(f"/sites/{sid}/drives")
                out['drives'] = [{'id': d.get('id'), 'nome': d.get('name'), 'tipo': d.get('driveType'), 'url': d.get('webUrl')}
                                 for d in dr.get('value', [])[:20]]
            except Exception as e:
                out['drives'] = {'erro': str(e)[:200]}
        return jsonify(out)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 200


@controle_bp.route('/graph/users')
def graph_list_users():
    """Lista usuários Microsoft da organização."""
    try:
        from .graph import list_org_users
        users = list_org_users()
        return jsonify([{
            'id':    u['id'],
            'nome':  u.get('displayName', ''),
            'email': u.get('mail') or u.get('userPrincipalName', ''),
            'cargo': u.get('jobTitle', ''),
            'dept':  u.get('department', ''),
        } for u in users])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ── Matching empresa ──────────────────────────────────────────────────

@controle_bp.route('/empresas/pendentes')
def api_empresas_pendentes():
    """Lista empresas criadas automaticamente pelo Planner que precisam de validação."""
    init_db()
    with get_db() as conn:
        rows = conn.execute('''
            SELECT e.id, e.nome, e.cnpj, e.criado_em,
                   COUNT(d.id) AS total_demandas
            FROM empresas e
            LEFT JOIN demandas d ON d.empresa_id = e.id
            WHERE e.pendente = 1
            GROUP BY e.id
            ORDER BY total_demandas DESC, e.criado_em DESC
        ''').fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@controle_bp.route('/empresas/pendentes/page')
def page_empresas_pendentes():
    from flask import render_template as rt
    return rt('empresas_pendentes.html')


@controle_bp.route('/ajuda')
def page_ajuda():
    from flask import render_template as rt
    return rt('ajuda.html')


@controle_bp.route('/empresas/pendentes/<int:pend_id>/vincular', methods=['POST'])
def api_vincular_empresa_pendente(pend_id):
    """
    Vincula empresa pendente a uma empresa existente (ou a confirma como nova).
    Body: {"empresa_id": 123}  → usa empresa existente
    Body: {"confirmar": true}  → confirma pendente como empresa real (remove flag)
    Body: {"excluir": true}    → remove empresa pendente sem demandas
    """
    init_db()
    d = request.json or {}
    with get_db() as conn:
        if d.get('excluir'):
            uso = _empresa_uso(conn, pend_id)
            if uso['total'] > 0:
                return jsonify({'erro': f"Empresa tem {uso['total']} registro(s) vinculado(s) — "
                                        "vincule a uma empresa existente em vez de excluir"}), 409
            conn.execute('DELETE FROM empresas WHERE id=? AND pendente=1', (pend_id,))
            return jsonify({'ok': True, 'acao': 'excluida'})
        elif 'empresa_id' in d:
            try:
                destino = int(d['empresa_id'])
            except (ValueError, TypeError):
                return jsonify({'erro': 'empresa_id inválido'}), 400
            if destino == pend_id:
                return jsonify({'erro': 'destino é a própria empresa pendente'}), 400
            alvo = conn.execute('SELECT id FROM empresas WHERE id=?', (destino,)).fetchone()
            if not alvo:
                return jsonify({'erro': f'empresa destino {destino} não existe'}), 404
            # Remapear TODOS os vínculos da pendente para a real (não só demandas —
            # amostradores, coletas, visitas, planejamentos e contatos também)
            remapeados = {}
            for t in _EMPRESA_TABELAS_USO:
                try:
                    cur = conn.execute(f'UPDATE {t} SET empresa_id=? WHERE empresa_id=?',
                                       (destino, pend_id))
                    remapeados[t] = getattr(cur, 'rowcount', 0)
                except Exception:
                    remapeados[t] = 0
            conn.execute('DELETE FROM empresas WHERE id=?', (pend_id,))
            return jsonify({'ok': True, 'acao': 'remapeada', 'empresa_id': destino,
                            'remapeados': remapeados})
        elif d.get('confirmar'):
            conn.execute('UPDATE empresas SET pendente=0 WHERE id=?', (pend_id,))
            return jsonify({'ok': True, 'acao': 'confirmada'})
        else:
            return jsonify({'erro': 'Informe empresa_id, confirmar ou excluir'}), 400


# ── Limpeza de empresas "fantasma" ────────────────────────────────────
_EMPRESA_TABELAS_USO = [
    'demandas', 'amostradores', 'coletas_ruido', 'coletas_quimico',
    'coletas_outros', 'visitas_tecnicas', 'planejamentos', 'contatos_empresa',
]


def _empresa_uso(conn, eid):
    """Conta quantos registros estão vinculados a uma empresa em todas as
    tabelas com empresa_id. Usado para nunca excluir empresa com histórico."""
    out, total = {}, 0
    for t in _EMPRESA_TABELAS_USO:
        try:
            r = conn.execute(f'SELECT COUNT(*) AS n FROM {t} WHERE empresa_id=?', (eid,)).fetchone()
            n = int(row_to_dict(r).get('n', 0) or 0)
        except Exception:
            n = 0
        out[t] = n
        total += n
    out['total'] = total
    return out


@controle_bp.route('/empresas/suspeitas')
def api_empresas_suspeitas():
    """Lista empresas provavelmente-fantasma (nome vazio / só números / AET-*)
    QUE NÃO TÊM nenhum registro vinculado. Read-only — só lista, não apaga."""
    init_db()
    suspeitas = []
    with get_db() as conn:
        rows = conn.execute('SELECT id, nome, cnpj FROM empresas').fetchall()
        for r in rows:
            e = row_to_dict(r)
            nome = (e.get('nome') or '').strip()
            if not nome:
                motivo = 'nome vazio'
            elif nome.isdigit():
                motivo = 'nome só números'
            elif nome.upper().startswith('AET'):
                motivo = 'AET (teste antigo)'
            else:
                continue
            uso = _empresa_uso(conn, e['id'])
            if uso['total'] == 0:
                e['motivo'] = motivo
                suspeitas.append(e)
    return jsonify(suspeitas)


@controle_bp.route('/empresas/<int:eid>/excluir-fantasma', methods=['POST'])
def api_excluir_empresa_fantasma(eid):
    """Exclui uma empresa SOMENTE se ela não tiver nenhum registro vinculado.
    Trava de segurança: se houver qualquer demanda/coleta/visita, recusa."""
    init_db()
    with get_db() as conn:
        row = conn.execute('SELECT nome FROM empresas WHERE id=?', (eid,)).fetchone()
        if not row:
            return jsonify({'erro': 'Empresa não encontrada'}), 404
        nome = (row_to_dict(row).get('nome') or '').strip()
        uso = _empresa_uso(conn, eid)
        if uso['total'] > 0:
            return jsonify({'erro': f'Empresa tem {uso["total"]} registro(s) vinculado(s) — não excluída.',
                            'uso': uso}), 409
        conn.execute('DELETE FROM empresas WHERE id=?', (eid,))
    registrar_evento('empresa_fantasma_excluida', f'{nome or "(sem nome)"} (#{eid})',
                     eid, 'empresa',
                     current_user.nome if current_user.is_authenticated else 'sistema',
                     request.remote_addr)
    return jsonify({'ok': True, 'excluida': eid})


@controle_bp.route('/demandas/match-empresas', methods=['POST'])
def api_match_empresas():
    """Re-executa matching de empresa em todas as demandas sem vínculo.
    threshold padrão: 0.65 (mais permissivo que o sync — 0.72)
    """
    init_db()
    try:
        from .empresa_match import match_todas_demandas
        threshold = float(request.json.get('threshold', 0.65)) if request.json else 0.65
        with get_db() as conn:
            if not USE_PG:
                conn.execute('PRAGMA foreign_keys = OFF')
            stats = match_todas_demandas(conn, threshold=threshold)
        return jsonify({'ok': True, **stats})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/demandas/reclassificar', methods=['POST'])
def api_reclassificar():
    """Reclassifica TODAS as demandas do Planner (operacional/interna/administrativa)
    e extrai OS do título quando ainda não preenchida."""
    init_db()
    try:
        from .classificador import reclassificar_lote
        with get_db() as conn:
            stats = reclassificar_lote(conn)
        return jsonify({'ok': True, **stats})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500



# ── Fila de Revisão Humana (baixa confiança de extração) ──────────────

@controle_bp.route('/demandas/revisao')
def api_fila_revisao():
    """
    Demandas marcadas como needs_review=1 pelo motor inteligente.
    Retorna lista com score, inconsistências e fontes disponíveis.
    """
    init_db()
    limit = min(int(request.args.get('limit', 100) or 100), 500)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT d.id, d.planner_task_id,
                   COALESCE(d.titulo, d.nome_tarefa) AS titulo,
                   d.numero_os, d.status, d.planner_bucket,
                   d.empresa_id, e.nome AS empresa_nome,
                   d.needs_review, d.extracao_json,
                   d.criado_em, d.atualizado_em
            FROM demandas d
            LEFT JOIN empresas e ON e.id = d.empresa_id
            WHERE d.needs_review = 1
              AND d.origem = 'planner'
            ORDER BY d.atualizado_em DESC
            LIMIT ?
        """, (limit,)).fetchall()
    resultado = []
    for r in rows:
        d = row_to_dict(r)
        if d.get('extracao_json'):
            try:
                import json as _j
                d['extracao'] = _j.loads(d['extracao_json'])
            except Exception:
                d['extracao'] = {}
        resultado.append(d)
    return jsonify({
        'total': len(resultado),
        'demandas': resultado,
    })


@controle_bp.route('/demandas/revisao/stats')
def api_revisao_stats():
    """Resumo rápido: total de demandas needs_review + primeiros itens."""
    init_db()
    with get_db() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) AS c FROM demandas WHERE needs_review=1 AND origem='planner'"
        ).fetchone()
        total = (row_to_dict(total_row).get('c', 0) if total_row else 0) or 0
        rows = conn.execute("""
            SELECT d.id,
                   COALESCE(d.titulo, d.nome_tarefa) AS titulo,
                   d.numero_os, d.empresa_match_score,
                   e.nome AS empresa_nome
            FROM demandas d
            LEFT JOIN empresas e ON e.id = d.empresa_id
            WHERE d.needs_review = 1 AND d.origem = 'planner'
            ORDER BY d.atualizado_em DESC LIMIT 20
        """).fetchall()
    return jsonify({'total': total, 'itens': [row_to_dict(r) for r in rows]})


@controle_bp.route('/demandas/<int:did>/extracao')
def api_detalhe_extracao(did):
    """Detalhe completo da extração inteligente de uma demanda."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            'SELECT id, titulo, numero_os, needs_review, extracao_json FROM demandas WHERE id=?',
            (did,)
        ).fetchone()
    if not row:
        return jsonify({'erro': 'Demanda não encontrada'}), 404
    d = row_to_dict(row)
    if d.get('extracao_json'):
        try:
            import json as _j
            d['extracao'] = _j.loads(d['extracao_json'])
        except Exception:
            d['extracao'] = {}
    return jsonify(d)


@controle_bp.route('/demandas/<int:did>/revisar', methods=['POST'])
def api_revisar_demanda(did):
    """
    Marca demanda como revisada pelo humano.
    Body: { numero_os, empresa_id, observacao }
    Limpa needs_review=0 após confirmação.
    """
    init_db()
    payload = request.json or {}
    updates = ['needs_review=0', 'atualizado_em=CURRENT_TIMESTAMP']
    params  = []
    if payload.get('numero_os'):
        updates.append('numero_os=?')
        params.append(payload['numero_os'])
    if payload.get('empresa_id'):
        updates.append('empresa_id=?')
        params.append(int(payload['empresa_id']))
    if payload.get('observacao'):
        updates.append('observacao=?')
        params.append(payload['observacao'])
    params.append(did)
    with get_db() as conn:
        conn.execute(
            f'UPDATE demandas SET {", ".join(updates)} WHERE id=?',
            params
        )
        usuario = ''
        try:
            from flask_login import current_user
            usuario = getattr(current_user, 'email', '') or ''
        except Exception:
            pass
        registrar_evento('demanda_revisada_humano',
                         f'Demanda {did} revisada manualmente',
                         ref_id=did, ref_tipo='demanda', usuario=usuario,
                         ip=request.remote_addr)
    return jsonify({'ok': True, 'demanda_id': did})


# Estado de progresso do reprocessamento (processo único → memória compartilhada)
_REEXTRAIR_STATUS = {'running': False, 'total': 0, 'feitas': 0, 'erros': 0}


@controle_bp.route('/demandas/re-extrair/status')
def api_re_extrair_status():
    """Progresso do reprocessamento de agentes (para a barra na UI)."""
    return jsonify(_REEXTRAIR_STATUS)


@controle_bp.route('/demandas/re-extrair', methods=['POST'])
def api_re_extrair_demandas():
    """
    Re-executa o motor inteligente em todas as demandas do Planner.
    Útil após atualizar o dicionário operacional.
    Processa em background (retorna imediatamente); progresso em /re-extrair/status.
    """
    init_db()
    import threading as _thr
    if _REEXTRAIR_STATUS.get('running'):
        return jsonify({'ok': True, 'info': 'Já em andamento', 'status': dict(_REEXTRAIR_STATUS)})
    _REEXTRAIR_STATUS.update({'running': True, 'total': 0, 'feitas': 0, 'erros': 0})
    def _job():
        try:
            from .inteligencia_demandas import (
                analisar_tarefa_planner, extrair_os_multifonte,
                extrair_agentes_multifonte,
            )
            import json as _j
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, titulo, numero_os, descricao, checklist, "
                    "planner_task_id, planner_bucket, percent_complete "
                    "FROM demandas WHERE origem='planner' ORDER BY id"
                ).fetchall()
            _REEXTRAIR_STATUS['total'] = len(rows)
            processadas = 0
            for r in rows:
                try:
                    d = row_to_dict(r)
                    titulo   = d.get('titulo') or ''
                    desc     = d.get('descricao') or ''
                    bucket   = d.get('planner_bucket') or ''
                    percent  = int(d.get('percent_complete') or 0)
                    cl_raw   = d.get('checklist') or ''
                    try:
                        checklist = _j.loads(cl_raw) if cl_raw else []
                    except Exception:
                        checklist = []
                    # Usar motor multi-fonte (sem Graph API - dados já no banco)
                    os_num, os_conf, os_fontes = extrair_os_multifonte(
                        titulo=titulo, descricao=desc,
                        checklist_texto=' | '.join(
                            (i.get('titulo','') if isinstance(i, dict) else str(i))
                            for i in (checklist if isinstance(checklist, list) else [])
                        ),
                    )
                    agentes = extrair_agentes_multifonte(
                        titulo=titulo, descricao=desc,
                        checklist=checklist, bucket=bucket,
                    )
                    from .inteligencia_demandas import ExtractionResult, validar_resultado
                    result = ExtractionResult(
                        task_id=d.get('planner_task_id', ''),
                        titulo=titulo,
                        numero_os=os_num or d.get('numero_os'),
                        numero_os_confianca=os_conf,
                        numero_os_fontes=os_fontes,
                        agentes=agentes,
                        fontes_lidas=['banco'],
                    )
                    result = validar_resultado(result, bucket, percent)
                    result.calcular_score()
                    with get_db() as conn2:
                        conn2.execute(
                            'UPDATE demandas SET needs_review=?, extracao_json=? WHERE id=?',
                            (1 if result.needs_review else 0,
                             _j.dumps(result.to_dict(), ensure_ascii=False),
                             d['id'])
                        )
                    processadas += 1
                    _REEXTRAIR_STATUS['feitas'] = processadas
                except Exception as e:
                    _REEXTRAIR_STATUS['erros'] = _REEXTRAIR_STATUS.get('erros', 0) + 1
                    import logging as _log
                    _log.getLogger(__name__).warning('re-extrair demanda %s: %s', d.get('id'), e)
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).error('re-extrair job error: %s', e)
        finally:
            _REEXTRAIR_STATUS['running'] = False
    t = _thr.Thread(target=_job, daemon=True)
    t.start()
    return jsonify({'ok': True, 'info': 'Re-extração iniciada em background', 'status': dict(_REEXTRAIR_STATUS)})


@controle_bp.route('/empresas/mesclar', methods=['POST'])
def api_mesclar_empresas():
    """Mescla empresas duplicadas (mesmo nome normalizado)."""
    init_db()
    try:
        n = mesclar_empresas_duplicatas()
        return jsonify({'ok': True, 'mescladas': n})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/admin/saude')
def admin_saude():
    """Diagnóstico completo do banco e integrações."""
    init_db()
    try:
        from .monitoring import diagnostico_banco
        return jsonify(diagnostico_banco())
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/admin/log_evento', methods=['POST'])
def admin_log_evento():
    """Registra evento de analytics (PostHog server-side)."""
    d = request.json or {}
    try:
        from .monitoring import track_evento
        track_evento(
            d.get('evento', 'acao_usuario'),
            usuario=d.get('usuario', 'web'),
            **{k: v for k, v in d.items() if k not in ('evento', 'usuario')}
        )
    except Exception:
        pass
    return jsonify({'ok': True})


@controle_bp.route('/eventos', methods=['POST'])
def api_registrar_evento():
    """Registra evento manualmente (ações do usuário)."""
    init_db()
    d = request.json or {}
    with get_db() as conn:
        conn.execute(
            'INSERT INTO eventos (tipo,descricao,ref_id,ref_tipo,usuario,criado_em) '
            'VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)',
            (d.get('tipo', 'acao'), d.get('descricao', ''),
             d.get('ref_id'), d.get('ref_tipo'), d.get('usuario', 'sistema'))
        )
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════
# ── PLANEJAMENTO DE MEDIÇÃO ───────────────────────────────────────────
# Etapa pré-visita. Dados vêm do Planner/OS + confirmação do técnico.
# ══════════════════════════════════════════════════════════════════════

# Família química nos agentes previstos: a extração antiga gravava
# 'particulado' (poeira) — conta bomba igual a 'quimico'.
_QUIM_FAM_PLANO = ('quimico', 'particulado', 'poeira', 'metais', 'gases')


@controle_bp.route('/planejamentos', methods=['GET'])
def api_list_planejamentos():
    """Lista planejamentos com filtros: tecnico, status, empresa_id, demanda_id."""
    init_db()
    f = {k: v for k, v in request.args.items()}
    return jsonify(list_planejamentos(f))


@controle_bp.route('/planejamentos', methods=['POST'])
def api_criar_planejamento():
    """
    Cria planejamento de medição.
    Body: {demanda_id, empresa_id, numero_os, tecnico, data_prevista,
           agentes_previstos:[{tipo,agente,qtd}], observacao}
    Sistema calcula automaticamente qtd_dosim_prevista e qtd_bombas_previstas.
    """
    init_db()
    d = request.json or {}
    if not d.get('empresa_id') or not d.get('tecnico'):
        return jsonify({'erro': 'empresa_id e tecnico são obrigatórios'}), 400

    # Cálculo automático de equipamentos pelos agentes previstos
    agentes = d.get('agentes_previstos') or []
    if isinstance(agentes, str):
        import json as _j; agentes = _j.loads(agentes)

    qtd_dosim  = sum(int(a.get('qtd', 1)) for a in agentes if a.get('tipo') == 'ruido')
    qtd_bombas = sum(int(a.get('qtd', 1)) for a in agentes
                     if a.get('tipo') in _QUIM_FAM_PLANO)

    d['qtd_dosim_prevista']   = qtd_dosim
    d['qtd_bombas_previstas'] = qtd_bombas

    # Equipamentos sugeridos automaticamente
    equip = []
    if qtd_dosim > 0:
        equip.append({'tipo': 'dosimetro', 'qtd': qtd_dosim, 'obs': 'NHO-01'})
        equip.append({'tipo': 'calibrador', 'qtd': 1, 'obs': 'Calibrador de campo'})
    if qtd_bombas > 0:
        equip.append({'tipo': 'bomba', 'qtd': qtd_bombas, 'obs': 'Bomba de amostragem'})
        equip.append({'tipo': 'calibrador_bomba', 'qtd': 1, 'obs': 'Rotâmetro/calibrador de vazão'})
    has_calor   = any(a.get('tipo') == 'calor' for a in agentes)
    has_vibracao= any(a.get('tipo') in ('vibracao', 'vibracao_vci', 'vibracao_vbma') for a in agentes)
    if has_calor:
        equip.append({'tipo': 'termometro_ibutg', 'qtd': 1, 'obs': 'IBUTG NR-15 Anexo 3'})
    if has_vibracao:
        equip.append({'tipo': 'acelerometro', 'qtd': 1, 'obs': 'ISO 2631 / NR-9'})
    d['equipamentos_json'] = equip

    # ── Checklist e divergencias: aceitar do payload ──
    import json as _j
    for _fld in ('checklist_prevista', 'divergencias_json'):
        _v = d.get(_fld)
        if isinstance(_v, (list, dict)):
            d[_fld] = _j.dumps(_v, ensure_ascii=False)

    # ── Verificar conflito de data (aviso, não bloqueia) ──
    _warnings = []
    if d.get('data_prevista') and d.get('status') == 'confirmado':
        try:
            with get_db() as _conn:
                _conflitos = _conn.execute(
                    "SELECT id, tecnico, numero_os FROM planejamentos "
                    "WHERE data_prevista=? AND status='confirmado' AND empresa_id!=?",
                    (d['data_prevista'], d.get('empresa_id', 0))
                ).fetchall()
            if _conflitos:
                _warnings.append(f"⚠️ {len(_conflitos)} outro(s) planejamento(s) confirmado(s) nessa data — verifique disponibilidade de equipamentos.")
        except Exception:
            pass

    # ── Reserva automática de amostradores ao CONFIRMAR (com trava) ──
    # Pré-checagem: amostrador já reservado por outro plano / no lab → 409, não cria.
    if d.get('status') == 'confirmado':
        _pre = _sync_reserva_plano(None, agentes, dry=True)
        if _pre['conflitos']:
            return jsonify({'erro': '🔒 Amostrador indisponível: '
                            + '; '.join(_pre['conflitos'])
                            + '. Troque o amostrador ou salve como rascunho.'}), 409

    try:
        pid = criar_planejamento(d)
        reserva = None
        if d.get('status') == 'confirmado':
            reserva = _sync_reserva_plano(pid, agentes)
            if reserva['nao_encontrados']:
                _warnings.append('⚠️ Amostrador(es) não encontrado(s) no estoque (sem reserva): '
                                 + ', '.join(reserva['nao_encontrados']))
            if reserva['conflitos']:
                _warnings.append('🔒 Amostrador levado por outro plano durante a confirmação '
                                 '(sem reserva): ' + '; '.join(reserva['conflitos']))
        registrar_evento('planejamento_criado',
                         f'OS: {d.get("numero_os","—")} | Técnico: {d.get("tecnico","—")} | Status: {d.get("status","rascunho")}',
                         pid, 'planejamento',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
        return jsonify({'ok': True, 'id': pid, 'warnings': _warnings, 'reserva': reserva})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/planejamentos/<int:pid>', methods=['GET'])
def api_get_planejamento(pid):
    init_db()
    p = get_planejamento(pid)
    if not p:
        return jsonify({'erro': 'não encontrado'}), 404
    return jsonify(p)


@controle_bp.route('/planejamentos/<int:pid>', methods=['PUT'])
def api_editar_planejamento(pid):
    """Edita um planejamento existente. Recalcula equipamentos pelos agentes."""
    init_db()
    if not get_planejamento(pid):
        return jsonify({'erro': 'não encontrado'}), 404
    d = request.json or {}

    # Recalcular equipamentos quando agentes vierem no payload
    if 'agentes_previstos' in d:
        agentes = d.get('agentes_previstos') or []
        if isinstance(agentes, str):
            import json as _j; agentes = _j.loads(agentes or '[]')
        qtd_dosim  = sum(int(a.get('qtd', 1)) for a in agentes if a.get('tipo') == 'ruido')
        qtd_bombas = sum(int(a.get('qtd', 1)) for a in agentes
                         if a.get('tipo') in _QUIM_FAM_PLANO)
        d['qtd_dosim_prevista']   = qtd_dosim
        d['qtd_bombas_previstas'] = qtd_bombas
        equip = []
        if qtd_dosim > 0:
            equip.append({'tipo': 'dosimetro', 'qtd': qtd_dosim, 'obs': 'NHO-01'})
            equip.append({'tipo': 'calibrador', 'qtd': 1, 'obs': 'Calibrador de campo'})
        if qtd_bombas > 0:
            equip.append({'tipo': 'bomba', 'qtd': qtd_bombas, 'obs': 'Bomba de amostragem'})
            equip.append({'tipo': 'calibrador_bomba', 'qtd': 1, 'obs': 'Rotâmetro/calibrador de vazão'})
        if any(a.get('tipo') == 'calor' for a in agentes):
            equip.append({'tipo': 'termometro_ibutg', 'qtd': 1, 'obs': 'IBUTG NR-15 Anexo 3'})
        if any(a.get('tipo') in ('vibracao', 'vibracao_vci', 'vibracao_vbma') for a in agentes):
            equip.append({'tipo': 'acelerometro', 'qtd': 1, 'obs': 'ISO 2631 / NR-9'})
        d['equipamentos_json'] = equip

    # ── Reserva automática de amostradores (com trava) ──
    _p_atual = get_planejamento(pid) or {}
    _status_final = d.get('status') or _p_atual.get('status') or ''
    _agentes_final = d.get('agentes_previstos', _p_atual.get('agentes_previstos'))
    if _status_final == 'confirmado':
        _pre = _sync_reserva_plano(pid, _agentes_final, dry=True)
        if _pre['conflitos']:
            return jsonify({'erro': '🔒 Amostrador indisponível: '
                            + '; '.join(_pre['conflitos'])
                            + '. Troque o amostrador ou salve como rascunho.'}), 409

    try:
        atualizar_planejamento(pid, d)
        reserva = None
        if _status_final == 'confirmado':
            reserva = _sync_reserva_plano(pid, _agentes_final)
        elif _status_final in ('rascunho', 'cancelado'):
            _liberar_reservas_plano(pid)
        registrar_evento('planejamento_editado',
                         f'OS: {d.get("numero_os","—")} | Status: {d.get("status","—")}',
                         pid, 'planejamento',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
        return jsonify({'ok': True, 'id': pid, 'reserva': reserva})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/planejamentos/<int:pid>', methods=['DELETE'])
def api_excluir_planejamento(pid):
    """Exclui um planejamento. Amostradores ainda reservados voltam ao estoque.
    Bloqueia se já houver planilha de campo / visita vinculada (histórico)."""
    init_db()
    p = get_planejamento(pid)
    if not p:
        return jsonify({'erro': 'não encontrado'}), 404
    try:
        with get_db() as conn:
            vinculos = []
            for tbl, label in (('coletas_ruido', 'planilha de ruído'),
                               ('coletas_quimico', 'planilha química'),
                               ('coletas_outros', 'planilha de campo'),
                               ('visitas_tecnicas', 'visita técnica'),
                               ('execucao_campo', 'execução de campo')):
                try:
                    n = conn.execute(
                        f'SELECT COUNT(*) AS n FROM {tbl} WHERE planejamento_id=?',
                        (pid,)).fetchone()
                    n = row_to_dict(n).get('n', 0) if n else 0
                except Exception:
                    n = 0
                if n:
                    vinculos.append(f'{n} {label}')
            if vinculos:
                return jsonify({'erro': 'Este planejamento já tem registro de campo vinculado ('
                                + ', '.join(vinculos)
                                + '). Cancele ou conclua em vez de excluir.'}), 409
            _liberar_reservas_plano(pid, conn)
            conn.execute('DELETE FROM planejamentos WHERE id=?', (pid,))
        registrar_evento('planejamento_excluido',
                         f'OS: {p.get("numero_os", "—")} | Técnico: {p.get("tecnico", "—")} | '
                         f'Status: {p.get("status", "—")}',
                         pid, 'planejamento',
                         current_user.nome if current_user.is_authenticated else 'sistema',
                         request.remote_addr)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/planejamentos/<int:pid>/pdf')
def api_planejamento_pdf(pid):
    """Gera PDF do planejamento para impressão de campo."""
    init_db()
    from .db import get_planejamento
    import io as _io
    p = get_planejamento(pid)
    if not p:
        return jsonify({'erro': 'Planejamento não encontrado'}), 404
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        buf = _io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                rightMargin=2*cm, leftMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        story = []
        # Título
        story.append(Paragraph(f'<b>Planejamento de Medição #{p["id"]}</b>', styles['Title']))
        story.append(Spacer(1, 0.3*cm))
        # Dados principais
        info = [
            ['Empresa:', p.get('empresa_nome') or '—'],
            ['OS:', p.get('numero_os') or '—'],
            ['Técnico:', p.get('tecnico') or '—'],
            ['Data prevista:', p.get('data_prevista') or '—'],
            ['Status:', p.get('status') or '—'],
            ['Dosímetros:', str(p.get('qtd_dosim_prevista') or 0)],
            ['Bombas:', str(p.get('qtd_bombas_previstas') or 0)],
        ]
        t = Table(info, colWidths=[4*cm, 12*cm])
        t.setStyle(TableStyle([
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#f5f5f5')]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('PADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.5*cm))
        # Agentes previstos
        import json as _j
        agentes = p.get('agentes_previstos') or []
        if isinstance(agentes, str):
            try: agentes = _j.loads(agentes)
            except: agentes = []
        if agentes:
            story.append(Paragraph('<b>Agentes Previstos</b>', styles['Heading2']))
            ag_data = [['Tipo', 'Agente', 'Qtd']]
            for a in agentes:
                ag_data.append([a.get('tipo',''), a.get('agente',''), str(a.get('qtd',1))])
            ta = Table(ag_data, colWidths=[4*cm, 10*cm, 2*cm])
            ta.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B6B6B')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('PADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(ta)
            story.append(Spacer(1, 0.5*cm))
        # Equipamentos
        equips = p.get('equipamentos_json') or []
        if isinstance(equips, str):
            try: equips = _j.loads(equips)
            except: equips = []
        if equips:
            story.append(Paragraph('<b>Equipamentos</b>', styles['Heading2']))
            eq_data = [['Tipo', 'Qtd', 'Obs']]
            for e in equips:
                eq_data.append([e.get('tipo',''), str(e.get('qtd',1)), e.get('obs','')])
            te = Table(eq_data, colWidths=[5*cm, 2*cm, 9*cm])
            te.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B6B6B')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('PADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(te)
        # Observações
        if p.get('observacao'):
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph(f'<b>Observações:</b> {p["observacao"]}', styles['Normal']))
        doc.build(story)
        buf.seek(0)
        from flask import send_file as _sf
        return _sf(buf, as_attachment=True,
                   download_name=f'Planejamento_{pid}_{p.get("empresa_nome","")}.pdf',
                   mimetype='application/pdf')
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/planejamentos/<int:pid>/status', methods=['POST'])
def api_update_planejamento_status(pid):
    """Atualiza status do planejamento: rascunho | confirmado | em_execucao | concluido | cancelado."""
    init_db()
    d = request.json or {}
    status = d.get('status')
    VALIDOS = {'rascunho', 'confirmado', 'em_execucao', 'concluido', 'cancelado'}
    if status not in VALIDOS:
        return jsonify({'erro': f'status inválido. Válidos: {VALIDOS}'}), 400
    reserva = None
    if status == 'confirmado':
        # import local: o bloco em_execucao abaixo também importa get_planejamento
        # localmente, o que tornaria o nome global inacessível aqui (UnboundLocalError)
        from .db import get_planejamento as _get_plan
        _p = _get_plan(pid) or {}
        _pre = _sync_reserva_plano(pid, _p.get('agentes_previstos'), dry=True)
        if _pre['conflitos']:
            return jsonify({'erro': '🔒 Amostrador indisponível: '
                            + '; '.join(_pre['conflitos'])
                            + '. Troque o amostrador no plano antes de confirmar.'}), 409
        reserva = _sync_reserva_plano(pid, _p.get('agentes_previstos'))
    update_planejamento_status(pid, status)
    # Devolução automática: reservas não utilizadas voltam ao estoque
    # (utilizado já virou 'laboratorio' pela baixa da planilha química)
    if status in ('rascunho', 'cancelado', 'concluido'):
        _liberar_reservas_plano(pid)
    # Auto-criar fichas de coleta se status = em_execucao
    if status == 'em_execucao':
        try:
            from .db import get_planejamento, save_coleta_ruido, save_coleta_quimico, save_coleta_outros
            import json as _j
            p = get_planejamento(pid)
            if p:
                agentes = p.get('agentes_previstos') or []
                if isinstance(agentes, str):
                    try: agentes = _j.loads(agentes)
                    except: agentes = []
                for ag in agentes:
                    tipo = ag.get('tipo', '')
                    if tipo == 'ruido':
                        save_coleta_ruido({
                            'empresa_id': p.get('empresa_id'),
                            'empresa_nome': p.get('empresa_nome'),
                            'demanda_id': p.get('demanda_id'),
                            'tecnico': p.get('tecnico'),
                            'data_coleta': p.get('data_prevista') or '',
                            'status': 'planejada',
                            'planejamento_id': pid,
                        })
                    elif tipo == 'quimico':
                        save_coleta_quimico({
                            'empresa_id': p.get('empresa_id'),
                            'empresa_nome': p.get('empresa_nome'),
                            'demanda_id': p.get('demanda_id'),
                            'responsavel_coleta': p.get('tecnico'),
                            'data_coleta': p.get('data_prevista') or '',
                            'status': 'planejada',
                            'planejamento_id': pid,
                        })
                    elif tipo in ('calor', 'vibracao', 'vibracao_vci', 'vibracao_vbma'):
                        save_coleta_outros({
                            'tipo': tipo,
                            'empresa_id': p.get('empresa_id'),
                            'empresa_nome': p.get('empresa_nome'),
                            'demanda_id': p.get('demanda_id'),
                            'avaliador': p.get('tecnico'),
                            'data_coleta': p.get('data_prevista') or '',
                            'status': 'planejada',
                            'planejamento_id': pid,
                        })
        except Exception as _ce:
            import traceback; traceback.print_exc()
            # Não impede atualização de status
    return jsonify({'ok': True})


# ── VISITA TÉCNICA ────────────────────────────────────────────────────

@controle_bp.route('/visitas', methods=['GET'])
def api_list_visitas():
    """Lista visitas com filtros: tecnico, demanda_id, planejamento_id."""
    init_db()
    f = {k: v for k, v in request.args.items()}
    return jsonify(list_visitas(f))


@controle_bp.route('/visitas', methods=['POST'])
def api_criar_visita():
    """
    Cria visita técnica (abertura da visita — técnico chegou na empresa).
    Body: {planejamento_id, demanda_id, empresa_id, tecnico, data_visita,
           hora_inicio, tipo_visita}
    """
    init_db()
    d = request.json or {}
    if not d.get('tecnico') or not d.get('data_visita'):
        return jsonify({'erro': 'tecnico e data_visita são obrigatórios'}), 400
    try:
        vid = criar_visita(d)
        return jsonify({'ok': True, 'id': vid})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/visitas/<int:vid>', methods=['GET'])
def api_get_visita(vid):
    init_db()
    v = get_visita(vid)
    if not v:
        return jsonify({'erro': 'não encontrado'}), 404
    return jsonify(v)


@controle_bp.route('/relatorio_visita/<int:vid>')
def relatorio_visita_html(vid):
    """Gera relatório HTML de visita técnica — otimizado para mobile."""
    init_db()
    with get_db() as conn:
        row = conn.execute('''
            SELECT vt.*,
                   e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
                   e.cidade AS empresa_cidade, e.endereco AS empresa_endereco,
                   d.numero_os, d.agentes AS demanda_agentes
            FROM visitas_tecnicas vt
            LEFT JOIN empresas e ON e.id = vt.empresa_id
            LEFT JOIN demandas d ON d.id = vt.demanda_id
            WHERE vt.id = ?
        ''', (vid,)).fetchone()
        if not row:
            return '<h3 style="font-family:sans-serif;padding:24px">Visita não encontrada</h3>', 404
        v = row_to_dict(row)
        exec_row = conn.execute(
            'SELECT * FROM execucao_campo WHERE visita_id=? ORDER BY criado_em DESC LIMIT 1', (vid,)
        ).fetchone()
        exec_data = row_to_dict(exec_row) if exec_row else {}
        coletas_r = conn.execute(
            'SELECT tipo, data_coleta, avaliador, status FROM coletas_ruido WHERE demanda_id=? ORDER BY criado_em DESC LIMIT 5',
            (v.get('demanda_id') or 0,)
        ).fetchall()
        coletas_q = conn.execute(
            'SELECT tipo_avaliacao, data_coleta, avaliador, status FROM coletas_quimico WHERE demanda_id=? ORDER BY criado_em DESC LIMIT 5',
            (v.get('demanda_id') or 0,)
        ).fetchall()
        coletas_o = conn.execute(
            'SELECT tipo, data_coleta, avaliador, status FROM coletas_outros WHERE demanda_id=? ORDER BY criado_em DESC LIMIT 5',
            (v.get('demanda_id') or 0,)
        ).fetchall()
        try:
            fotos_rows = conn.execute(
                'SELECT categoria, data, legenda FROM visita_fotos WHERE visita_id=? ORDER BY id', (vid,)
            ).fetchall()
        except Exception:
            fotos_rows = []

    import json as _json
    def _fmt_date(s):
        if not s: return '—'
        try:
            from datetime import datetime
            return datetime.fromisoformat(s[:10]).strftime('%d/%m/%Y')
        except: return s[:10]

    resultado_cores = {'concluido': '#22c55e', 'parcial': '#f59e0b', 'cancelado': '#ef4444', 'pendente': '#6b7280'}
    resultado_cor = resultado_cores.get(v.get('resultado', ''), '#6b7280')

    agentes_exec = exec_data.get('agentes_executados', '')
    agentes_nao  = exec_data.get('agentes_nao_executados', '')
    try: agentes_nao = _json.loads(agentes_nao) if isinstance(agentes_nao, str) and agentes_nao.startswith('[') else agentes_nao
    except: pass

    coletas_rows = ''
    for c in list(coletas_r) + list(coletas_q) + list(coletas_o):
        cols = dict(c) if hasattr(c, 'keys') else {}
        tipo = cols.get('tipo') or cols.get('tipo_avaliacao') or '—'
        coletas_rows += f'<tr><td>{tipo}</td><td>{_fmt_date(cols.get("data_coleta"))}</td><td>{cols.get("avaliador","—")}</td><td><span style="color:{resultado_cores.get(cols.get("status",""),"#6b7280")}">{cols.get("status","—")}</span></td></tr>'

    # ── Fotos (ambiente / atividade / equipamentos) ───────────────────
    _CAT_LABEL = {'ambiente': '🏭 Ambiente', 'atividade': '👷 Atividade', 'equipamentos': '🔧 Equipamentos'}
    fotos_html = ''
    if fotos_rows:
        por_cat = {}
        for fr in fotos_rows:
            fd = dict(fr) if hasattr(fr, 'keys') else {}
            por_cat.setdefault(fd.get('categoria') or 'ambiente', []).append(fd)
        blocos = ''
        for cat, items in por_cat.items():
            imgs = ''.join(
                f'<figure style="margin:0"><img src="{f.get("data","")}" '
                f'style="width:100%;border-radius:8px;border:1px solid #2a2d3e">'
                + (f'<figcaption style="font-size:.72rem;color:#94a3b8;margin-top:3px">{(f.get("legenda") or "")}</figcaption>' if f.get('legenda') else '')
                + '</figure>'
                for f in items)
            blocos += (f'<div style="margin-bottom:12px"><div style="font-size:.78rem;color:#94a3b8;'
                       f'margin-bottom:6px;font-weight:600">{_CAT_LABEL.get(cat, cat)}</div>'
                       f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">{imgs}</div></div>')
        fotos_html = f'<div class="card"><h2>Registro Fotográfico</h2>{blocos}</div>'

    # ── Imprevisto / justificativa ────────────────────────────────────
    _IMPREV_LABEL = {'culpa_empresa': 'Responsabilidade da empresa', 'culpa_equipe': 'Responsabilidade da equipe',
                     'clima': 'Condições climáticas', 'ausencia_trabalhador': 'Ausência de trabalhador',
                     'indisponibilidade': 'Indisponibilidade operacional', 'outros': 'Outros'}
    imprev_html = ''
    if v.get('imprevisto_tipo') or v.get('justificativa'):
        imprev_html = ('<div class="card"><h2>Impedimentos / Justificativa</h2>'
                       + (f'<div class="row"><span class="lbl">Imprevisto</span><span class="val" style="color:#f59e0b">{_IMPREV_LABEL.get(v.get("imprevisto_tipo",""), v.get("imprevisto_tipo") or "—")}</span></div>' if v.get('imprevisto_tipo') else '')
                       + (f'<p style="color:#cbd5e1;line-height:1.6;margin:6px 0 0">{v.get("justificativa")}</p>' if v.get('justificativa') else '')
                       + '</div>')

    # ── Assinaturas + termo de ciência ────────────────────────────────
    sig_html = ''
    sig_tec = v.get('assinatura') or ''
    sig_emp = v.get('assinatura_empresa') or ''
    if sig_tec or sig_emp or v.get('sem_assinatura_motivo'):
        partes = ''
        if sig_tec.startswith('data:image/'):
            partes += (f'<div style="margin-bottom:14px"><div class="lbl" style="margin-bottom:4px">Técnico (Ocupacional)</div>'
                       f'<img src="{sig_tec}" style="width:100%;max-width:340px;background:#fff;border-radius:8px">'
                       f'<div class="val" style="margin-top:4px">{v.get("tecnico","")}</div></div>')
        if sig_emp.startswith('data:image/'):
            ass_quando = _fmt_date(v.get('assinado_em')) if v.get('assinado_em') else _fmt_date(v.get('data_visita'))
            partes += (f'<div><div class="lbl" style="margin-bottom:4px">Responsável da empresa</div>'
                       f'<img src="{sig_emp}" style="width:100%;max-width:340px;background:#fff;border-radius:8px">'
                       f'<div class="val" style="margin-top:4px">{v.get("assinante_nome") or v.get("acompanhante") or "—"}'
                       + (f' · {v.get("assinante_cargo")}' if v.get('assinante_cargo') else '')
                       + f' · {ass_quando}</div>'
                       + (f'<p style="font-size:.72rem;color:#94a3b8;line-height:1.5;margin:8px 0 0;font-style:italic">“{v.get("ciencia_texto")}”</p>' if v.get('ciencia_texto') else '')
                       + '</div>')
        elif v.get('sem_assinatura_motivo'):
            partes += (f'<div><div class="lbl" style="margin-bottom:4px">Responsável da empresa</div>'
                       f'<div class="val" style="color:#f59e0b">Sem assinatura — {v.get("sem_assinatura_motivo")}</div></div>')
        sig_html = f'<div class="card"><h2>Assinaturas</h2>{partes}</div>'

    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Relatório de Visita #{vid}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;margin:0;padding:16px;font-size:14px}}
  .card{{background:#1a1d2e;border:1px solid #2a2d3e;border-radius:12px;padding:16px;margin-bottom:14px}}
  h1{{font-size:1.15rem;margin:0 0 4px;color:#fff}}
  h2{{font-size:.9rem;color:#94a3b8;margin:0 0 12px;font-weight:500}}
  .badge{{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.78rem;font-weight:600;color:#fff;background:{resultado_cor}}}
  .row{{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}}
  .lbl{{color:#64748b;font-size:.78rem;min-width:100px}}
  .val{{color:#e2e8f0;font-size:.84rem}}
  table{{width:100%;border-collapse:collapse;font-size:.82rem}}
  th{{text-align:left;color:#64748b;font-weight:500;padding:6px 0;border-bottom:1px solid #2a2d3e}}
  td{{padding:6px 0;border-bottom:1px solid #1e2130;color:#e2e8f0}}
  .tag{{background:#1e293b;border:1px solid #334155;border-radius:4px;padding:2px 7px;font-size:.75rem;margin:2px;display:inline-block}}
  .logo{{color:#3b82f6;font-size:.75rem;text-align:center;margin-top:20px;opacity:.5}}
</style>
</head>
<body>
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <h1>Visita #{vid}</h1>
      <h2>{v.get("empresa_nome","—")}</h2>
    </div>
    <span class="badge">{(v.get("resultado") or "pendente").upper()}</span>
  </div>
  <div class="row"><span class="lbl">Data</span><span class="val">{_fmt_date(v.get("data_visita"))}</span></div>
  <div class="row"><span class="lbl">Técnico</span><span class="val">{v.get("tecnico","—")}</span></div>
  <div class="row"><span class="lbl">OS</span><span class="val">{v.get("numero_os","—")}</span></div>
  <div class="row"><span class="lbl">Tipo</span><span class="val">{v.get("tipo_visita","—")}</span></div>
  {"<div class='row'><span class='lbl'>Horário</span><span class='val'>" + v.get("hora_inicio","") + " – " + v.get("hora_termino","") + "</span></div>" if v.get("hora_inicio") else ""}
</div>

{"<div class='card'><h2>Execução de Campo</h2><div class='row'><span class='lbl'>Executados</span><span class='val'>" + str(agentes_exec or "—") + "</span></div>" + ("<div class='row'><span class='lbl'>Não feitos</span><span class='val' style='color:#f59e0b'>" + ("; ".join(a.get("agente","?") for a in agentes_nao) if isinstance(agentes_nao, list) else str(agentes_nao)) + "</span></div>" if agentes_nao else "") + ("</div>" ) if exec_data else ""}

{("<div class='card'><h2>Coletas Registradas</h2><table><tr><th>Tipo</th><th>Data</th><th>Avaliador</th><th>Status</th></tr>" + coletas_rows + "</table></div>") if coletas_rows else ""}

{"<div class='card'><h2>Observações</h2><p style='color:#cbd5e1;line-height:1.6'>" + v.get("observacao_geral","—") + "</p></div>" if v.get("observacao_geral") else ""}

{imprev_html}

{fotos_html}

{sig_html}

<div class="logo">Ocupacional Medicina e Segurança do Trabalho · Sistema SST</div>
</body>
</html>'''
    from flask import Response
    return Response(html, mimetype='text/html')


@controle_bp.route('/execucao/nao-executados')
def api_execucao_nao_executados():
    """Lista OS com agentes não executados em campo."""
    init_db()
    import json as _json
    with get_db() as conn:
        rows = conn.execute('''
            SELECT ec.agentes_nao_executados, ec.justificativa_causa,
                   vt.data_visita,
                   e.nome AS empresa_nome,
                   d.numero_os
            FROM execucao_campo ec
            LEFT JOIN visitas_tecnicas vt ON vt.id = ec.visita_id
            LEFT JOIN demandas d ON d.id = vt.demanda_id
            LEFT JOIN empresas e ON e.id = vt.empresa_id
            WHERE ec.agentes_nao_executados IS NOT NULL
              AND ec.agentes_nao_executados != ''
              AND ec.agentes_nao_executados != '[]'
            ORDER BY ec.criado_em DESC LIMIT 100
        ''').fetchall()
    result = []
    for row in rows:
        r = row_to_dict(row)
        nao_raw = r.get('agentes_nao_executados', '')
        try:
            nao = _json.loads(nao_raw) if isinstance(nao_raw, str) and nao_raw.startswith('[') else []
            if isinstance(nao, list):
                nao_str = '; '.join(
                    (a.get('agente') or a.get('nome') or str(a)) for a in nao if isinstance(a, dict)
                ) or nao_raw
            else:
                nao_str = str(nao_raw)
        except Exception:
            nao_str = str(nao_raw)
        result.append({
            'numero_os':    r.get('numero_os') or '—',
            'empresa_nome': r.get('empresa_nome') or '—',
            'data_visita':  r.get('data_visita') or '—',
            'agentes_nao':  nao_str,
            'causa':        r.get('justificativa_causa') or '—',
        })
    return jsonify(result)


@controle_bp.route('/planejamentos/<int:pid>/execucao', methods=['POST'])
def api_registrar_execucao(pid):
    """
    Registra o resultado da execução de campo de um planejamento.
    Chamado automaticamente após nvSalvarMedicao quando há agentes não medidos.
    Body: {resultado, agentes_nao_executados, medicao_id}
    """
    import json as _json
    init_db()
    d = request.json or {}
    resultado            = d.get('resultado', 'parcial')
    agentes_nao          = d.get('agentes_nao_executados', [])
    medicao_id           = d.get('medicao_id')

    try:
        with get_db() as conn:
            # Registrar em execucao_campo (sem visita_id)
            conn.execute('''
                INSERT INTO execucao_campo
                  (planejamento_id, agentes_nao_executados, justificativa_causa, observacao)
                VALUES (?, ?, ?, ?)
            ''', (
                pid,
                _json.dumps(agentes_nao, ensure_ascii=False),
                agentes_nao[0].get('causa', '') if agentes_nao else '',
                '; '.join(f"{a.get('agente','?')} — {a.get('observacao','')}" for a in agentes_nao),
            ))
            # Atualizar status do planejamento
            conn.execute(
                "UPDATE planejamentos SET status=? WHERE id=?",
                (resultado, pid)
            )
        return jsonify({'ok': True, 'resultado': resultado})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/visitas/<int:vid>/concluir', methods=['POST'])
def api_concluir_visita(vid):
    """
    Conclui visita técnica. Cria execucao_campo automaticamente.
    Body: {resultado: concluido|parcial|cancelado,
           justificativa, justificativa_causa, cobravel,
           agentes_executados, agentes_nao_executados, agentes_adicionados,
           hora_termino, observacao}
    Regra: se resultado != 'concluido' → justificativa_causa obrigatória.
    """
    init_db()
    d = request.json or {}
    resultado = d.get('resultado', 'concluido')

    # Justificativa obrigatória para não-conclusão E para agente não executado
    # (Diretriz Mestra: justificativa imediata, não divergência posterior)
    nao_exec = d.get('agentes_nao_executados') or []
    if resultado != 'concluido' or nao_exec:
        if not (d.get('justificativa_causa') or d.get('justificativa')):
            return jsonify({
                'erro': 'justificativa obrigatória: visita não concluída ou há agentes não executados',
                'causas_validas': [
                    'culpa_empresa', 'culpa_equipe', 'clima',
                    'ausencia_trabalhador', 'indisponibilidade', 'outros'
                ]
            }), 400

    try:
        concluir_visita(vid, d)
        return jsonify({'ok': True, 'resultado': resultado})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ── Admin: gerenciamento de usuários ──────────────────────────────────
def _require_admin():
    if not current_user.is_authenticated or current_user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Somente administradores.'}), 403


@controle_bp.route('/admin/usuarios')
@login_required
def admin_usuarios():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    init_db()
    with get_db() as conn:
        rows = conn.execute(
            'SELECT id, nome, email, registro_mte, role, ativo, criado_em FROM usuarios ORDER BY ativo ASC, criado_em DESC'
        ).fetchall()
    usuarios = [row_to_dict(r) for r in rows]
    from .db import list_tecnicos_mte
    tecnicos = list_tecnicos_mte(incluir_inativos=True)
    from flask import render_template
    return render_template('admin_usuarios.html', usuarios=usuarios,
                           tecnicos=tecnicos)


# ── Admin: catálogo de TSTs (nome ↔ registro MTE) ─────────────────────
# Alimenta o select de técnico dos documentos. Admin-only: o técnico comum
# só consome a lista, não a edita.
@controle_bp.route('/admin/tecnicos-mte', methods=['GET', 'POST'])
@login_required
def admin_tecnicos_mte():
    chk = _require_admin()
    if chk: return chk
    from .db import list_tecnicos_mte, salvar_tecnico_mte
    if request.method == 'GET':
        return jsonify(list_tecnicos_mte(incluir_inativos=True))
    d = request.json or {}
    try:
        res = salvar_tecnico_mte(
            nome=d.get('nome'),
            registro=d.get('registro_mte'),
            cargo=d.get('cargo') or 'TST',
            ativo=1 if d.get('ativo', 1) else 0,
            tid=d.get('id'),
        )
    except ValueError as e:
        return jsonify({'erro': str(e)}), 400
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    registrar_evento('admin_tecnico_mte', f"{res['nome']} → {res['registro_mte']}",
                     usuario=current_user.nome, ip=request.remote_addr)
    return jsonify({'ok': True, 'tecnico': res})


@controle_bp.route('/admin/tecnicos-mte/<int:tid>/toggle', methods=['POST'])
@login_required
def admin_tecnico_mte_toggle(tid):
    """Desativa (some do select) ou reativa. Nunca deleta: o seed do deploy
    só insere quem não existe, então apagar faria a pessoa voltar sozinha."""
    chk = _require_admin()
    if chk: return chk
    d = request.json or {}
    ativo = 1 if d.get('ativo') else 0
    with get_db() as conn:
        conn.execute('UPDATE tecnicos_mte SET ativo=?, atualizado_em=? WHERE id=?',
                     (ativo, datetime.now().isoformat(timespec='seconds'), tid))
    registrar_evento('admin_tecnico_mte_toggle', f'id={tid} ativo={ativo}',
                     usuario=current_user.nome, ip=request.remote_addr)
    return jsonify({'ok': True})


@controle_bp.route('/admin/usuarios/<int:uid>/ativar', methods=['POST'])
@login_required
def admin_ativar_usuario(uid):
    chk = _require_admin()
    if chk: return chk
    with get_db() as conn:
        conn.execute('UPDATE usuarios SET ativo=1 WHERE id=?', (uid,))
    registrar_evento('admin_ativar_usuario', f'uid={uid}',
                     usuario=current_user.nome, ip=request.remote_addr)
    return jsonify({'ok': True})


@controle_bp.route('/admin/usuarios/<int:uid>/desativar', methods=['POST'])
@login_required
def admin_desativar_usuario(uid):
    chk = _require_admin()
    if chk: return chk
    with get_db() as conn:
        conn.execute('UPDATE usuarios SET ativo=0 WHERE id=?', (uid,))
    registrar_evento('admin_desativar_usuario', f'uid={uid}',
                     usuario=current_user.nome, ip=request.remote_addr)
    return jsonify({'ok': True})


@controle_bp.route('/admin/usuarios/<int:uid>/role', methods=['POST'])
@login_required
def admin_set_role(uid):
    chk = _require_admin()
    if chk: return chk
    d = request.json or {}
    role = d.get('role', 'tecnico')
    if role not in ('admin', 'tecnico', 'visualizador'):
        return jsonify({'erro': 'Role inválido'}), 400
    with get_db() as conn:
        conn.execute('UPDATE usuarios SET role=? WHERE id=?', (role, uid))
    registrar_evento('admin_set_role', f'uid={uid} role={role}',
                     usuario=current_user.nome, ip=request.remote_addr)
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# CAMADA DE CONSISTÊNCIA OPERACIONAL
# ══════════════════════════════════════════════════════════════════════════════

@controle_bp.route('/consistencia/stats')
@login_required
def consistencia_stats():
    """Resumo de divergências por severidade."""
    init_db()
    try:
        from .consistencia import stats_consistencia
        return jsonify(stats_consistencia())
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/divergencias')
@login_required
def consistencia_listar():
    """Lista divergências com filtros opcionais."""
    init_db()
    status = request.args.get('status', 'aberta')
    limit  = _qint('limit', 100, 500)
    tipo   = request.args.get('tipo')
    sev    = request.args.get('severidade')
    try:
        from .consistencia import listar_divergencias
        rows = listar_divergencias(status=status, limit=limit, tipo=tipo, severidade=sev)
        return jsonify(rows)
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/rodar', methods=['POST'])
@login_required
def consistencia_rodar():
    """Executa todas as verificações de consistência e persiste divergências."""
    init_db()
    try:
        from .consistencia import run_consistencia_geral
        resultado = run_consistencia_geral()
        return jsonify({'ok': True, **resultado})
    except Exception as e:
        import traceback
        traceback.print_exc(); return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/divergencias/<int:div_id>/justificar', methods=['POST'])
@login_required
def consistencia_justificar(div_id):
    """Adiciona justificativa a uma divergência."""
    init_db()
    d = request.json or {}
    motivo    = d.get('motivo', 'outros')
    descricao = d.get('descricao', '')
    tecnico   = current_user.nome if current_user.is_authenticated else 'sistema'
    try:
        from .consistencia import justificar_divergencia
        justificar_divergencia(div_id, motivo, descricao, tecnico)
        registrar_evento('consistencia_justificativa', f'div={div_id} motivo={motivo}',
                         div_id, 'divergencia', tecnico, request.remote_addr)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/divergencias/<int:div_id>/resolver', methods=['POST'])
@login_required
def consistencia_resolver(div_id):
    """Marca divergência como resolvida."""
    init_db()
    tecnico = current_user.nome if current_user.is_authenticated else 'sistema'
    try:
        from .consistencia import resolver_divergencia
        resolver_divergencia(div_id, tecnico)
        registrar_evento('consistencia_resolvida', f'div={div_id}',
                         div_id, 'divergencia', tecnico, request.remote_addr)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/metodos/<path:agente>')
@login_required
def consistencia_metodo(agente):
    """Retorna especificações do método analítico para um agente."""
    try:
        from .consistencia import info_metodo
        return jsonify(info_metodo(agente))
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/validar_vazao', methods=['POST'])
@login_required
def consistencia_validar_vazao():
    """Valida se a vazão informada é compatível com o método analítico."""
    d = request.json or {}
    agente = d.get('agente', '')
    vazao  = d.get('vazao_lpm')
    try:
        from .consistencia import validar_vazao
        return jsonify(validar_vazao(agente, vazao))
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/consistencia/alertas')
@login_required
def consistencia_alertas():
    """Lista alertas operacionais ativos."""
    init_db()
    status = request.args.get('status', 'ativo')
    limit  = _qint('limit', 50, 200)
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM alertas_operacionais WHERE status=? ORDER BY criado_em DESC LIMIT ?',
            (status, limit)
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@controle_bp.route('/consistencia/motivos')
def consistencia_motivos():
    """Lista motivos padronizados de divergência."""
    try:
        from .consistencia import MOTIVOS_DIVERGENCIA
        return jsonify(MOTIVOS_DIVERGENCIA)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ── Cadeia de Custódia — formulário IT02-M do laboratório ──────────────
# Gerada no ato de despachar: os dados já estão nas coletas (o técnico
# redigitava), e o despacho carimba data_envio_lab, que é o que faltava para
# o alerta de atraso enxergar o amostrador.

def _cc_ids(d):
    """IDs de amostrador do payload, só inteiros."""
    out = []
    for x in (d.get('amostrador_ids') or d.get('ids') or []):
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


@controle_bp.route('/amostradores/sincronizar-datas-laudo', methods=['POST'])
@login_required
def api_sincronizar_datas_laudo():
    """Preenche data_medicao vazia com a data de amostragem que o laudo declara.

    Ferramenta de manutenção (admin): o sync já faz isso a cada rodada, este
    endpoint é para não esperar. O ciclo completo (medição → envio → resultado)
    existia em 6 de 487 amostradores porque a data da coleta ficava só em
    `ra_laudos`, extraída do PDF, sem chegar ao amostrador.
    """
    init_db()
    from .lab_inbox import sincronizar_data_medicao_dos_laudos, normalizar_datas_vazias
    try:
        propagadas = sincronizar_data_medicao_dos_laudos()
        normalizadas = normalizar_datas_vazias()
    except Exception as e:
        log.exception('[datas-laudo] falhou')
        return jsonify({'erro': str(e)}), 500
    usuario = getattr(current_user, 'email', 'sistema')
    registrar_evento('amostrador_atualizado',
                     f'Datas do laudo → amostrador: {propagadas} data(s) de medição '
                     f'preenchida(s), {normalizadas} campo(s) vazio(s) normalizado(s)',
                     usuario=usuario, ip=request.remote_addr)
    return jsonify({'ok': True, 'data_medicao_preenchida': propagadas,
                    'campos_vazios_normalizados': normalizadas})


@controle_bp.route('/cadeia-custodia/medicoes')
@login_required
def cadeia_custodia_medicoes():
    """Medições químicas realizadas, cada uma conferida contra a guia de métodos.

    É a aba "Gerar Cadeia de Custódia": mostra o que foi coletado e se vazão,
    volume e tipo de amostrador batem com o método ANTES de a amostra sair —
    depois que o tubo vai ao laboratório não tem volta.
    """
    from .validacao_metodo import (validar_coleta, escolher_metodo, chave_metodo,
                                   escolha_incerta, SEM_METODO)
    init_db()
    # `agentes` = {'TCP2912AV3': 'Tolueno', ...} — escolha que o técnico está
    # fazendo na tela, para conferir ANTES de gravar. Sem ela usa o que está na
    # coleta, que hoje está vazio em 100% dos registros (o agente nunca chegava
    # ao banco: nascia direto na planilha).
    escolhidos = {}
    _raw = request.args.get('agentes')
    if _raw:
        try:
            escolhidos = {str(k).strip().upper(): v
                          for k, v in json.loads(_raw).items()}
        except Exception:
            escolhidos = {}
    # `metodos` = {'FVPH': 'OSHA 1019|SKC 225-9030 (FVPH****)'} — o método que o
    # técnico escolheu na tela. 116 dos 374 agentes da guia têm mais de um jeito
    # de serem medidos (Benzeno tem 4); sem escolha o sistema pré-seleciona pelo
    # amostrador usado, mas quem decide é o técnico.
    metodos_escolhidos = {}
    _rawm = request.args.get('metodos')
    if _rawm:
        try:
            metodos_escolhidos = {str(k).strip().upper(): str(v or '')
                                  for k, v in json.loads(_rawm).items()}
        except Exception:
            metodos_escolhidos = {}
    só_pendentes = (request.args.get('pendentes') or '1') not in ('0', 'false', 'no')
    try:
        limite = min(int(request.args.get('limit') or 300), 1000)
    except ValueError:
        limite = 300
    with get_db() as conn:
        # DISTINCT por amostrador: a coleta pode ter linha duplicada (visto em
        # EC98029A, 2 registros idênticos) e a aba não pode contar 2x.
        rows = [row_to_dict(r) for r in conn.execute(
            """SELECT cqa.id_amostrador, cqa.tipo_amostrador, cqa.substancia,
                      cqa.vazao_media, cqa.volume_l, cqa.tempo_min,
                      cqa.hora_inicio, cqa.hora_final,
                      cq.empresa_nome, cq.nome_funcionario, cq.funcao, cq.setor,
                      cq.responsavel_coleta, cq.data_coleta, cq.demanda_id,
                      a.id AS amostrador_id, a.status, a.data_envio_lab
               FROM coletas_quimico_amostr cqa
               JOIN coletas_quimico cq ON cq.id = cqa.coleta_id
               LEFT JOIN amostradores a
                      ON UPPER(TRIM(a.codigo)) = UPPER(TRIM(cqa.id_amostrador))
               ORDER BY cq.data_coleta DESC, cqa.id_amostrador""").fetchall()]

    vistos, itens = set(), []
    for r in rows:
        cod = str(r.get('id_amostrador') or '').strip()
        if not cod or cod.upper() in vistos:
            continue
        vistos.add(cod.upper())
        # Já despachado ao laboratório → fora da fila de conferência
        if só_pendentes and str(r.get('data_envio_lab') or '').strip():
            continue
        agente = (escolhidos.get(cod.upper()) or r.get('substancia') or '').strip()
        metodos = _buscar_metodos_agente(agente) if agente else []
        m = escolher_metodo(metodos, metodos_escolhidos.get(cod.upper()),
                            r.get('tipo_amostrador'))
        v = validar_coleta(m, vazao=r.get('vazao_media'), volume=r.get('volume_l'),
                           tipo_amostrador=r.get('tipo_amostrador'),
                           tempo_min=r.get('tempo_min'),
                           hora_inicio=r.get('hora_inicio'),
                           hora_final=r.get('hora_final'))
        if not agente:
            # Sem agente não há método a conferir — a cadeia cobra a escolha
            # depois; aqui só sinaliza para não parecer aprovado.
            v['veredicto'] = SEM_METODO
            v['problemas'] = ['agente da amostra não informado na coleta']
        itens.append({
            'amostrador_id': r.get('amostrador_id'), 'codigo': cod,
            'tipo': r.get('tipo_amostrador') or '', 'agente': agente,
            'empresa': r.get('empresa_nome') or '', 'demanda_id': r.get('demanda_id'),
            'funcionario': r.get('nome_funcionario') or '', 'funcao': r.get('funcao') or '',
            'setor': r.get('setor') or '', 'tecnico': r.get('responsavel_coleta') or '',
            'data': str(r.get('data_coleta') or '')[:10],
            'status': r.get('status') or '', 'ja_enviado': bool(str(r.get('data_envio_lab') or '').strip()),
            'vazao': r.get('vazao_media'), 'hora_inicio': r.get('hora_inicio') or '',
            'hora_final': r.get('hora_final') or '',
            'metodo': v.get('metodo_cod'), 'volume': v.get('volume_calculado'),
            'duracao_min': v.get('duracao_min'),
            'veredicto': v['veredicto'], 'problemas': v['problemas'],
            'avisos': v.get('avisos') or [],
            'itens': v.get('itens') or [],
            # Regime conferido (TWA/STEL/vapores) — sai da DURAÇÃO da coleta,
            # porque a guia escreve os dois no mesmo campo e a faixa fundida
            # reprovava TWA legítimo e aprovava STEL longo demais.
            'regime': v.get('regime') or '', 'passivo': v.get('passivo') or False,
            'metodo_chave': chave_metodo(m), 'metodo_desc': v.get('metodo_desc') or '',
            'metodos': [{'chave': chave_metodo(x), 'cod': x.get('metodoCod') or '',
                         'amostrador': x.get('amostradorCod') or '',
                         'vazao': x.get('vazao') or '', 'volume': x.get('volume') or ''}
                        for x in (metodos or [])],
            'metodo_incerto': escolha_incerta(metodos, r.get('tipo_amostrador')),
        })
        if len(itens) >= limite:
            break

    resumo = {'total': len(itens)}
    for k in ('ok', 'atencao', 'fora', 'sem_metodo', 'sem_dado'):
        resumo[k] = sum(1 for x in itens if x['veredicto'] == k)
    return jsonify({'medicoes': itens, 'resumo': resumo})


@controle_bp.route('/cadeia-custodia/agentes', methods=['POST'])
@login_required
def cadeia_custodia_gravar_agentes():
    """Grava na COLETA o agente de cada amostrador.

    É o dado que nunca existia no banco: `coletas_quimico_amostr.substancia`
    está vazia em 100% dos registros porque o agente só era escrito na planilha
    da cadeia, à mão. Sem ele o sistema não sabe o que cada tubo carrega, não dá
    para conferir contra a guia de métodos e o laudo não tem com o que casar.

    Body: {"agentes": {"TCP2912AV3": "Tolueno", ...}}
    """
    init_db()
    d = request.get_json(silent=True) or {}
    mapa = d.get('agentes') or {}
    if not isinstance(mapa, dict) or not mapa:
        return jsonify({'erro': 'informe agentes: {codigo: agente}'}), 400
    if len(mapa) > 500:
        return jsonify({'erro': 'no máximo 500 por vez'}), 400
    gravados, ignorados = [], []
    with get_db() as conn:
        for cod, agente in mapa.items():
            cod = str(cod or '').strip()
            agente = str(agente or '').strip()
            if not cod or not agente:
                ignorados.append(cod or '(vazio)')
                continue
            cur = conn.execute(
                "UPDATE coletas_quimico_amostr SET substancia=? "
                "WHERE UPPER(TRIM(id_amostrador))=?", (agente, cod.upper()))
            if (getattr(cur, 'rowcount', 0) or 0) > 0:
                gravados.append(cod)
            else:
                ignorados.append(cod)
    if gravados:
        registrar_evento(
            'coleta_atualizada',
            f'Agente informado na coleta de {len(gravados)} amostrador(es): '
            + ', '.join(gravados[:8]) + ('…' if len(gravados) > 8 else ''),
            usuario=getattr(current_user, 'email', 'sistema'), ip=request.remote_addr)
    return jsonify({'ok': True, 'gravados': gravados, 'sem_coleta': ignorados})


@controle_bp.route('/cadeia-custodia/preview', methods=['POST'])
@login_required
def cadeia_custodia_preview():
    """Mostra o que vai para a cadeia ANTES de gerar — inclusive os avisos de
    campo obrigatório vazio. Não grava nada."""
    from .cadeia_custodia import coletar_dados
    d = request.get_json(silent=True) or {}
    ids = _cc_ids(d)
    if not ids:
        return jsonify({'erro': 'informe amostrador_ids'}), 400
    try:
        dados = coletar_dados(ids, d.get('demanda_id'), d.get('agentes'))
    except Exception as e:
        log.exception('[cadeia] preview falhou')
        return jsonify({'erro': str(e)}), 500
    return jsonify({
        'empresa': dados.get('empresa') or {},
        'avisos': dados.get('avisos') or [],
        # Lista para o técnico escolher o agente de CADA amostrador — nunca
        # preenchida sozinha: a OS diz o que a empresa contratou no total, não
        # o que cada tubo carrega, e chutar multiplicaria o custo da análise.
        'agentes_sugeridos': dados.get('agentes_sugeridos') or [],
        'linhas': [{k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                    for k, v in ln.items()} for ln in (dados.get('linhas') or [])],
    })


@controle_bp.route('/cadeia-custodia/gerar', methods=['POST'])
@login_required
def cadeia_custodia_gerar():
    """Gera o xlsx e (por padrão) carimba o despacho ao laboratório.

    `marcar_envio=false` gera sem carimbar — para reimprimir uma cadeia de
    amostrador já despachado sem mexer na data original.
    """
    from .cadeia_custodia import (coletar_dados, gerar_xlsx, nome_arquivo,
                                  marcar_despacho, completar_empresa)
    d = request.get_json(silent=True) or {}
    ids = _cc_ids(d)
    if not ids:
        return jsonify({'erro': 'informe amostrador_ids'}), 400
    try:
        # Dado da empresa digitado na tela entra no CADASTRO antes de montar a
        # cadeia — 99% das empresas estão sem CNPJ, que o lab marca como
        # obrigatório. Digitado uma vez, fica.
        if d.get('empresa'):
            previa = coletar_dados(ids, d.get('demanda_id'))
            emp_id = (previa.get('empresa') or {}).get('id')
            if emp_id:
                completar_empresa(emp_id, d['empresa'])
        dados = coletar_dados(ids, d.get('demanda_id'), d.get('agentes'))
        if not dados.get('linhas'):
            return jsonify({'erro': 'nenhum amostrador válido'}), 400
        # AGENTE é obrigatório no formulário: sem ele o laboratório não sabe o
        # que analisar e a importação eletrônica é rejeitada. `forcar=true`
        # libera (o técnico pode preencher à mão depois).
        sem_agente = [ln['codigo'] for ln in dados['linhas'] if not ln.get('agentes')]
        if sem_agente and not d.get('forcar'):
            return jsonify({
                'erro': 'Escolha o agente de cada amostrador antes de gerar',
                'sem_agente': sem_agente,
                'agentes_sugeridos': dados.get('agentes_sugeridos') or [],
            }), 422
        data_envio = d.get('data_envio')
        blob = gerar_xlsx(dados, data_envio)
        if d.get('marcar_envio', True):
            marcar_despacho(ids, data_envio,
                            d.get('dias_validade', 45), d.get('lote', ''))
            usuario = getattr(current_user, 'email', 'sistema')
            registrar_evento(
                'cadeia_custodia_gerada',
                f"Cadeia de custódia — {(dados.get('empresa') or {}).get('nome') or 's/empresa'} "
                f"({len(ids)} amostradores)",
                usuario=usuario, ip=request.remote_addr)
    except FileNotFoundError as e:
        return jsonify({'erro': str(e)}), 500
    except Exception as e:
        log.exception('[cadeia] geração falhou')
        return jsonify({'erro': str(e)}), 500
    return send_file(
        io.BytesIO(blob),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=nome_arquivo(dados, d.get('data_envio')))


# ── Orquestrador da OS (substitui o MAESTRO) — rotas /controle/os/* ────
# Registrado aqui (import time) para entrar no blueprint ANTES do register.
from .orquestrador import registrar_rotas as _orq_registrar_rotas
_orq_registrar_rotas(controle_bp)
