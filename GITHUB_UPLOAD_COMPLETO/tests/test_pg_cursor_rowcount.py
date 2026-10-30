# -*- coding: utf-8 -*-
"""O cursor do Postgres tem de responder `rowcount`, como o sqlite3.

`_baixar_medicao_pendente` faz um UPDATE condicional e confere `cur.rowcount`
para saber se ganhou a corrida. Em produção o `_PGCursor` não tinha o atributo:
a baixa estourava DENTRO da transação e era desfeita, sem aviso na tela. Em
30/10/2026 o Helbert finalizou as 4 planilhas da OS LIP-161026-002 (Pacto) e
nenhuma das 4 medições baixou ("'_PGCursor' object has no attribute 'rowcount'"
no log). Os testes rodam em SQLite e não passam pelo wrapper do Postgres, por
isso o defeito não aparecia aqui.
"""
from controle.db import _PGCursor


class _CursorFalso:
    rowcount = 1


def test_cursor_do_postgres_responde_rowcount():
    c = _PGCursor(pg_conn=None)
    assert c.rowcount == -1          # antes de executar, como o sqlite3
    c._cur = _CursorFalso()
    assert c.rowcount == 1
