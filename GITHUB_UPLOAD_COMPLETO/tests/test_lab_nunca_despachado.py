# -*- coding: utf-8 -*-
"""Alerta de amostrador coletado que nunca foi despachado ao laboratório.

Ponto cego achado em 30/07/2026: `_alertar_resultados_atrasados` exige
`data_envio_lab` preenchida, então quem saiu de campo e nunca teve o despacho
registrado ficava invisível — eram 7 dos 8 em `status='laboratorio'`, dois
parados havia 66 dias sem aviso nenhum.

A data não é carimbada na coleta de propósito (routes.py:1808, fix 03/07/2026);
este alerta vigia o intervalo em vez de preencher a data.
"""
import pytest

from controle.db import get_db, init_db
from controle.lab_inbox import _alertar_nunca_despachados, _alertar_resultados_atrasados


def _limpar(conn):
    conn.execute("DELETE FROM eventos WHERE ref_tipo='amostrador' AND ref_id < 0")
    conn.execute("DELETE FROM amostradores WHERE id < 0")


def _inserir(conn, id_, codigo, status='laboratorio', data_medicao=None,
             data_envio_lab=None, data_resultado=None, atualizado_em='2026-01-01'):
    conn.execute(
        "INSERT INTO amostradores (id, codigo, tipo, status, data_medicao, data_envio_lab,"
        " data_resultado, atualizado_em, arquivado) VALUES (?,?,?,?,?,?,?,?,0)",
        (id_, codigo, 'TCP', status, data_medicao, data_envio_lab, data_resultado,
         atualizado_em))


@pytest.fixture
def conn():
    """O banco de teste vem semeado com centenas de amostradores reais, então os
    testes olham o EVENTO do id negativo que inserem, não o total do alerta."""
    init_db()
    with get_db() as c:
        _limpar(c)
        _alertar_nunca_despachados(c, dias=7)   # drena o baseline do seed
        yield c
        _limpar(c)


def _alertou(conn, ref_id):
    r = conn.execute("SELECT descricao FROM eventos WHERE tipo='lab_nunca_despachado' "
                     "AND ref_id=?", (ref_id,)).fetchone()
    return r['descricao'] if r else None


def test_alerta_quem_ficou_alem_do_limite(conn):
    _inserir(conn, -1, 'VELHO01', data_medicao='2026-01-10')
    _alertar_nunca_despachados(conn, dias=7)
    d = _alertou(conn, -1)
    assert d and 'VELHO01' in d and 'nunca foi registrado' in d


def test_nao_alerta_coleta_recente(conn):
    from datetime import date
    _inserir(conn, -2, 'NOVO01', data_medicao=date.today().isoformat())
    _alertar_nunca_despachados(conn, dias=7)
    assert _alertou(conn, -2) is None


def test_nao_repete_alerta_para_o_mesmo_amostrador(conn):
    _inserir(conn, -3, 'VELHO02', data_medicao='2026-01-10')
    _alertar_nunca_despachados(conn, dias=7)
    n1 = conn.execute("SELECT COUNT(*) c FROM eventos WHERE ref_id=-3").fetchone()['c']
    _alertar_nunca_despachados(conn, dias=7)
    n2 = conn.execute("SELECT COUNT(*) c FROM eventos WHERE ref_id=-3").fetchone()['c']
    assert n1 == 1 and n2 == 1          # 1x por amostrador


def test_ignora_quem_ja_tem_data_de_envio(conn):
    # Território do _alertar_resultados_atrasados — não pode alertar duas vezes.
    _inserir(conn, -4, 'ENVIADO01', data_medicao='2026-01-10', data_envio_lab='2026-01-12')
    _alertar_nunca_despachados(conn, dias=7)
    assert _alertou(conn, -4) is None


def test_ignora_quem_ja_tem_resultado(conn):
    _inserir(conn, -5, 'PRONTO01', data_medicao='2026-01-10', data_resultado='2026-02-01')
    _alertar_nunca_despachados(conn, dias=7)
    assert _alertou(conn, -5) is None


def test_sem_data_de_medicao_cai_para_atualizado_em(conn):
    # Caso real dos amostradores 2793 e 15975: sem data_medicao, parados há 66 dias.
    _inserir(conn, -6, 'SEMDATA01', data_medicao=None, atualizado_em='2026-01-05')
    _alertar_nunca_despachados(conn, dias=7)
    d = _alertou(conn, -6)
    assert d and 'sem data de medição' in d


def test_os_dois_alertas_nao_se_sobrepoem(conn):
    """Cada amostrador é responsabilidade de exatamente um dos dois alertas."""
    _inserir(conn, -7, 'SEMENVIO', data_medicao='2026-01-10')                   # → nunca despachado
    _inserir(conn, -8, 'NOLAB',    data_medicao='2026-01-10',
             data_envio_lab='2026-01-11')                                       # → atrasado
    _alertar_nunca_despachados(conn, dias=7)
    _alertar_resultados_atrasados(conn, dias=7)
    tipos = {r['ref_id']: r['tipo'] for r in conn.execute(
        "SELECT ref_id, tipo FROM eventos WHERE ref_id IN (-7,-8)").fetchall()}
    assert tipos == {-7: 'lab_nunca_despachado', -8: 'lab_resultado_atrasado'}


def test_so_olha_status_laboratorio(conn):
    _inserir(conn, -9, 'DISPON01', status='disponivel', data_medicao='2026-01-10')
    _alertar_nunca_despachados(conn, dias=7)
    assert _alertou(conn, -9) is None
