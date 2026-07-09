# -*- coding: utf-8 -*-
"""_savepoint (lab_inbox): falha de um statement não pode derrubar a transação.

Regressão do backfill de RAs em produção (09/07): um laudo com erro abortava
a transação Postgres inteira, os laudos seguintes falhavam em cascata e o
rollback final desfazia até as conclusões que tinham dado certo.
"""
import pytest

from controle.db import get_db
from controle.lab_inbox import _savepoint


def test_falha_dentro_do_savepoint_nao_derruba_o_resto():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t_sp (a TEXT)")
        conn.execute("DELETE FROM t_sp")
        conn.execute("INSERT INTO t_sp (a) VALUES ('antes')")

        with pytest.raises(Exception):
            _savepoint(conn, lambda: conn.execute(
                "INSERT INTO tabela_que_nao_existe (x) VALUES (1)"))

        # A transação continua utilizável após a falha isolada
        conn.execute("INSERT INTO t_sp (a) VALUES ('depois')")
        vals = {r['a'] for r in conn.execute("SELECT a FROM t_sp").fetchall()}

    assert vals == {'antes', 'depois'}


def test_savepoint_devolve_o_retorno_da_funcao():
    with get_db() as conn:
        assert _savepoint(conn, lambda: 42) == 42


def test_savepoint_com_sucesso_persiste_a_escrita():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t_sp2 (a TEXT)")
        conn.execute("DELETE FROM t_sp2")
        _savepoint(conn, lambda: conn.execute("INSERT INTO t_sp2 (a) VALUES ('ok')"))
        vals = [r['a'] for r in conn.execute("SELECT a FROM t_sp2").fetchall()]
    assert vals == ['ok']
