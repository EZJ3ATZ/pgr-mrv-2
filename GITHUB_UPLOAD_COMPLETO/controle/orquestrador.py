# -*- coding: utf-8 -*-
"""Orquestrador da OS — substitui o MAESTRO no pós-fechamento (v1, 22/07/2026).

Requisitos aprovados pelo Bernardo (22/07):
  - Nº da OS gerado AQUI (hoje é o MAESTRO que gera), formato lógico com data.
  - Fan-out por raia ao abrir a OS; cronômetro por raia (SLA por setor).
  - Medição contratada entra DIRETO no portal (tabela demandas), sem Planner.
  - Engenharia/Ergonomia/Treinamento: fila "a distribuir" com técnico sugerido;
    Valéria e Luiz APROVAM (sugestão automática) → aí cria a task no Planner
    (ergonomia no plano próprio do grupo Ergonomia, interno/externo).
  - Credenciamento: só e-mail de apresentação. Cobrança: e-mail padrão do
    financeiro (tabela SERVIÇOS|VALOR|VENCIMENTO|O.S|PARCELAMENTO).
  - Onboarding: apenas cliente novo com mais de 300 vidas (acesso SOC/LuzIA).
  - Aprovou → opcional criar linha no BI da engenharia (v1 registra pendência).

Envio de e-mail é OPT-IN: só dispara com ORQ_ENVIAR_EMAILS=1 no ambiente;
sem a flag, o corpo fica pronto em detalhe_json (status pendente_envio).
"""
import os
import re
import json
import logging
from datetime import datetime, timezone

from flask import request, jsonify

from .db import get_db, row_to_dict, registrar_evento, USE_PG, data_iso

log = logging.getLogger(__name__)

EMAIL_FINANCEIRO     = os.environ.get('ORQ_EMAIL_FINANCEIRO', 'grupofinanceiro@ocupacional.com.br')
EMAIL_CREDENCIAMENTO = os.environ.get('ORQ_EMAIL_CREDENCIAMENTO', 'credenciamento@ocupacional.com.br')
EMAIL_REMETENTE      = os.environ.get('ORQ_MAIL_FROM', 'medicoes@ocupacional.com.br')
ONBOARDING_MIN_VIDAS = 300
# Abaixo desta similaridade, CNPJ que casa com nome diferente vira revisão humana.
# Mesmo limiar do fuzzy de `empresa_match.encontrar_empresa`.
MATCH_NOME_MIN       = 0.72
# Roster da engenharia (ergonomia/treinamento/eng) mora na BI, dentro do Assinador.
ASSINADOR_URL        = os.environ.get(
    'ASSINADOR_URL', 'https://assinador-ocupacional-a1-production.up.railway.app')
ROSTER_TTL_OK        = 600   # s — cache do roster quando a busca deu certo
ROSTER_TTL_ERRO      = 60    # s — e quando falhou, para não bater a cada raia
# Cargo de gestão não executa demanda. Mesma régua que o Painel de Entregas
# Técnicas já usa para tirar gestor da disputa (`painel_planner.py:1556`) —
# reusada de propósito, para as duas telas não discordarem sobre quem é técnico.
_RE_GESTAO = re.compile(r'DIRETOR|COORDENADOR|SUPERVISOR|GERENTE|GESTOR', re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS os_ordens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    numero          TEXT UNIQUE,
    empresa         TEXT NOT NULL,
    cnpj            TEXT,
    vidas           INTEGER,
    cliente_novo    INTEGER DEFAULT 0,
    consultor       TEXT,
    contato_nome    TEXT,
    contato_email   TEXT,
    contato_tel     TEXT,
    negocio_crm_id  TEXT,
    servicos_json   TEXT,
    vencimento      TEXT,
    parcelamento    TEXT,
    obs             TEXT,
    status          TEXT DEFAULT 'aberta',
    criado_em       TEXT,
    concluido_em    TEXT
);
CREATE TABLE IF NOT EXISTS os_raias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    os_id           INTEGER NOT NULL,
    raia            TEXT NOT NULL,
    status          TEXT DEFAULT 'pendente',
    detalhe_json    TEXT,
    tecnico_sugerido TEXT,
    tecnico_definido TEXT,
    aprovado_por    TEXT,
    planner_task_id TEXT,
    demanda_id      INTEGER,
    iniciado_em     TEXT,
    aprovado_em     TEXT,
    concluido_em    TEXT,
    FOREIGN KEY (os_id) REFERENCES os_ordens(id)
);
"""

_schema_ok = False


def _ensure_schema(conn):
    # Postgres (produção) não aceita AUTOINCREMENT do SQLite — mesma conversão
    # do db.py (SCHEMA_PG). Sem isso o CREATE falha silencioso no executescript
    # e toda rota quebra com "relation os_ordens does not exist".
    global _schema_ok
    if not _schema_ok:
        schema = (_SCHEMA.replace('INTEGER PRIMARY KEY AUTOINCREMENT',
                                  'SERIAL PRIMARY KEY') if USE_PG else _SCHEMA)
        conn.executescript(schema)
        _schema_ok = True


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Nº da OS: lógico, com data (requisito 7 do Bernardo) ────────────────
def gerar_numero_os(conn):
    """Formato: AAAA.MMDD-NNN (sequencial do dia). Ex.: 2026.0722-003."""
    hoje = datetime.now().strftime('%Y.%m%d')
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM os_ordens WHERE numero LIKE ?",
        (f'{hoje}-%',)).fetchone()
    seq = (row_to_dict(row).get('c') or 0) + 1
    while True:
        numero = f'{hoje}-{seq:03d}'
        dup = conn.execute("SELECT 1 FROM os_ordens WHERE numero=?", (numero,)).fetchone()
        if not dup:
            return numero
        seq += 1


# ── Classificação serviço → raia ────────────────────────────────────────
_KW = {
    'medicao':      ('ruído', 'ruido', 'calor', 'vibra', 'químic', 'quimic', 'poeira',
                     'sílica', 'silica', 'dosimetria', 'medição', 'medicao', 'avaliação ambiental'),
    'ergonomia':    ('aet', 'drp', 'ergonom', 'psicossocial', 'copsoq'),
    'treinamento':  ('treinamento', 'palestra', 'capacitação', 'capacitacao', 'sipat', 'brigada'),
    'engenharia':   ('pgr', 'pcmso', 'ltcat', 'lip', 'ppr', 'pca', 'laudo', 'relatório',
                     'relatorio', 'insalubr', 'periculos', 'art', 'gro'),
}


def classificar_servico(servico):
    cat = (servico.get('categoria') or '').strip().lower()
    if cat in ('medicao', 'ergonomia', 'treinamento', 'engenharia'):
        return cat
    nome = (servico.get('nome') or '').lower()
    for raia, kws in _KW.items():
        if any(k in nome for k in kws):
            return raia
    return 'engenharia'   # documento técnico genérico → engenharia avalia


# ── Sugestão de técnico (v1: menor carga entre técnicos já ativos) ─────
_roster_cache = {'ate': 0.0, 'nomes': []}


def _roster_engenharia():
    """Nomes do roster da engenharia — vive na BI, servida pelo Assinador.

    Best-effort de propósito: se o Assinador não responder, devolve [] e a raia
    fica sem sugestão. Abrir a OS nunca pode depender de outro app estar no ar.
    """
    import time
    agora = time.time()
    if agora < _roster_cache['ate']:
        return _roster_cache['nomes']
    segredo = os.environ.get('CRM_PLANNER_SECRET', '')
    nomes, ok = [], False
    if segredo:
        try:
            import json as _j
            import urllib.request
            req = urllib.request.Request(
                ASSINADOR_URL.rstrip('/') + '/api/painel-engenharia/roster',
                headers={'x-crm-secret': segredo})
            with urllib.request.urlopen(req, timeout=6) as r:
                dados = _j.loads(r.read().decode('utf-8'))
            nomes = [t['nome'].strip() for t in dados.get('tecnicos', [])
                     if (t.get('nome') or '').strip()
                     and not _RE_GESTAO.search(t.get('cargo') or '')]
            ok = True
        except Exception as e:
            log.warning('[orq] roster da engenharia indisponível (%s) — '
                        'raia fica sem sugestão', e)
    _roster_cache['nomes'] = nomes
    _roster_cache['ate'] = agora + (ROSTER_TTL_OK if ok else ROSTER_TTL_ERRO)
    return nomes


def _carga_atual(conn, raia):
    """Quanto cada pessoa já tem em aberto. Medição conta pelas `demandas`
    (é onde o técnico de campo realmente aparece); as outras raias contam
    pelas próprias raias, que é o único registro que o portal tem delas."""
    if raia == 'medicao':
        sql = """SELECT responsavel AS quem, COUNT(*) AS carga FROM demandas
                 WHERE responsavel IS NOT NULL AND responsavel != ''
                   AND status != 'concluida' GROUP BY responsavel"""
        args = ()
    else:
        sql = """SELECT tecnico_definido AS quem, COUNT(*) AS carga FROM os_raias
                 WHERE raia=? AND tecnico_definido IS NOT NULL
                   AND status != 'concluida' GROUP BY tecnico_definido"""
        args = (raia,)
    return {row_to_dict(r)['quem']: row_to_dict(r)['carga']
            for r in conn.execute(sql, args).fetchall()}


def sugerir_tecnico(conn, raia):
    """Quem tem MENOS trabalho aberto, entre quem existe de verdade.

    Antes isto olhava só `os_raias.tecnico_definido` — tabela que nasce vazia,
    então a sugestão vinha `None` para sempre e quem aprovava digitava o nome
    na mão (medido em 01/09/2026, com o orquestrador recém-ligado). Agora o
    candidato sai do cadastro real: medição usa os técnicos do portal, as
    outras raias usam o roster da engenharia. Empate desempata por nome, para
    a sugestão ser estável entre chamadas.
    """
    if raia == 'medicao':
        # Só role='tecnico': quem coordena aprova, não executa. Por isso existe
        # o papel 'coordenacao' na tela de usuários — o Luiz Fernando estava
        # cadastrado como técnico e caía no rodízio de campo (02/09/2026).
        candidatos = [row_to_dict(r)['nome'] for r in conn.execute(
            "SELECT nome FROM usuarios WHERE role='tecnico' AND ativo=1"
            " AND nome IS NOT NULL AND nome != '' ORDER BY nome").fetchall()]
    else:
        candidatos = _roster_engenharia()
    if not candidatos:
        return None
    carga = _carga_atual(conn, raia)
    return sorted(candidatos, key=lambda n: (carga.get(n, 0), n))[0]


# ── Templates de e-mail (capturados das caixas reais, 21/07) ────────────
def montar_email_cobranca(os_row, servicos):
    linhas = [f"Prezados,",
              f"",
              f"Gentileza emitir a cobrança para a empresa {os_row['empresa']}, conforme os detalhes abaixo:",
              f"",
              f"SERVIÇOS | VALOR | VENCIMENTO | O.S | PARCELAMENTO"]
    primeiro = True
    for s in servicos:
        try:
            valor = f"R$ {float(s.get('valor') or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        except (TypeError, ValueError):
            valor = str(s.get('valor') or '-')
        if primeiro:
            linhas.append(f"{s.get('nome','?')} | {valor} | {os_row.get('vencimento') or '-'} | "
                          f"{os_row['numero']} | {os_row.get('parcelamento') or '1X'}")
            primeiro = False
        else:
            linhas.append(f"{s.get('nome','?')} | {valor} | | |")
    linhas += ["",
               "Informações adicionais:",
               f"Nome do responsável na empresa: {os_row.get('contato_nome') or '-'}",
               f"Contato do responsável: {os_row.get('contato_email') or '-'} / {os_row.get('contato_tel') or '-'}",
               f"Observações: {os_row.get('obs') or 'N/A'}",
               "",
               f"(E-mail gerado automaticamente pelo CRM no fechamento — consultor(a): {os_row.get('consultor') or '-'})"]
    return {'para': EMAIL_FINANCEIRO,
            'assunto': f"Cobrança / Documentação para contrato - {os_row['empresa']} - O.S {os_row['numero']}",
            'corpo': '\n'.join(linhas)}


def montar_email_credenciamento(os_row):
    corpo = (f"Prezado cliente {os_row['empresa']},\n\n"
             f"Somos o setor de Credenciamento do Grupo Ocupacional — cuidamos da rede de clínicas "
             f"credenciadas onde seus colaboradores realizam exames em todo o país.\n\n"
             f"Estamos à disposição para orientar agendamentos e indicar a unidade credenciada mais "
             f"próxima de cada localidade.\n\nContato: {EMAIL_CREDENCIAMENTO}\n\n"
             f"(Referência interna: O.S {os_row['numero']})")
    return {'para': os_row.get('contato_email') or '', 'cc': EMAIL_CREDENCIAMENTO,
            'assunto': f"Credenciamento Grupo Ocupacional - {os_row['empresa']}",
            'corpo': corpo}


def montar_email_onboarding(os_row):
    corpo = (f"Oi, tudo bem?\n\n"
             f"Estamos entusiasmados em começar nossa parceria! Para garantir um início tranquilo, "
             f"vamos agendar o seu onboarding — um bate-papo online de 5 minutos para orientar o envio "
             f"dos documentos e acompanhar as etapas até a geração dos programas PGR e PCMSO.\n\n"
             f"Você também receberá a planilha MODELO 01 (dados cadastrais dos colaboradores), que é a "
             f"base do nosso trabalho — sem ela não conseguimos iniciar. Basta preenchê-la e devolver "
             f"para suportecliente@ocupacional.com.br.\n\n"
             f"Nosso suporte criará na sequência o seu acesso aos sistemas SOC/LuzIA.\n\n"
             f"(O.S {os_row['numero']} — gerado automaticamente no fechamento)")
    return {'para': os_row.get('contato_email') or '', 'cc': 'suportesoc@ocupacional.com.br',
            'assunto': f"ONBOARDING OCUPACIONAL - {os_row['empresa']}",
            'corpo': corpo}


def enviar_email_graph(msg):
    """Envia via Graph sendMail (app-only) a partir de EMAIL_REMETENTE.
    Retorna (ok, erro). Nunca levanta — falha vira raia pendente_envio."""
    try:
        from .graph import graph_post
        payload = {'message': {
            'subject': msg['assunto'],
            'body': {'contentType': 'Text', 'content': msg['corpo']},
            'toRecipients': [{'emailAddress': {'address': msg['para']}}],
        }, 'saveToSentItems': True}
        if msg.get('cc'):
            payload['message']['ccRecipients'] = [{'emailAddress': {'address': msg['cc']}}]
        graph_post(f'/users/{EMAIL_REMETENTE}/sendMail', payload)
        return True, None
    except Exception as e:
        return False, str(e)


def _envio_habilitado():
    return os.environ.get('ORQ_ENVIAR_EMAILS') == '1'


def _orq_ativo():
    """Interruptor MESTRE do orquestrador. Default DESLIGADO.

    Com OFF (padrão), NADA é gravado nem disparado em produção — demandas,
    Planner, os_ordens/os_raias, e-mail: abrir_os vira preview (dry_run forçado)
    e aprovar/concluir não executam. O CRM não está em uso; ligar de propósito
    com ORQ_ATIVO=1 no ambiente é uma decisão explícita.
    Ver vault: Ideas/Orquestrador OS — Checklist de ativação (o que falta ligar)."""
    return os.environ.get('ORQ_ATIVO') == '1'


# ── Núcleo: abrir OS (fan-out) ──────────────────────────────────────────
def abrir_os(payload, dry_run=False):
    # Interruptor mestre: dormente ⇒ nunca grava, só devolve o preview.
    dormente = not _orq_ativo()
    dry_run = dry_run or dormente
    servicos = payload.get('servicos') or []
    por_raia = {}
    for s in servicos:
        por_raia.setdefault(classificar_servico(s), []).append(s)

    with get_db() as conn:
        _ensure_schema(conn)
        numero = gerar_numero_os(conn)
        os_row = {
            'numero': numero,
            'empresa': (payload.get('empresa') or '').strip(),
            'cnpj': (payload.get('cnpj') or '').strip(),
            'vidas': payload.get('vidas'),
            'cliente_novo': 1 if payload.get('cliente_novo') else 0,
            'consultor': (payload.get('consultor') or '').strip(),
            'contato_nome': (payload.get('contato_nome') or '').strip(),
            'contato_email': (payload.get('contato_email') or '').strip(),
            'contato_tel': (payload.get('contato_tel') or '').strip(),
            'negocio_crm_id': str(payload.get('negocio_crm_id') or ''),
            'vencimento': (payload.get('vencimento') or '').strip(),
            'parcelamento': (payload.get('parcelamento') or '').strip(),
            # Prazo combinado com o cliente, informado pela consultora no CRM.
            # `data_iso` porque a coluna é TEXT e várias consultas fazem
            # (col)::date — data em DD/MM/AAAA crua já derrubou o dashboard.
            'prazo': data_iso(payload.get('prazo')),
            'obs': (payload.get('obs') or '').strip(),
        }

        raias_plano = []

        # 1) COBRANÇA — sempre (requisito: e-mail automático pro financeiro)
        email_cob = montar_email_cobranca(os_row, servicos)
        raias_plano.append(('cobranca', 'pendente_envio', {'email': email_cob}))

        # 2) MEDIÇÃO — direto no portal, sem Planner (requisito do Matheus)
        if 'medicao' in por_raia:
            raias_plano.append(('medicao', 'aguardando_aprovacao',
                                {'itens': por_raia['medicao'],
                                 'prazo': os_row['prazo']}))

        # 3) ENGENHARIA / ERGONOMIA / TREINAMENTO — fila a distribuir
        for raia in ('engenharia', 'ergonomia', 'treinamento'):
            if raia in por_raia:
                det = {'itens': por_raia[raia]}
                if raia == 'ergonomia':
                    det['modalidade'] = payload.get('ergonomia_modalidade') or 'interna'
                raias_plano.append((raia, 'aguardando_aprovacao', det))

        # 4) CREDENCIAMENTO — só e-mail de apresentação (requisito 5)
        if payload.get('envolve_credenciamento'):
            raias_plano.append(('credenciamento', 'pendente_envio',
                                {'email': montar_email_credenciamento(os_row)}))

        # 5) ONBOARDING — cliente novo E > 300 vidas (requisito 1)
        try:
            vidas = int(os_row['vidas'] or 0)
        except (TypeError, ValueError):
            vidas = 0
        if os_row['cliente_novo'] and vidas > ONBOARDING_MIN_VIDAS:
            raias_plano.append(('onboarding', 'pendente_envio',
                                {'email': montar_email_onboarding(os_row)}))

        if dry_run:
            return {'ok': True, 'dry_run': True, 'dormente': dormente,
                    'numero': numero,
                    'raias': [{'raia': r, 'status': s, 'detalhe': d}
                              for r, s, d in raias_plano]}

        conn.execute(
            """INSERT INTO os_ordens (numero, empresa, cnpj, vidas, cliente_novo,
                 consultor, contato_nome, contato_email, contato_tel, negocio_crm_id,
                 servicos_json, vencimento, parcelamento, obs, status, criado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'aberta', ?)""",
            (numero, os_row['empresa'], os_row['cnpj'], os_row['vidas'],
             os_row['cliente_novo'], os_row['consultor'], os_row['contato_nome'],
             os_row['contato_email'], os_row['contato_tel'], os_row['negocio_crm_id'],
             json.dumps(servicos, ensure_ascii=False), os_row['vencimento'],
             os_row['parcelamento'], os_row['obs'], _now()))
        os_id = row_to_dict(conn.execute(
            "SELECT id FROM os_ordens WHERE numero=?", (numero,)).fetchone())['id']

        resultado = []
        for raia, status, det in raias_plano:
            demanda_id = None
            sugerido = None
            # medição contratada → cria a demanda no portal AGORA
            if raia == 'medicao':
                demanda_id = _criar_demanda_medicao(conn, numero, os_row, det['itens'])
                det['demanda_id'] = demanda_id
            if status == 'aguardando_aprovacao':
                sugerido = sugerir_tecnico(conn, raia)
            # e-mails: envia já se habilitado
            if status == 'pendente_envio' and _envio_habilitado():
                ok, err = enviar_email_graph(det['email'])
                if ok:
                    status = 'concluida'
                else:
                    det['erro_envio'] = err
            concluido = _now() if status == 'concluida' else None
            conn.execute(
                """INSERT INTO os_raias (os_id, raia, status, detalhe_json,
                     tecnico_sugerido, demanda_id, iniciado_em, concluido_em)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (os_id, raia, status, json.dumps(det, ensure_ascii=False),
                 sugerido, demanda_id, _now(), concluido))
            resultado.append({'raia': raia, 'status': status,
                              'tecnico_sugerido': sugerido, 'demanda_id': demanda_id})

        try:
            registrar_evento('os_aberta_crm',
                             f"OS {numero} aberta pelo CRM: {os_row['empresa'][:60]} "
                             f"({len(resultado)} raias)", ref_tipo='os')
        except Exception:
            pass
        return {'ok': True, 'dry_run': False, 'numero': numero,
                'os_id': os_id, 'raias': resultado}


def _criar_demanda_medicao(conn, numero, os_row, itens):
    """Medição contratada entra direto na tabela demandas do portal.

    O CNPJ casa a empresa, mas casar SÓ por CNPJ é cego: um dígito errado no
    fechamento joga a OS na ficha de outro cliente e nada avisa. Medido em
    01/09/2026 — abri uma OS de teste com o CNPJ do Banco do Brasil e a demanda
    entrou calada na ficha do BANCO DO BRASIL SA. Por isso, quando o CNPJ casa
    mas o NOME não bate, a demanda nasce com `needs_review=1` e guarda o método
    e o score do match, para aparecer no banner de revisão humana.
    """
    from .empresa_match import similaridade
    metodo, score, revisar = None, None, 0
    emp = None
    if os_row['cnpj']:
        emp = conn.execute("SELECT id, nome FROM empresas WHERE cnpj=?",
                           (os_row['cnpj'],)).fetchone()
        if emp:
            nome_banco = row_to_dict(emp).get('nome') or ''
            sim = similaridade(os_row['empresa'], nome_banco)
            if sim < MATCH_NOME_MIN:
                metodo, score, revisar = 'cnpj_nome_divergente', round(sim, 3), 1
                log.warning('[orq] OS %s: CNPJ %s casou com "%s", mas a OS diz "%s" '
                            '(similaridade %.2f) — demanda marcada para revisão',
                            numero, os_row['cnpj'], nome_banco, os_row['empresa'], sim)
            else:
                metodo, score = 'cnpj', 1.0
    if not emp:
        emp = conn.execute("SELECT id, nome FROM empresas WHERE nome=?",
                           (os_row['empresa'],)).fetchone()
        if emp:
            metodo, score = 'nome_exato', 1.0
    if emp:
        empresa_id = row_to_dict(emp)['id']
    else:
        conn.execute("INSERT INTO empresas (cnpj, nome) VALUES (?,?)",
                     (os_row['cnpj'] or None, os_row['empresa']))
        empresa_id = row_to_dict(conn.execute(
            "SELECT id FROM empresas WHERE nome=?",
            (os_row['empresa'],)).fetchone())['id']
        metodo, score = 'criada', 1.0
    desc = "MEDIÇÕES A REALIZAR:\n" + "\n".join(
        f"- {i.get('nome','?')}" + (f" (x{int(i['quantidade'])})"
        if i.get('quantidade') and float(i['quantidade']) > 1 else '')
        for i in itens)
    titulo = f"{numero} - {os_row['empresa']}"
    conn.execute(
        """INSERT INTO demandas (numero_os, empresa_id, cnpj, titulo, nome_tarefa,
             descricao, status, origem, tipo_demanda, prazo,
             empresa_match_score, empresa_match_metodo, needs_review,
             criado_em, atualizado_em)
           VALUES (?,?,?,?,?,?, 'pendente', 'crm_os', 'operacional', ?, ?,?,?, ?, ?)""",
        (numero, empresa_id, os_row['cnpj'], titulo, titulo, desc,
         os_row.get('prazo'), score, metodo, revisar, _now(), _now()))
    return row_to_dict(conn.execute(
        "SELECT id FROM demandas WHERE numero_os=? ORDER BY id DESC LIMIT 1",
        (numero,)).fetchone())['id']


# ── Aprovação (Valéria/Luiz) → cria task no Planner ─────────────────────
def aprovar_raia(numero, raia_id, tecnico, aprovado_por, criar_linha_bi=False):
    if not _orq_ativo():
        return {'ok': True, 'dormente': True, 'numero': numero, 'raia_id': raia_id,
                'tecnico': tecnico,
                'mensagem': 'Orquestrador dormente (ORQ_ATIVO=0): aprovação simulada — '
                            'nada criado no Planner nem gravado.'}, 200
    from .graph import (graph_ok, criar_planner_task, set_task_description,
                        get_category_ids_by_names, get_bucket_id_by_name,
                        get_plan_id_by_title, PLAN_ENTREGAS_TECNICAS,
                        BUCKET_ENG_NOVAS_DEMANDAS, GRUPO_ERGONOMIA)
    with get_db() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT r.*, o.empresa, o.numero FROM os_raias r
               JOIN os_ordens o ON o.id = r.os_id
               WHERE r.id=? AND o.numero=?""", (raia_id, numero)).fetchone()
        if not row:
            return {'ok': False, 'erro': 'raia não encontrada'}, 404
        r = row_to_dict(row)
        if r['status'] != 'aguardando_aprovacao':
            return {'ok': False, 'erro': f"raia está '{r['status']}', não aguardando aprovação"}, 409
        det = json.loads(r.get('detalhe_json') or '{}')
        itens = det.get('itens') or []
        task_id = None

        if r['raia'] == 'medicao':
            # demanda já existe no portal — só define o responsável
            if r.get('demanda_id'):
                conn.execute("UPDATE demandas SET responsavel=?, atualizado_em=? WHERE id=?",
                             (tecnico, _now(), r['demanda_id']))
        elif graph_ok():
            titulo = f"{r['numero']} - {r['empresa']}"
            if r['raia'] == 'ergonomia':
                plan_id = get_plan_id_by_title(GRUPO_ERGONOMIA, 'ergonomia')
                titulo = f"[{r['numero']}] AET - {r['empresa']}"
                task = criar_planner_task(plan_id or PLAN_ENTREGAS_TECNICAS, titulo)
            elif r['raia'] == 'treinamento':
                labels = get_category_ids_by_names(PLAN_ENTREGAS_TECNICAS, ['TREINAMENTO'])
                task = criar_planner_task(PLAN_ENTREGAS_TECNICAS, titulo,
                                          applied_categories=labels or None)
            else:  # engenharia
                nomes = [i.get('nome', '') for i in itens]
                labels = get_category_ids_by_names(PLAN_ENTREGAS_TECNICAS, nomes)
                bucket = get_bucket_id_by_name(PLAN_ENTREGAS_TECNICAS,
                                               'Engenharia - Novas Demandas',
                                               BUCKET_ENG_NOVAS_DEMANDAS)
                task = criar_planner_task(PLAN_ENTREGAS_TECNICAS, titulo,
                                          applied_categories=labels or None,
                                          bucket_id=bucket)
            task_id = task.get('id')
            try:
                desc = (f"O.S {r['numero']} — {r['empresa']}\n"
                        f"Responsável: {tecnico}\nAprovado por: {aprovado_por}\n\nSERVIÇOS:\n"
                        + "\n".join(f"- {i.get('nome','?')}" for i in itens))
                set_task_description(task_id, desc)
            except Exception as e:
                log.warning('[orq] descrição task %s: %s', task_id, e)
        else:
            return {'ok': False, 'erro': 'Graph não configurado'}, 503

        det['criar_linha_bi'] = bool(criar_linha_bi)
        conn.execute(
            """UPDATE os_raias SET status='em_andamento', tecnico_definido=?,
                 aprovado_por=?, planner_task_id=?, aprovado_em=?, detalhe_json=?
               WHERE id=?""",
            (tecnico, aprovado_por, task_id, _now(),
             json.dumps(det, ensure_ascii=False), raia_id))
        try:
            registrar_evento('os_raia_aprovada',
                             f"OS {numero} · {r['raia']} → {tecnico} (por {aprovado_por})"
                             + (' + linha BI' if criar_linha_bi else ''), ref_tipo='os')
        except Exception:
            pass
        return {'ok': True, 'numero': numero, 'raia': r['raia'],
                'tecnico': tecnico, 'planner_task_id': task_id,
                'bi_linha_pendente': bool(criar_linha_bi)}, 200


def concluir_raia(numero, raia_id):
    if not _orq_ativo():
        return {'ok': True, 'dormente': True,
                'mensagem': 'Orquestrador dormente (ORQ_ATIVO=0): conclusão simulada.'}, 200
    with get_db() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT r.id, r.status, o.id AS os_id FROM os_raias r
               JOIN os_ordens o ON o.id=r.os_id WHERE r.id=? AND o.numero=?""",
            (raia_id, numero)).fetchone()
        if not row:
            return {'ok': False, 'erro': 'raia não encontrada'}, 404
        r = row_to_dict(row)
        conn.execute("UPDATE os_raias SET status='concluida', concluido_em=? WHERE id=?",
                     (_now(), raia_id))
        pend = row_to_dict(conn.execute(
            "SELECT COUNT(*) AS c FROM os_raias WHERE os_id=? AND status!='concluida'",
            (r['os_id'],)).fetchone())['c']
        os_concluida = False
        if not pend:
            conn.execute("UPDATE os_ordens SET status='concluida', concluido_em=? WHERE id=?",
                         (_now(), r['os_id']))
            os_concluida = True
            try:
                registrar_evento('os_concluida', f'OS {numero} concluída (todas as raias)',
                                 ref_tipo='os')
            except Exception:
                pass
        return {'ok': True, 'os_concluida': os_concluida}, 200


# ── Painel / SLA (requisito 6: tempo por setor) ─────────────────────────
def painel(negocio=None):
    """Sem filtro: últimas 100 OS (visão geral). Com `negocio` (id do negócio
    no CRM): TODAS as OS daquele negócio, sem LIMIT — o rastreio do vendedor
    não pode perder OS antiga quando o volume passar de 100."""
    with get_db() as conn:
        _ensure_schema(conn)
        if negocio:
            ordens = [row_to_dict(x) for x in conn.execute(
                "SELECT * FROM os_ordens WHERE negocio_crm_id=? ORDER BY id DESC",
                (str(negocio),)).fetchall()]
        else:
            ordens = [row_to_dict(x) for x in conn.execute(
                "SELECT * FROM os_ordens ORDER BY id DESC LIMIT 100").fetchall()]
        for o in ordens:
            raias = [row_to_dict(x) for x in conn.execute(
                "SELECT * FROM os_raias WHERE os_id=? ORDER BY id", (o['id'],)).fetchall()]
            for ra in raias:
                ini = ra.get('iniciado_em')
                fim = ra.get('concluido_em') or _now()
                try:
                    dt_i = datetime.fromisoformat(str(ini))
                    dt_f = datetime.fromisoformat(str(fim))
                    ra['horas_decorridas'] = round((dt_f - dt_i).total_seconds() / 3600, 1)
                except Exception:
                    ra['horas_decorridas'] = None
                ra.pop('detalhe_json', None)
            o['raias'] = raias
        return {'ok': True, 'ordens': ordens}


# ── Fila de aprovação (consumida pela UI do Assinador) ──────────────────
def fila_aprovacao(raias=None):
    """Raias em 'aguardando_aprovacao' (fila da Valéria/Luiz), já com os itens
    do serviço e o técnico sugerido. `raias` opcional filtra os tipos
    (ex.: ['engenharia','ergonomia','treinamento']).

    Só leitura — não depende de ORQ_ATIVO. Com o orquestrador dormente a fila
    fica vazia (abrir_os não grava), o que é o comportamento esperado."""
    with get_db() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT r.id AS raia_id, r.raia, r.status, r.detalhe_json,
                      r.tecnico_sugerido, r.iniciado_em,
                      o.numero, o.empresa, o.cnpj, o.vidas, o.consultor,
                      o.contato_nome, o.vencimento
               FROM os_raias r JOIN os_ordens o ON o.id = r.os_id
               WHERE r.status='aguardando_aprovacao'
               ORDER BY r.id""").fetchall()
        fila = []
        for x in rows:
            d = row_to_dict(x)
            if raias and d['raia'] not in raias:
                continue
            det = json.loads(d.pop('detalhe_json') or '{}')
            d['itens'] = det.get('itens') or []
            if det.get('modalidade'):
                d['modalidade'] = det['modalidade']
            fila.append(d)
        return {'ok': True, 'fila': fila, 'total': len(fila)}


# ── Rotas ───────────────────────────────────────────────────────────────
def registrar_rotas(bp):
    def _auth_secret():
        segredo = os.environ.get('CRM_PLANNER_SECRET', '')
        return bool(segredo) and request.headers.get('x-crm-secret') == segredo

    @bp.route('/os/abrir', methods=['POST'])
    def orq_abrir_os():
        if not _auth_secret():
            return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
        body = request.get_json(silent=True) or {}
        if not (body.get('empresa') or '').strip():
            return jsonify({'ok': False, 'erro': 'empresa obrigatória'}), 400
        try:
            return jsonify(abrir_os(body, dry_run=bool(body.get('dry_run'))))
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({'ok': False, 'erro': str(e)}), 500

    @bp.route('/os/<numero>/aprovar', methods=['POST'])
    def orq_aprovar(numero):
        body = request.get_json(silent=True) or {}
        if not _auth_secret():
            from flask_login import current_user
            if not getattr(current_user, 'is_authenticated', False):
                return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
            body.setdefault('aprovado_por', getattr(current_user, 'nome', None)
                            or getattr(current_user, 'email', 'usuário'))
        if not body.get('raia_id') or not (body.get('tecnico') or '').strip():
            return jsonify({'ok': False, 'erro': 'raia_id e tecnico obrigatórios'}), 400
        resp, code = aprovar_raia(numero, body['raia_id'], body['tecnico'].strip(),
                                  (body.get('aprovado_por') or '').strip() or 'coordenação',
                                  bool(body.get('criar_linha_bi')))
        return jsonify(resp), code

    @bp.route('/os/<numero>/raia/<int:raia_id>/concluir', methods=['POST'])
    def orq_concluir(numero, raia_id):
        if not _auth_secret():
            from flask_login import current_user
            if not getattr(current_user, 'is_authenticated', False):
                return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
        resp, code = concluir_raia(numero, raia_id)
        return jsonify(resp), code

    @bp.route('/os/painel')
    def orq_painel():
        if not _auth_secret():
            from flask_login import current_user
            if not getattr(current_user, 'is_authenticated', False):
                return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
        return jsonify(painel(negocio=request.args.get('negocio')))

    @bp.route('/os/fila')
    def orq_fila():
        if not _auth_secret():
            from flask_login import current_user
            if not getattr(current_user, 'is_authenticated', False):
                return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
        tipos = request.args.get('raias')
        raias = [t.strip() for t in tipos.split(',') if t.strip()] if tipos else None
        return jsonify(fila_aprovacao(raias))
