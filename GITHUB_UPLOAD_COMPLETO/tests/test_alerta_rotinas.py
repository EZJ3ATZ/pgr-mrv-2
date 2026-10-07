# -*- coding: utf-8 -*-
"""Vigia das rotinas automáticas + a trava que impedia o alerta de abrir.

06/10/2026: o sync do laboratório ficou 26 dias rodando sem gravar nada e o
resumo diário do alerta chegava todo dia dizendo "Nada aberto". Dois motivos:
não havia regra para rotina parada, e `alerta_estado` (sem coluna id) não
estava em `_NO_ID_TABLES` — no Postgres todo INSERT ganha ' RETURNING id' e
abrir qualquer alerta estourava ("column id does not exist").
"""
import glob
import os
import re
from datetime import datetime, timedelta, timezone

from controle import alerta
from controle.db import _PGCursor, get_db, init_db

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. toda tabela sem `id` está na lista de exceção do Postgres ───────────
def _tabelas_sem_id():
    sem = set()
    for f in glob.glob(os.path.join(RAIZ, 'controle', '*.py')) + [os.path.join(RAIZ, 'app.py')]:
        s = open(f, encoding='utf-8').read()
        for m in re.finditer(r'CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(', s, re.I):
            i, d, j = m.end(), 1, m.end()
            while j < len(s) and d > 0:
                d += {'(': 1, ')': -1}.get(s[j], 0)
                j += 1
            corpo = s[i:j - 1]
            if not re.search(r'(^|[,(])\s*["\']?\s*id\s', corpo, re.I | re.M):
                sem.add(m.group(1).upper())
    return sem


def test_tabela_sem_id_esta_na_excecao_do_postgres():
    faltando = _tabelas_sem_id() - set(_PGCursor._NO_ID_TABLES)
    assert not faltando, f'INSERT nessas tabelas estoura no Postgres: {sorted(faltando)}'


# ── 2. rotina parada vira alerta "quebrou" ─────────────────────────────────
def _utc(horas_atras):
    return (datetime.now(tz=timezone.utc) - timedelta(hours=horas_atras)).strftime('%Y-%m-%d %H:%M:%S')


def _grava_lab_sync(horas_atras):
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
        conn.execute("DELETE FROM ms_sync_state WHERE chave='lab_sync_result'")
        conn.execute("INSERT INTO ms_sync_state (chave, valor, atualizado_em) VALUES ('lab_sync_result', '{}', ?)",
                     (_utc(horas_atras),))


def _chaves():
    with get_db() as conn:
        return {a[0] for a in alerta._achados_rotinas(conn)}


def setup_function(_):
    init_db()


def teardown_function(_):
    with get_db() as conn:
        conn.execute("DELETE FROM ms_sync_state WHERE chave IN ('lab_sync_result', 'last_sync')")


def test_lab_sync_parado_alerta():
    _grava_lab_sync(30)
    assert 'rotina:lab_sync' in _chaves()


def test_lab_sync_em_dia_nao_alerta():
    _grava_lab_sync(1)
    assert 'rotina:lab_sync' not in _chaves()


def test_sem_registro_nao_e_parada():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
        conn.execute("DELETE FROM ms_sync_state WHERE chave='lab_sync_result'")
    assert 'rotina:lab_sync' not in _chaves()


def _grava_last_sync(horas_atras):
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
        conn.execute("DELETE FROM ms_sync_state WHERE chave='last_sync'")
        conn.execute("INSERT INTO ms_sync_state (chave, valor, atualizado_em) VALUES ('last_sync', 'x', ?)",
                     (_utc(horas_atras),))


def _quebrou_em_expediente():
    orig = alerta._horario_comercial
    alerta._horario_comercial = lambda dt=None: True
    try:
        with get_db() as conn:
            return {a[0]: a for a in alerta._achados_quebrou(conn)}
    finally:
        alerta._horario_comercial = orig


# ── 3. sync do Planner: mede a volta do agendador, não demanda nova (07/10) ─
def test_sync_rodando_sem_demanda_nova_nao_alerta():
    # 07/10 08h09: a última demanda nova era de 14h antes, o agendador rodava a cada 15 min
    _grava_last_sync(0.25)
    with get_db() as conn:
        conn.execute("INSERT INTO eventos (tipo, descricao, criado_em) VALUES ('demanda_criada_planner', 't', ?)", (_utc(14),))
    assert 'sync_parado' not in _quebrou_em_expediente()


def test_sync_sem_rodar_alerta():
    _grava_last_sync(3)
    achado = _quebrou_em_expediente().get('sync_parado')
    assert achado and 'sem rodar há 3h' in achado[2]


# ── 4. silêncio do medidor conta só depois das 8h ─────────────────────────
def test_madrugada_nao_conta_como_silencio():
    agora = datetime(2026, 10, 7, 8, 9, tzinfo=alerta.BRT)
    ultimo_acesso_4h = (agora - timedelta(hours=4, minutes=9)).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    horas = alerta._horas_no_expediente(ultimo_acesso_4h, agora=agora)
    assert horas is not None and horas < 0.2


def test_silencio_no_meio_do_dia_conta_inteiro():
    agora = datetime(2026, 10, 7, 15, 0, tzinfo=alerta.BRT)
    ultimo_11h = (agora - timedelta(hours=4)).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    assert alerta._horas_no_expediente(ultimo_11h, agora=agora) >= 3.9


def test_rotina_parada_entra_no_quebrou_do_alerta():
    _grava_lab_sync(30)
    with get_db() as conn:
        achados = alerta._achados_quebrou(conn)
    titulo = next(t for c, n, t, d, v in achados if c == 'rotina:lab_sync')
    assert 'sem gravar há 30h' in titulo
