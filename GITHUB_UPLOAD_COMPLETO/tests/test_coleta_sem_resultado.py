# -*- coding: utf-8 -*-
"""Alerta 'Coleta sem resultado': só químico, e pelo resultado do tubo.

06/10/2026: 57 alertas abertos, todos falsos. A regra procurava o status
'concluido' e a coleta grava 'concluida'; ruído (que não vai ao laboratório)
também entrava. O Helbert recebeu "Ruído — Almaq ... há mais de 45 dias sem
resultado" em 09/09. E o alerta não fechava sozinho.
"""
from datetime import date, timedelta

from controle.consistencia import (detectar_coletas_sem_resultado,
                                   fechar_divergencias_superadas,
                                   salvar_divergencias)
from controle.db import get_db, init_db

VELHA = (date.today() - timedelta(days=60)).isoformat()
NOVA = (date.today() - timedelta(days=5)).isoformat()
EMP = 'EMPRESA CSR TESTE'


def _limpa():
    with get_db() as conn:
        ids = [dict(r)['id'] for r in conn.execute(
            'SELECT id FROM coletas_quimico WHERE empresa_nome=?', (EMP,)).fetchall()]
        for i in ids:
            conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (i,))
        conn.execute('DELETE FROM coletas_quimico WHERE empresa_nome=?', (EMP,))
        conn.execute('DELETE FROM coletas_ruido WHERE empresa_nome=?', (EMP,))
        conn.execute("DELETE FROM amostradores WHERE codigo LIKE 'CSR%'")
        conn.execute("DELETE FROM resultados_lab WHERE amostrador_cod LIKE 'CSR%'")
        conn.execute("DELETE FROM divergencias WHERE tipo='coleta_sem_resultado'")


def setup_function(_):
    init_db()
    _limpa()


def teardown_function(_):
    _limpa()


def _coleta_quimico(data, tubo, status='concluida'):
    with get_db() as conn:
        cid = conn.execute(
            'INSERT INTO coletas_quimico (empresa_nome, data_coleta, status) VALUES (?, ?, ?)',
            (EMP, data, status)).lastrowid
        conn.execute(
            'INSERT INTO coletas_quimico_amostr (coleta_id, seq, id_amostrador) VALUES (?, 1, ?)',
            (cid, tubo))
    return cid


def _tubo(codigo, data_resultado=''):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, data_resultado, arquivado) "
            "VALUES (?, 'PVC', 'laboratorio', ?, 0)", (codigo, data_resultado))


def _ids(alertas):
    return {a['entidade_id'] for a in alertas if a['entidade_tipo'] == 'coletas_quimico'}


def test_ruido_concluido_nao_vira_alerta():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO coletas_ruido (empresa_nome, data_coleta, status) VALUES (?, ?, 'concluida')",
            (EMP, VELHA))
    alertas = detectar_coletas_sem_resultado(45)
    assert not [a for a in alertas if a['entidade_tipo'] == 'coletas_ruido']


def test_quimico_com_resultado_no_tubo_nao_alerta():
    _tubo('CSR01', data_resultado=NOVA)
    cid = _coleta_quimico(VELHA, 'CSR01')
    assert cid not in _ids(detectar_coletas_sem_resultado(45))


def test_quimico_com_resultado_em_resultados_lab_nao_alerta():
    _tubo('CSR02')
    with get_db() as conn:
        conn.execute("INSERT INTO resultados_lab (amostrador_cod, ra_num, fonte) VALUES ('CSR02', '1', 'teste')")
    cid = _coleta_quimico(VELHA, 'csr02')       # caixa diferente casa igual
    assert cid not in _ids(detectar_coletas_sem_resultado(45))


def test_quimico_sem_resultado_alerta_e_diz_o_tubo():
    _tubo('CSR03')
    cid = _coleta_quimico(VELHA, 'CSR03')
    fora = _coleta_quimico(VELHA, 'CSR99')      # tubo que não existe no inventário
    recente = _coleta_quimico(NOVA, 'CSR03')
    alertas = detectar_coletas_sem_resultado(45)
    assert {cid, fora} <= _ids(alertas)
    assert recente not in _ids(alertas)
    desc = next(a['descricao'] for a in alertas if a['entidade_id'] == fora)
    assert 'CSR99 (fora do inventário)' in desc


def test_alerta_fecha_sozinho_quando_o_resultado_chega():
    _tubo('CSR04')
    cid = _coleta_quimico(VELHA, 'CSR04')
    salvar_divergencias(detectar_coletas_sem_resultado(45))
    with get_db() as conn:
        conn.execute("UPDATE amostradores SET data_resultado=? WHERE codigo='CSR04'", (NOVA,))
    fechados = fechar_divergencias_superadas('coleta_sem_resultado',
                                             detectar_coletas_sem_resultado(45))
    assert fechados >= 1
    with get_db() as conn:
        st = dict(conn.execute(
            "SELECT status, resolvido_por FROM divergencias WHERE tipo='coleta_sem_resultado' "
            "AND entidade_id=?", (cid,)).fetchone())
    assert st == {'status': 'resolvida', 'resolvido_por': 'sistema'}
