# -*- coding: utf-8 -*-
"""'Parado há' de tubo no laboratório conta do envio quando não há medição.

Em produção (06/10/2026) 5 PVC enviados em 27/07 tinham data_entrada 26/08
(recadastro) e nenhuma data de medição: a tela dizia 41 dias no lab em vez
de 71, e o vermelho de atraso acendia um mês depois do devido.
"""
from datetime import date, timedelta

from controle.db import get_db, init_db, list_amostradores


def _dia(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _limpa():
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo LIKE 'TPLAB%'")


def setup_function(_):
    init_db()
    _limpa()


def teardown_function(_):
    _limpa()


def _seed(codigo, status, entrada, envio='', medicao=''):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, data_entrada, data_envio_lab, "
            "data_medicao, arquivado) VALUES (?, 'PVC', ?, ?, ?, ?, 0)",
            (codigo, status, entrada, envio, medicao))


def _parado(codigo):
    return next(a['tempo_parado'] for a in list_amostradores({}) if a['codigo'] == codigo)


def test_lab_sem_medicao_conta_do_envio():
    _seed('TPLAB01', 'laboratorio', entrada=_dia(41), envio=_dia(71))
    assert _parado('TPLAB01') == 71


def test_lab_com_medicao_segue_contando_da_medicao():
    _seed('TPLAB02', 'laboratorio', entrada=_dia(90), envio=_dia(20), medicao=_dia(25))
    assert _parado('TPLAB02') == 25


def test_estoque_continua_contando_da_entrada():
    # tubo na prateleira com envio velho (marca em bloco) não muda de régua
    _seed('TPLAB03', 'disponivel', entrada=_dia(60), envio=_dia(30))
    assert _parado('TPLAB03') == 60
