# -*- coding: utf-8 -*-
"""_sync_reserva_plano: reserva de amostrador não pode sofrer double-booking.

Regressão da auditoria de segurança (09/07): check-then-act (TOCTOU/CWE-362)
— o SELECT via 'disponivel', outro plano confirmava no meio, e o UPDATE
incondicional sobrescrevia a reserva do primeiro (double-booking; provável
causa-raiz da "planilha em branco"). O UPDATE agora re-afirma o status no
WHERE e rowcount 0 vira conflito.
"""
from controle import routes as routes_mod
from controle.db import get_db, init_db, row_to_dict
from controle.routes import _sync_reserva_plano


def _seed(codigo, status='disponivel', dono=None):
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo=?", (codigo,))
        conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, reservado_por_plano) "
            "VALUES (?, 'tubo', ?, ?)", (codigo, status, dono))
        row = conn.execute(
            "SELECT id FROM amostradores WHERE codigo=?", (codigo,)).fetchone()
        return row_to_dict(row)['id']


def _estado(aid):
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, reservado_por_plano FROM amostradores WHERE id=?",
            (aid,)).fetchone()
        return row_to_dict(row)


def test_disponivel_e_reservado_pelo_plano():
    aid = _seed('RACET01')
    res = _sync_reserva_plano(501, [{'amostrador_codigo': 'RACET01'}])
    assert res['reservados'] and not res['conflitos']
    st = _estado(aid)
    assert st['status'] == 'reservado' and int(st['reservado_por_plano']) == 501


def test_reservado_por_outro_plano_e_conflito_sem_sobrescrever():
    aid = _seed('RACET02', status='reservado', dono=111)
    res = _sync_reserva_plano(502, [{'amostrador_codigo': 'RACET02'}])
    assert res['conflitos'] and not res['reservados']
    assert int(_estado(aid)['reservado_por_plano']) == 111


def test_corrida_apos_o_select_nao_sobrescreve(monkeypatch):
    """Simula o TOCTOU: o SELECT vê 'disponivel' (estado velho), mas na hora
    do UPDATE o banco já está reservado pelo plano 111. O UPDATE condicional
    não pode escrever — vira conflito e o dono original permanece."""
    aid = _seed('RACET03', status='reservado', dono=111)

    real = routes_mod.row_to_dict
    calls = {'n': 0}

    def stale_first(row):
        d = real(row)
        if d and d.get('codigo') == 'RACET03' and 'status' in d and calls['n'] == 0:
            calls['n'] += 1
            return {**d, 'status': 'disponivel', 'reservado_por_plano': None}
        return d

    monkeypatch.setattr(routes_mod, 'row_to_dict', stale_first)
    res = _sync_reserva_plano(503, [{'amostrador_codigo': 'RACET03'}])
    assert res['conflitos'] and not res['reservados']
    assert 'plano #111' in res['conflitos'][0]
    assert int(_estado(aid)['reservado_por_plano']) == 111


def test_reservado_manual_sem_dono_e_adotado():
    aid = _seed('RACET04', status='reservado', dono=None)
    res = _sync_reserva_plano(504, [{'amostrador_codigo': 'RACET04'}])
    assert res['reservados'] and not res['conflitos']
    st = _estado(aid)
    assert st['status'] == 'reservado' and int(st['reservado_por_plano']) == 504
