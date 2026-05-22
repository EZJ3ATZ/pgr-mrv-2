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
from datetime import datetime, timezone

from .graph import (
    graph_ok, get_teams_groups, get_plans_for_group,
    get_plan_buckets, get_plan_tasks, get_task_details, get_user,
)
from .db import get_db, init_db

log = logging.getLogger(__name__)

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


def _parse_date(s) -> str | None:
    """Converte ISO 8601 do Graph para YYYY-MM-DD."""
    if not s:
        return None
    try:
        return s[:10]
    except Exception:
        return None


def _task_to_demanda(task: dict, bucket_map: dict, plan: dict, group: dict) -> dict:
    """Converte um task do Planner para o formato interno de demanda."""
    assignees = list(task.get('assignments', {}).keys())
    assignee_id   = assignees[0] if assignees else None
    all_assignees = assignees

    completed = task.get('percentComplete', 0) == 100
    status_raw = 'concluida' if completed else (
        'em_andamento' if task.get('percentComplete', 0) > 0 else 'aberta'
    )

    return {
        'planner_task_id':    task['id'],
        'planner_plan_id':    task.get('planId', ''),
        'planner_plan_nome':  plan.get('title', ''),
        'planner_bucket_id':  task.get('bucketId', ''),
        'planner_bucket':     bucket_map.get(task.get('bucketId', ''), ''),
        'planner_group_id':   group.get('id', ''),
        'planner_group_nome': group.get('displayName', ''),
        'titulo':             task.get('title', 'Sem título'),
        'prioridade':         PRIORITY_MAP.get(task.get('priority', 5), 'media'),
        'status':             status_raw,
        'percent_complete':   task.get('percentComplete', 0),
        'prazo':              _parse_date(task.get('dueDateTime')),
        'criado_em_ms':       _parse_date(task.get('createdDateTime')),
        'concluido_em_ms':    _parse_date(task.get('completedDateTime')),
        'ms_assignee_id':     assignee_id,
        'ms_assignees_json':  json.dumps(all_assignees),
        'etiquetas_json':     json.dumps(task.get('appliedCategories', {})),
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
                atualizado_em=CURRENT_TIMESTAMP
            WHERE planner_task_id=?
        ''', (
            d['titulo'], d['prioridade'], d['status'],
            d['percent_complete'], d['prazo'],
            d['planner_bucket'], d['planner_plan_nome'],
            d['ms_assignee_id'], d['ms_assignees_json'],
            d['etiquetas_json'], desc, checklist_json,
            d['planner_task_id'],
        ))
        return existing['id'], 'updated'
    else:
        cur = conn.execute('''
            INSERT INTO demandas (
                planner_task_id, planner_plan_id, planner_plan_nome,
                planner_bucket_id, planner_bucket, planner_group_id, planner_group_nome,
                titulo, prioridade, status, percent_complete,
                prazo, criado_em_ms, concluido_em_ms,
                ms_assignee_id, ms_assignees_json,
                etiquetas_json, descricao, checklist,
                origem, criado_em, atualizado_em
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'planner',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        ''', (
            d['planner_task_id'], d['planner_plan_id'], d['planner_plan_nome'],
            d['planner_bucket_id'], d['planner_bucket'],
            d['planner_group_id'], d['planner_group_nome'],
            d['titulo'], d['prioridade'], d['status'], d['percent_complete'],
            d['prazo'], d['criado_em_ms'], d['concluido_em_ms'],
            d['ms_assignee_id'], d['ms_assignees_json'],
            d['etiquetas_json'], desc, checklist_json,
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
            INSERT OR REPLACE INTO ms_users (ms_id, display_name, email, job_title, department, atualizado_em)
            VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
        ''', (user_id, u.get('displayName',''), u.get('mail',''),
              u.get('jobTitle',''), u.get('department','')))
        return u
    except Exception as e:
        log.warning('[planner_sync] ms_user %s: %s', user_id, e)
        return {}


# ── Ponto de entrada principal ─────────────────────────────────────────

def sync_planner(group_filter: str = None) -> dict:
    """
    Sincroniza Planner → sistema.

    Args:
        group_filter: se fornecido, sincroniza apenas o grupo com este ID ou nome.

    Returns:
        Dicionário com estatísticas do sync.
    """
    if not graph_ok():
        return {'erro': 'Credenciais Azure não configuradas ou inválidas.'}

    init_db()
    stats = {
        'grupos':    0,
        'planos':    0,
        'tarefas':   0,
        'criadas':   0,
        'atualizadas': 0,
        'erros':     [],
        'iniciado_em': datetime.now(timezone.utc).isoformat(),
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

    log.info('[planner_sync] %d grupos encontrados', len(grupos))

    with get_db() as conn:
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

                # Listar tarefas
                try:
                    tarefas = get_plan_tasks(pid)
                except Exception as e:
                    log.warning('[planner_sync] tarefas plano %s: %s', pnome, e)
                    stats['erros'].append(f'Plano {pnome}: {e}')
                    continue

                log.info('[planner_sync] plano "%s" → %d tarefas', pnome, len(tarefas))

                for tarefa in tarefas:
                    stats['tarefas'] += 1
                    tid = tarefa['id']

                    try:
                        # Buscar detalhes (descrição + checklist)
                        details = get_task_details(tid)
                        desc = details.get('description', '')
                        checklist_raw = details.get('checklist', {})
                        checklist = [
                            {
                                'titulo':     v.get('title', ''),
                                'concluido':  v.get('isChecked', False),
                                'ordem':      v.get('orderHint', ''),
                            }
                            for v in checklist_raw.values()
                        ] if isinstance(checklist_raw, dict) else []
                        checklist_json = json.dumps(checklist, ensure_ascii=False)

                        # Cachear assignee(s) no banco
                        for uid in list(tarefa.get('assignments', {}).keys()):
                            _upsert_ms_user(conn, uid)

                        # Mapear e fazer upsert
                        d = _task_to_demanda(tarefa, bucket_map, plano, grupo)
                        did, acao = _upsert_demanda(conn, d, desc, checklist_json)

                        if acao == 'created':
                            stats['criadas'] += 1
                            _registrar_evento(conn, 'demanda_criada_planner',
                                              f'Tarefa Planner importada: {d["titulo"][:80]}',
                                              did, 'demanda')
                            log.info('[planner_sync] NOVA demanda: %s', d['titulo'][:60])
                        elif acao == 'updated':
                            stats['atualizadas'] += 1

                    except Exception as e:
                        msg = f'Tarefa {tid[:8]}: {e}'
                        log.warning('[planner_sync] %s', msg)
                        stats['erros'].append(msg)

        # Atualizar estado do último sync
        conn.execute('''
            INSERT OR REPLACE INTO ms_sync_state (chave, valor, atualizado_em)
            VALUES ('last_sync', ?, CURRENT_TIMESTAMP)
        ''', (datetime.now(timezone.utc).isoformat(),))

        conn.execute('''
            INSERT OR REPLACE INTO ms_sync_state (chave, valor, atualizado_em)
            VALUES ('last_sync_stats', ?, CURRENT_TIMESTAMP)
        ''', (json.dumps(stats),))

    stats['concluido_em'] = datetime.now(timezone.utc).isoformat()
    log.info('[planner_sync] sync concluído: %s', stats)
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
