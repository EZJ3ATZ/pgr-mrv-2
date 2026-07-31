# -*- coding: utf-8 -*-
"""Data da coleta que o laudo declara → amostradores.data_medicao.

O PDF do laudo traz a data de amostragem em 100% dos 77 registros lidos, mas 73
desses amostradores estavam sem `data_medicao`. Sem a ponta inicial não há como
medir coleta → envio → resultado: o ciclo completo existia em 6 de 487 (1,2%).

Também trava a normalização de '' → NULL: `COUNT(data_medicao)` reportava 364
preenchidas havendo 69 reais, porque 295 eram string vazia.
"""
import pytest

from controle.db import get_db, init_db, row_to_dict
from controle.lab_inbox import (
    _iso_br, _upsert_ra_laudo, sincronizar_data_medicao_dos_laudos,
    normalizar_datas_vazias, _ensure_ra_laudos,
)


# ── conversão de formato ───────────────────────────────────────────────

def test_iso_br_converte():
    # O PDF traz BR; as colunas guardam ISO. Misturar quebra todo prazo.
    assert _iso_br('30/06/2026') == '2026-06-30'
    assert _iso_br(' 12/06/2026 ') == '2026-06-12'


def test_iso_br_rejeita_lixo():
    assert _iso_br('') == ''
    assert _iso_br(None) == ''
    assert _iso_br('2026-06-30') == ''      # já ISO, não é entrada esperada
    assert _iso_br('30/06/26') == ''
    assert _iso_br('31/02/2026') == ''      # data que não existe


# ── propagação ─────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    init_db()
    with get_db() as c:
        _ensure_ra_laudos(c)
        c.execute("DELETE FROM ra_laudos WHERE amostrador_id < 0")
        c.execute("DELETE FROM amostradores WHERE id < 0")
        yield c
        c.execute("DELETE FROM ra_laudos WHERE amostrador_id < 0")
        c.execute("DELETE FROM amostradores WHERE id < 0")


def _amostrador(c, id_, codigo, data_medicao=None):
    c.execute("INSERT INTO amostradores (id, codigo, tipo, status, data_medicao, arquivado)"
              " VALUES (?,?,?,?,?,0)", (id_, codigo, 'TCP', 'laboratorio', data_medicao))


def _laudo(c, aid, cod, ra, data_amostragem):
    _upsert_ra_laudo(c, aid, cod, {'subject': f'RA {ra}', 'data': '2026-07-29'},
                     {'ra_num': ra, 'data_amostragem': data_amostragem})


def _data(c, id_):
    r = c.execute("SELECT data_medicao FROM amostradores WHERE id=?", (id_,)).fetchone()
    return row_to_dict(r).get('data_medicao')


def test_upsert_do_laudo_ja_preenche_a_data(conn):
    _amostrador(conn, -200, 'DL01', data_medicao='')
    _laudo(conn, -200, 'DL01', '81900001', '30/06/2026')
    assert str(_data(conn, -200))[:10] == '2026-06-30'


def test_nao_sobrescreve_data_que_o_tecnico_lancou(conn):
    _amostrador(conn, -201, 'DL02', data_medicao='2026-06-01')
    _laudo(conn, -201, 'DL02', '81900002', '30/06/2026')
    assert str(_data(conn, -201))[:10] == '2026-06-01'   # o lançado vence


def test_laudo_sem_data_nao_mexe(conn):
    _amostrador(conn, -202, 'DL03', data_medicao='')
    _laudo(conn, -202, 'DL03', '81900003', '')
    assert (_data(conn, -202) or '') == ''


def test_retroativo_preenche_laudo_ja_gravado(conn):
    # Simula o estado real: laudo em ra_laudos, amostrador sem data.
    _amostrador(conn, -203, 'DL04', data_medicao='2026-06-15')
    _laudo(conn, -203, 'DL04', '81900004', '10/06/2026')
    conn.execute("UPDATE amostradores SET data_medicao='' WHERE id=-203")
    assert sincronizar_data_medicao_dos_laudos(conn) >= 1
    assert str(_data(conn, -203))[:10] == '2026-06-10'


def test_varios_laudos_vale_a_amostragem_mais_antiga(conn):
    _amostrador(conn, -204, 'DL05', data_medicao='2026-01-01')
    _laudo(conn, -204, 'DL05', '81900005', '20/06/2026')
    _laudo(conn, -204, 'DL05', '81900006', '05/06/2026')
    conn.execute("UPDATE amostradores SET data_medicao='' WHERE id=-204")
    sincronizar_data_medicao_dos_laudos(conn)
    assert str(_data(conn, -204))[:10] == '2026-06-05'   # a mais antiga


# ── normalização de string vazia ───────────────────────────────────────

def test_normaliza_vazia_para_null(conn):
    _amostrador(conn, -205, 'DL06', data_medicao='')
    antes = conn.execute("SELECT COUNT(data_medicao) c FROM amostradores WHERE id=-205").fetchone()
    assert row_to_dict(antes)['c'] == 1          # COUNT conta '' como valor
    normalizar_datas_vazias(conn)
    depois = conn.execute("SELECT COUNT(data_medicao) c FROM amostradores WHERE id=-205").fetchone()
    assert row_to_dict(depois)['c'] == 0         # agora COUNT diz a verdade


def test_normalizar_nao_apaga_data_boa(conn):
    _amostrador(conn, -206, 'DL07', data_medicao='2026-06-30')
    normalizar_datas_vazias(conn)
    assert str(_data(conn, -206))[:10] == '2026-06-30'
