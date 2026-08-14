# -*- coding: utf-8 -*-
"""`?limit=-1` contornava o teto de resultados.

Todos os 10 usos de `_qint` são LIMIT. Em SQLite, `LIMIT -1` significa SEM LIMITE:
o teto de 200 em /empresas e de 500 em /eventos — que existe de propósito — caía
com um sinal de menos. No Postgres o mesmo valor é erro de sintaxe (HTTP 500).

Encontrado ao conferir os 38 `semgrep|formatted-sql-query` em 13/08/2026. Nenhum
dos 38 era injeção; este ficou no caminho.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import app as flask_app
from controle.routes import _qint


def _com_query(qs):
    with flask_app.test_request_context('/x?' + qs):
        return _qint('limit', 100, 200)


def test_limite_negativo_nao_vira_sem_limite():
    assert _com_query('limit=-1') == 1


def test_zero_tambem_e_barrado():
    assert _com_query('limit=0') == 1


def test_teto_continua_valendo():
    assert _com_query('limit=99999') == 200


def test_valor_normal_passa():
    assert _com_query('limit=50') == 50


def test_texto_cai_no_padrao():
    assert _com_query('limit=abc') == 100


def test_ausente_cai_no_padrao():
    assert _com_query('outra=1') == 100


def test_sem_teto_ainda_tem_piso():
    """`_qint('limit', 20)` (sem máximo) é usado no sync_log — o piso vale igual."""
    with flask_app.test_request_context('/x?limit=-5'):
        assert _qint('limit', 20) == 1
