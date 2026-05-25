# -*- coding: utf-8 -*-
"""
Planner Sync — sincroniza tarefas do Microsoft Planner com demandas internas.

Fluxo:
    1. Lista todos os grupos Teams da organização
    2. Para cada grupo → lista planos do Planner
    3. Para cada plano → lista buckets e tarefas
    4. Para cada tarefa → mapeia para demanda interna (upsert por planner_task_id)
    5. Registra evento de sync

Chamado periodicamente pelo APScheduler (app.py) e manualmente via endpoint.
"""

import logging
import json
import threading
import unicodedata
from datetime import datetime, timezone

# Lock global — impede dois syncs simultâneos (APScheduler + manual)
_sync_lock = threading.Lock()

from .graph import (
    graph_ok, get_teams_groups, get_plans_for_group,
    get_plan_buckets, get_plan_tasks, get_plan_category_map,
    get_task_details, get_user,
)
from .db import get_db, init_db, upsert_raw_task, mark_raw_task
from .empresa_match import match_todas_demandas, encontrar_empresa, extrair_campos, obter_ou_criar_pendente
from .classificador import extrair_os, classificar

log = logging.getLogger(__name__)

# Monitoring opcional (não quebra se não disponível)
try:
    from .monitoring import get_logger as _get_log, capturar_erro, track_evento
    _mlog = _get_log('planner_sync')
except Exception:
    _mlog = log
    def capturar_erro(exc, **ctx): pass
    def track_evento(ev, **kw): pass

# ── Mapeamento de prioridade Planner → interna ─────────────────────────
PRIORITY_MAP = {
    0: 'urgente',    # Urgent
    1: 'urgente',
    2: 'alta',
    3: 'alta',
    4: 'alta',       # Important
    5: 'media',
    6: 'media',
    7: 'baixa',
    8: 'baixa',
    9: 'baixa',      # Low
}


# ── Filtro de pipeline ────────────────────────────────────────────────

# Buckets que representam demandas operacionais reais de clientes
_BUCKETS_OPERACIONAIS_PIPELINE = {
    'medições', 'medicoes',           # bucket principal
    'verde', 'amarela', 'vermelho', 'laranja',  # status de medições
    '🔴 em andamento', 'em andamento',
    'entregas técnicas', 'entregas tecnicas',
    'entregue / concluído', 'entregue',
    'concluído ✅', 'concluído', 'concluido',
    'clientes da base',
    'demandas para análise', 'demandas para analise',
    'novas demandas', 'engenharia - novas demandas',
    'renovação', 'renovacao',
    'inclusão de funcionários',
    'ajustes pontuais',
    'correção',
    'pcmso',
}

# Buckets que são explicitamente internos/admin — nunca viram demanda operacional
_BUCKETS_IGNORADOS = {
    'treinamento', 'treinamentos',
    'tarefas administrativas', 'administrativo',
    'reunião gestores', 'reuniao gestores',
    'kickoff', 'materiais',
    'projetos principais',
    'lista de pendências', 'lista de pendencias',
    'email respondido', 'emails respondidos',
    'emails pendentes', 'email pendente',
    'tarefas pendentes',
    'avançar', 'recursos humanos', 'rh', 'onboarding',
    'financeiro', 'compras', 'contratação',
}




def _extrair_os_comentarios(group_id: str, thread_id: str) -> str | None:
    """
    Tenta extrair número de OS dos comentários/posts da conversação da tarefa.
    Usa a API de Groups threads — requer Group.Read.All.
    Retorna o OS encontrado ou None.
    """
    import re as _re
    _tag_re = _re.compile(r'<[^>]+>')
    try:
        from .graph import get_group_thread_posts
        posts = get_group_thread_posts(group_id, thread_id)
        for post in posts:
            body = (post.get('body') or {}).get('content', '') or ''
            # Remover HTML tags
            text = _tag_re.sub(' ', body)
            # Tentar no texto inteiro
            os_num = extrair_os(text)
            if os_num:
                return os_num
            # Linha a linha
            for linha in text.splitlines():
                os_num = extrair_os(linha.strip())
                if os_num:
                    return os_num
    except Exception as e:
        log.debug('[planner_sync] comentarios OS erro: %s', e)
    return None


def _extrair_desc_comentarios(group_id: str, thread_id: str) -> str:
    """
    Extrai o texto dos comentários da conversa da tarefa do Planner.
    Usado quando o campo 'Notas' (description) está vazio — o escopo
    foi escrito na conversa ao invés do campo de notas.
    Retorna texto limpo (sem HTML) ou string vazia.
    """
    import re as _re
    _tag_re = _re.compile(r'<[^>]+>')
    _ws_re  = _re.compile(r'\s{3,}')
    try:
        from .graph import get_group_thread_posts
        posts = get_group_thread_posts(group_id, thread_id)
        partes = []
        for post in posts:
            body = (post.get('body') or {}).get('content', '') or ''
            text = _tag_re.sub(' ', body).strip()
            text = _ws_re.sub(' ', text).strip()
            if text and len(text) > 5:  # ignora posts vazios/triviais
                partes.append(text)
        return '\n---\n'.join(partes) if partes else ''
    except Exception as e:
        log.debug('[planner_sync] comentarios desc erro: %s', e)
    return ''


def _extrair_os_multi(titulo: str, desc: str, checklist: list) -> tuple[str | None, str]:
    """
    Extrai número de OS de múltiplas fontes.
    Retorna (os_numero, fonte: 'titulo'|'descricao'|'checklist'|None).
    """
    os_num = extrair_os(titulo)
    if os_num:
        return os_num, 'titulo'

    # Tentar na descrição
    if desc:
        os_num = extrair_os(desc)
        if os_num:
            return os_num, 'descricao'
        # Procurar em linhas da descrição
        for linha in desc.splitlines():
            os_num = extrair_os(linha.strip())
            if os_num:
                return os_num, 'descricao'

    # Tentar em itens do checklist
    if checklist:
        for item in checklist:
            t = item.get('titulo', '') or ''
            os_num = extrair_os(t)
            if os_num:
                return os_num, 'checklist'

    return None, 'nenhuma'


def _parse_date(s) -> str | None:
    """Converte ISO 8601 do Graph para YYYY-MM-DD."""
    if not s:
        return None
    try:
        return s[:10]
    except Exception:
        return None


def _bucket_is_concluido(bucket_name: str) -> bool:
    """Retorna True se o bucket indica que a demanda foi finalizada operacionalmente."""
    b = (bucket_name or '').lower()
    return 'entregue' in b or 'conclu' in b


def _task_to_demanda(task: dict, bucket_map: dict, plan: dict, group: dict) -> dict:
    """Converte um task do Planner para o formato interno de demanda."""
    assignees = list(task.get('assignments', {}).keys())
    assignee_id   = assignees[0] if assignees else None
    all_assignees = assignees

    titulo = task.get('title', 'Sem título')
    bucket = bucket_map.get(task.get('bucketId', ''), '')

    # Bucket "Entregue / Concluído" é o sinal operacional real de conclusão.
    # percentComplete == 100 é sinal secundário (pode estar incompleto no bucket ativo).
    is_done_bucket = _bucket_is_concluido(bucket)
    completed      = task.get('percentComplete', 0) == 100
    status_raw = 'concluida' if (is_done_bucket or completed) else (
        'em_andamento' if task.get('percentComplete', 0) > 0 else 'aberta'
    )

    return {
        'planner_task_id':    task['id'],
        'planner_plan_id':    task.get('planId', ''),
        'planner_plan_nome':  plan.get('title', ''),
        'planner_bucket_id':  task.get('bucketId', ''),
        'planner_bucket':     bucket,
        'planner_group_id':   group.get('id', ''),
        'planner_group_nome': group.get('displayName', ''),
        'titulo':             titulo,
        'prioridade':         PRIORITY_MAP.get(task.get('priority', 5), 'media'),
        'status':             status_raw,
        'percent_complete':   task.get('percentComplete', 0),
        'prazo':              _parse_date(task.get('dueDateTime')),
        'criado_em_ms':       _parse_date(task.get('createdDateTime')),
        'concluido_em_ms':    _parse_date(task.get('completedDateTime')),
        'ms_assignee_id':     assignee_id,
        'ms_assignees_json':  json.dumps(all_assignees),
        'etiquetas_json':     json.dumps(task.get('appliedCategories', {})),
        'numero_os':          extrair_os(titulo),
        'tipo_demanda':       classificar(titulo, bucket),
    }


def _upsert_demanda(conn, d: dict, desc: str, checklist_json: str) -> tuple[int, str]:
    """
    Insere ou atualiza demanda pelo planner_task_id.
    Retorna (id, 'created'|'updated'|'unchanged').
    """
    existing = conn.execute(
        'SELECT id, status, titulo FROM demandas WHERE planner_task_id=?',
        (d['planner_task_id'],)
    ).fetchone()

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    if existing:
        conn.execute('''
            UPDATE demandas SET
                titulo=?, prioridade=?, status=?,
                percent_complete=?, prazo=?,
                planner_bucket=?, planner_plan_nome=?,
                ms_assignee_id=?, ms_assignees_json=?,
                etiquetas_json=?, descricao=?, checklist=?,
                numero_os=COALESCE(numero_os, ?),
                tipo_demanda=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE planner_task_id=?
        ''', (
            d['titulo'], d['prioridade'], d['status'],
            d['percent_complete'], d['prazo'],
            d['planner_bucket'], d['planner_plan_nome'],
            d['ms_assignee_id'], d['ms_assignees_json'],
            d['etiquetas_json'], desc, checklist_json,
            d['numero_os'], d['tipo_demanda'],
            d['planner_task_id'],
        ))
        return existing['id'], 'updated'
    else:
        # empresa_id do pipeline (ou 0 como sentinela quando ainda não resolvido)
        emp_id = d.get('empresa_id', 0) or 0
        cur = conn.execute('''
            INSERT INTO demandas (
                empresa_id,
                planner_task_id, planner_plan_id, planner_plan_nome,
                planner_bucket_id, planner_bucket, planner_group_id, planner_group_nome,
                titulo, prioridade, status, percent_complete,
                prazo, criado_em_ms, concluido_em_ms,
                ms_assignee_id, ms_assignees_json,
                etiquetas_json, descricao, checklist,
                numero_os, tipo_demanda,
                origem, criado_em, atualizado_em
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'planner',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        ''', (emp_id,
            d['planner_task_id'], d['planner_plan_id'], d['planner_plan_nome'],
            d['planner_bucket_id'], d['planner_bucket'],
            d['planner_group_id'], d['planner_group_nome'],
            d['titulo'], d['prioridade'], d['status'], d['percent_complete'],
            d['prazo'], d['criado_em_ms'], d['concluido_em_ms'],
            d['ms_assignee_id'], d['ms_assignees_json'],
            d['etiquetas_json'], desc, checklist_json,
            d['numero_os'], d['tipo_demanda'],
        ))
        return cur.lastrowid, 'created'


def _registrar_evento(conn, tipo: str, descricao: str, ref_id=None, ref_tipo=None):
    """Registra evento no log operacional."""
    conn.execute('''
        INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo, criado_em)
        VALUES (?,?,?,?,CURRENT_TIMESTAMP)
    ''', (tipo, descricao, ref_id, ref_tipo))


def _upsert_ms_user(conn, user_id: str) -> dict:
    """Busca e cacheia usuário Microsoft no banco."""
    row = conn.execute('SELECT * FROM ms_users WHERE ms_id=?', (user_id,)).fetchone()
    if row:
        return dict(row)
    try:
        u = get_user(user_id)
        conn.execute('''
            INSERT INTO ms_users (ms_id, display_name, email, job_title, department, atualizado_em)
            VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT (ms_id) DO UPDATE SET
              display_name=EXCLUDED.display_name,
              email=EXCLUDED.email,
              job_title=EXCLUDED.job_title,
              department=EXCLUDED.department,
              atualizado_em=EXCLUDED.atualizado_em
        ''', (user_id, u.get('displayName',''), u.get('mail',''),
              u.get('jobTitle',''), u.get('department','')))
        return u
    except Exception as e:
        log.warning('[planner_sync] ms_user %s: %s', user_id, e)
        return {}


# ── Ponto de entrada principal ─────────────────────────────────────────

def _task_has_label(task: dict, category_ids: set) -> bool:
    """Verifica se uma tarefa tem pelo menos um dos labels indicados."""
    applied = task.get('appliedCategories', {})
    return any(cid in applied and applied[cid] for cid in category_ids)


def _normalize(s: str) -> str:
    """Remove acentos e converte para minúsculas para comparação robusta."""
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii').lower()


def _find_category_ids(category_map: dict, label_filter: str) -> set:
    """
    Dado o mapa {categoryN: 'nome'} do plano, encontra os IDs cujo
    nome contém label_filter (case e acento insensitive).
    Ex: label_filter='medicoes' → match com 'MEDIÇÕES'
    """
    needle = _normalize(label_filter.strip())
    return {k for k, v in category_map.items() if v and needle in _normalize(v)}


def sync_planner(group_filter: str = None, label_filter: str = None) -> dict:
    """
    Sincroniza Planner → sistema.

    Args:
        group_filter:  filtra por ID ou nome parcial do grupo.
        label_filter:  filtra tarefas que têm este label/flag aplicado.
                       Ex: 'medições' — só importa tarefas com esse label.

    Returns:
        Dicionário com estatísticas do sync.
    """
    if not graph_ok():
        return {'erro': 'Credenciais Azure não configuradas ou inválidas.'}

    # Evita execuções simultâneas (APScheduler + trigger manual)
    if not _sync_lock.acquire(blocking=False):
        return {'erro': 'Sync já em execução — aguarde terminar.'}

    try:
        return _sync_planner_interno(group_filter=group_filter, label_filter=label_filter)
    finally:
        _sync_lock.release()


def _sync_planner_interno(group_filter: str = None, label_filter: str = None) -> dict:

    init_db()
    stats = {
        'grupos':           0,
        'planos':           0,
        'tarefas_total':    0,
        'tarefas_filtradas': 0,
        'criadas':          0,
        'atualizadas':      0,
        'ignoradas':        0,
        # Pipeline
        'raw_criadas':      0,
        'raw_atualizadas':  0,
        'bucket_ignoradas': 0,
        'sem_os':           0,
        'empresa_fuzzy':    0,
        'empresa_pendente': 0,
        'parse_erros':      0,
        'erros':            [],
        'label_filter':     label_filter,
        'iniciado_em':      datetime.now(timezone.utc).isoformat(),
    }

    try:
        grupos = get_teams_groups()
    except Exception as e:
        log.error('[planner_sync] erro ao listar grupos: %s', e)
        return {'erro': str(e)}

    if group_filter:
        grupos = [g for g in grupos
                  if g.get('id') == group_filter or
                  group_filter.lower() in g.get('displayName', '').lower()]

    log.info('[planner_sync] %d grupos | label_filter=%s', len(grupos), label_filter)

    conn = None
    try:
        from .db import _connect, USE_PG
        conn = _connect()
        if not USE_PG:
            conn.execute('PRAGMA foreign_keys = OFF')
        _task_count = 0  # contador para commits intermediários
        COMMIT_INTERVAL = 300  # commit a cada 300 tasks

        for grupo in grupos:
            stats['grupos'] += 1
            gid   = grupo['id']
            gnome = grupo.get('displayName', gid)

            try:
                planos = get_plans_for_group(gid)
            except Exception as e:
                log.warning('[planner_sync] grupo %s sem planos: %s', gnome, e)
                stats['erros'].append(f'Grupo {gnome}: {e}')
                continue

            for plano in planos:
                stats['planos'] += 1
                pid   = plano['id']
                pnome = plano.get('title', pid)

                # Mapear buckets: id → nome
                try:
                    buckets = get_plan_buckets(pid)
                    bucket_map = {b['id']: b.get('name', '') for b in buckets}
                except Exception as e:
                    log.warning('[planner_sync] buckets plano %s: %s', pnome, e)
                    bucket_map = {}

                # Descobrir IDs dos labels que correspondem ao filtro
                category_ids = set()
                if label_filter:
                    try:
                        cat_map = get_plan_category_map(pid)
                        category_ids = _find_category_ids(cat_map, label_filter)
                        log.info('[planner_sync] plano "%s" label "%s" → categorias: %s',
                                 pnome, label_filter, category_ids)
                        if not category_ids:
                            log.warning('[planner_sync] label "%s" não encontrado no plano "%s" — '
                                        'verifique o nome exato do label no Planner.', label_filter, pnome)
                    except Exception as e:
                        log.warning('[planner_sync] category_map plano %s: %s', pnome, e)

                # Listar tarefas
                try:
                    tarefas = get_plan_tasks(pid)
                except Exception as e:
                    log.warning('[planner_sync] tarefas plano %s: %s', pnome, e)
                    stats['erros'].append(f'Plano {pnome}: {e}')
                    continue

                stats['tarefas_total'] += len(tarefas)
                stats['tarefas_filtradas'] += len(tarefas)
                log.info('[planner_sync] plano "%s" → %d tarefas para processar', pnome, len(tarefas))

                for tarefa in tarefas:
                    tid = tarefa['id']

                    try:
                        bucket = bucket_map.get(tarefa.get('bucketId', ''), '')
                        titulo = tarefa.get('title', 'Sem título')

                        # ── PRÉ-FILTRO rápido: verificar label ANTES de gravar raw ─────
                        # Evita gravar 8000 tasks ignoradas no DB
                        # Se label_filter ativo mas label não existe no plano → ignora TODAS as tasks do plano
                        tem_label = True
                        if label_filter:
                            if category_ids:
                                tem_label = _task_has_label(tarefa, category_ids)
                            else:
                                tem_label = False  # label não configurado neste plano = ignorar

                        # ── FASE 1: Gravar raw (apenas tasks operacionais + amostra audit) ─
                        # raw_json omitido para tasks ignoradas para economizar espaço/memória
                        raw_data = {
                            'planner_plan_id':    tarefa.get('planId', ''),
                            'planner_plan_nome':  pnome,
                            'planner_bucket_id':  tarefa.get('bucketId', ''),
                            'planner_bucket':     bucket,
                            'planner_group_id':   gid,
                            'planner_group_nome': gnome,
                            'titulo':             titulo,
                            'descricao':          '',
                            'checklist_json':     '[]',
                            'raw_json':           json.dumps(tarefa, ensure_ascii=False) if tem_label else '',
                            'percent_complete':   tarefa.get('percentComplete', 0),
                            'prazo':              _parse_date(tarefa.get('dueDateTime')),
                            'criado_em_ms':       _parse_date(tarefa.get('createdDateTime')),
                            'concluido_em_ms':    _parse_date(tarefa.get('completedDateTime')),
                            'ms_assignee_id':     (list(tarefa.get('assignments', {}).keys()) or [None])[0],
                            'ms_assignees_json':  json.dumps(list(tarefa.get('assignments', {}).keys())),
                            'etiquetas_json':     json.dumps(tarefa.get('appliedCategories', {})),
                        }
                        raw_id, raw_acao = upsert_raw_task(conn, tid, raw_data)
                        if raw_acao == 'created':
                            stats['raw_criadas'] += 1
                        else:
                            stats['raw_atualizadas'] += 1

                        # ── FASE 2: Filtro de label (a verdade operacional) ──
                        # REGRA: só passa task com label "Medições" aplicado.
                        if label_filter:
                            if not tem_label:
                                motivo = (
                                    f"sem label '{label_filter}'"
                                    if category_ids
                                    else f"label '{label_filter}' não configurado no plano '{pnome}'"
                                )
                                mark_raw_task(conn, raw_id, 'ignored', motivo)
                                stats['bucket_ignoradas'] += 1

                                # Commit intermediário para não perder progresso
                                _task_count += 1
                                if _task_count % COMMIT_INTERVAL == 0:
                                    conn.commit()
                                    log.info('[planner_sync] checkpoint commit: %d tasks processadas', _task_count)

                                continue  # NÃO vai para demandas

                        # ── Buscar detalhes SOMENTE para tasks que passaram no filtro ──
                        details = get_task_details(tid)
                        desc = details.get('description', '')

                        # Se o campo "Notas" estiver vazio, tenta capturar da conversa.
                        # Equipe costuma escrever o escopo nos comentários da tarefa.
                        if not desc:
                            thread_id_conv = tarefa.get('conversationThreadId')
                            if thread_id_conv:
                                desc_conv = _extrair_desc_comentarios(gid, thread_id_conv)
                                if desc_conv:
                                    desc = desc_conv
                                    log.debug('[planner_sync] desc da conversa task %s: %d chars',
                                              tid[:8], len(desc))

                        checklist_raw = details.get('checklist', {})
                        checklist = [
                            {
                                'titulo':    v.get('title', ''),
                                'concluido': v.get('isChecked', False),
                                'ordem':     v.get('orderHint', ''),
                            }
                            for v in checklist_raw.values()
                        ] if isinstance(checklist_raw, dict) else []
                        checklist_json = json.dumps(checklist, ensure_ascii=False)
                        # Atualizar raw com os detalhes agora disponíveis
                        conn.execute(
                            'UPDATE planner_raw_tasks SET descricao=?, checklist_json=? WHERE id=?',
                            (desc, checklist_json, raw_id)
                        )

                        # ── Cachear assignee(s) ───────────────────────────
                        for uid in list(tarefa.get('assignments', {}).keys()):
                            _upsert_ms_user(conn, uid)

                        # ── FASE 3: Parser ────────────────────────────────
                        os_num, os_fonte = _extrair_os_multi(titulo, desc, checklist)

                        # Tentar nos comentários se ainda não encontrou OS
                        if not os_num:
                            thread_id = tarefa.get('conversationThreadId')
                            if thread_id:
                                os_num = _extrair_os_comentarios(gid, thread_id)
                                if os_num:
                                    os_fonte = 'comentarios'

                        if not os_num:
                            stats['sem_os'] += 1
                            _mlog.debug('task_sem_os', extra={
                                'planner_task_id': tid,
                                'titulo':          titulo[:60],
                                'bucket':          bucket,
                            })

                        # Mapear demanda com OS multi-fonte
                        d = _task_to_demanda(tarefa, bucket_map, plano, grupo)
                        d['numero_os'] = os_num  # sobrescreve com resultado multi-fonte

                        # ── FASE 4: Normalização de empresa ───────────────
                        empresa_id, emp_score, emp_metodo = encontrar_empresa(conn, titulo)
                        if empresa_id:
                            if emp_metodo == 'nome_fuzzy':
                                stats['empresa_fuzzy'] += 1
                        else:
                            campos = extrair_campos(titulo)
                            empresa_id = obter_ou_criar_pendente(conn, titulo, campos)
                            emp_metodo = 'pendente'
                            stats['empresa_pendente'] += 1

                        d['empresa_id']              = empresa_id
                        d['empresa_match_score']     = round(emp_score or 0.0, 3)
                        d['empresa_match_metodo']    = emp_metodo

                        # ── FASE 5: Upsert → demandas (operational) ───────
                        did, acao = _upsert_demanda(conn, d, desc, checklist_json)
                        # Atualizar empresa_id no demandas (upsert não passa empresa_id no UPDATE)
                        conn.execute(
                            '''UPDATE demandas SET empresa_id=?, empresa_match_score=?,
                               empresa_match_metodo=? WHERE id=? AND empresa_id=0''',
                            (empresa_id, d['empresa_match_score'], emp_metodo, did)
                        )

                        mark_raw_task(conn, raw_id, 'processed')

                        if acao == 'created':
                            stats['criadas'] += 1
                            _registrar_evento(conn, 'demanda_criada_planner',
                                              f'[{bucket}] {titulo[:80]}',
                                              did, 'demanda')
                            _mlog.info('demanda_criada', extra={
                                'planner_task_id': tid,
                                'titulo':          titulo[:60],
                                'bucket':          bucket,
                                'tipo_demanda':    d['tipo_demanda'],
                                'numero_os':       os_num,
                                'os_fonte':        os_fonte,
                                'empresa_id':      empresa_id,
                                'empresa_metodo':  emp_metodo,
                                'assignee_id':     d['ms_assignee_id'],
                                'demanda_id':      did,
                            })
                        else:
                            stats['atualizadas'] += 1

                    except Exception as e:
                        msg = f'Tarefa {tid[:8]}: {e}'
                        log.warning('[planner_sync] %s', msg)
                        stats['erros'].append(msg)
                        stats['parse_erros'] += 1
                        # PostgreSQL: transação pode estar abortada após erro
                        # Rollback para permitir continuar o loop
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        capturar_erro(e,
                            operacao='planner_sync',
                            planner_task_id=tid,
                            plano=pnome,
                            grupo=gnome,
                        )

        # Atualizar estado do último sync
        conn.execute('''
            INSERT INTO ms_sync_state (chave, valor, atualizado_em)
            VALUES ('last_sync', ?, CURRENT_TIMESTAMP)
            ON CONFLICT (chave) DO UPDATE SET
              valor=EXCLUDED.valor,
              atualizado_em=EXCLUDED.atualizado_em
        ''', (datetime.now(timezone.utc).isoformat(),))

        # ── Matching empresa após sync ────────────────────────────────
        log.info('[planner_sync] iniciando matching de empresas...')
        try:
            match_stats = match_todas_demandas(conn)
            stats['match_empresas'] = match_stats
            log.info('[planner_sync] match empresas: %s', match_stats)
        except Exception as e:
            log.warning('[planner_sync] match empresas erro: %s', e)
            stats['match_empresas'] = {'erro': str(e)}

        # ── Mesclar duplicatas ─────────────────────────────────────────
        # Commit antes: mesclar_empresas_duplicatas abre conexão própria
        conn.commit()
        log.info('[planner_sync] mesclando empresas duplicadas...')
        try:
            from .db import mesclar_empresas_duplicatas
            mescladas = mesclar_empresas_duplicatas()
            stats['mescladas'] = mescladas
            log.info('[planner_sync] %d empresas mescladas', mescladas)
        except Exception as e:
            log.warning('[planner_sync] mesclar erro: %s', e)
            stats['mescladas'] = 0

        # ── Reclassificar demandas ─────────────────────────────────────
        log.info('[planner_sync] reclassificando demandas...')
        try:
            from .classificador import reclassificar_lote
            reclass_stats = reclassificar_lote(conn)
            stats['reclassificacao'] = reclass_stats
            log.info('[planner_sync] reclassificacao: %s', reclass_stats)
        except Exception as e:
            log.warning('[planner_sync] reclassificar erro: %s', e)
            stats['reclassificacao'] = {'erro': str(e)}

        conn.execute('''
            INSERT INTO ms_sync_state (chave, valor, atualizado_em)
            VALUES ('last_sync_stats', ?, CURRENT_TIMESTAMP)
            ON CONFLICT (chave) DO UPDATE SET
              valor=EXCLUDED.valor,
              atualizado_em=EXCLUDED.atualizado_em
        ''', (json.dumps(stats),))
        conn.commit()

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    stats['concluido_em'] = datetime.now(timezone.utc).isoformat()
    _mlog.info('sync_concluido', extra={
        'criadas':       stats.get('criadas', 0),
        'atualizadas':   stats.get('atualizadas', 0),
        'erros':         len(stats.get('erros', [])),
        'mescladas':     stats.get('mescladas', 0),
        'medicao':       stats.get('reclassificacao', {}).get('medicao', 0),
        'laudo':         stats.get('reclassificacao', {}).get('laudo', 0),
        'operacional':   stats.get('reclassificacao', {}).get('operacional', 0),
        'os_extraida':   stats.get('reclassificacao', {}).get('os_extraida', 0),
    })
    track_evento('planner_sync_concluido',
        criadas=stats.get('criadas', 0),
        atualizadas=stats.get('atualizadas', 0),
        erros=len(stats.get('erros', [])),
        mescladas=stats.get('mescladas', 0),
    )
    return stats


def get_sync_status() -> dict:
    """Retorna informações do último sync."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT chave, valor FROM ms_sync_state WHERE chave IN ('last_sync','last_sync_stats')"
            ).fetchall()
        data = {r['chave']: r['valor'] for r in rows}
        stats = {}
        if 'last_sync_stats' in data:
            try:
                stats = json.loads(data['last_sync_stats'])
            except Exception:
                pass
        return {
            'last_sync':    data.get('last_sync', None),
            'configurado':  graph_ok(),
            'stats':        stats,
        }
    except Exception as e:
        return {'last_sync': None, 'configurado': graph_ok(), 'erro': str(e)}
