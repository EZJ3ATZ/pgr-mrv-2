# -*- coding: utf-8 -*-
"""No Postgres a normalização de datas do RA não pode nem TENTAR o rowid.

De 10/09 a 06/10/2026 o sync do laboratório (a cada 3h) não gravou nada: o
`SELECT rowid` falhava no Postgres, o except tentava a consulta certa, mas a
transação já estava abortada — e o rollback no fim desfazia status, envio,
resultado e alertas que o sync tinha acabado de calcular. A suíte roda em
SQLite, que tem rowid, então o defeito só existia em produção.
"""
import controle.lab_inbox as lab


class _Cursor:
    rowcount = 0

    def fetchall(self):
        return []


class _ConnQueRegistra:
    def __init__(self):
        self.sqls = []

    def execute(self, sql, params=()):
        self.sqls.append(sql)      # registra a TENTATIVA, mesmo a que falha
        if 'rowid' in sql.lower() and lab.USE_PG:
            raise RuntimeError('column "rowid" does not exist')
        return _Cursor()


def test_postgres_nao_pergunta_rowid(monkeypatch):
    monkeypatch.setattr(lab, 'USE_PG', True)
    conn = _ConnQueRegistra()
    lab.normalizar_datas_ra_laudos(conn)
    lidas = [s for s in conn.sqls if s.lstrip().upper().startswith('SELECT')]
    assert len(lidas) == len(lab._COLS_DATA_RA_LAUDO)
    assert all('rowid' not in s.lower() for s in conn.sqls)
    assert all('amostrador_cod, ra_num' in s for s in lidas)


def test_sqlite_segue_usando_rowid(monkeypatch):
    monkeypatch.setattr(lab, 'USE_PG', False)
    conn = _ConnQueRegistra()
    lab.normalizar_datas_ra_laudos(conn)
    lidas = [s for s in conn.sqls if s.lstrip().upper().startswith('SELECT')]
    assert lidas and all('rowid' in s.lower() for s in lidas)
