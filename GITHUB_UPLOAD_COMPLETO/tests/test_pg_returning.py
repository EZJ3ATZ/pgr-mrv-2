# -*- coding: utf-8 -*-
"""Prova o rewrite de SQL do wrapper PostgreSQL SEM precisar de banco.

Por que existe: o `_PGCursor.execute` anexa ` RETURNING id` em todo INSERT para
emular o `lastrowid` do sqlite3. Em tabela cuja PK NAO e `id`, isso levanta
`column "id" does not exist` e **aborta a transacao inteira**. Aconteceu 2x:
com RA_LAUDOS (backfill de RA desfazia conclusoes) e com SCHEMA_VERSAO em
13/08/2026 (derrubou o init_db todo).

O teste local com SQLite NAO pega esse defeito — o wrapper do PG nem entra no
caminho. Este roda o rewrite com um cursor de mentira.

    py tests\\test_pg_returning.py
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# DSN de mentira: liga o modo PG (importa psycopg2) sem nunca conectar
os.environ['DATABASE_URL'] = 'postgresql://ninguem:nada@localhost:1/vazio'

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import controle.db as db                       # noqa: E402

ok, falhas = 0, []


def checa(nome, cond, det=''):
    global ok
    if cond:
        ok += 1
        print(f'  OK    {nome}')
    else:
        falhas.append(f'{nome} :: {det}')
        print(f'  FALHA {nome} :: {det}')


class _CursorFalso:
    def __init__(self):
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {}


class _ConnFalso:
    def __init__(self):
        self.ultimo = None

    def cursor(self, *a, **kw):
        self.ultimo = _CursorFalso()
        return self.ultimo


def sql_enviado(sql, params=None):
    conn = _ConnFalso()
    db._PGCursor(conn).execute(sql, params)
    return conn.ultimo.sql


print('== tabelas com PK textual NAO podem receber RETURNING id ==')
for tabela in ('schema_versao', 'ms_users', 'ms_sync_state', 'ra_laudos'):
    s = sql_enviado(f"INSERT INTO {tabela} (a, b) VALUES (?, ?)", ('x', 'y'))
    checa(f'{tabela}: sem RETURNING id', 'RETURNING' not in s.upper(), s)

print('\n== tabela normal CONTINUA recebendo (o lastrowid depende disso) ==')
s = sql_enviado("INSERT INTO demandas (titulo) VALUES (?)", ('x',))
checa('demandas: com RETURNING id', s.upper().rstrip().endswith('RETURNING ID'), s)

print('\n== upsert e RETURNING explicito ficam intactos ==')
s = sql_enviado("INSERT INTO eventos (tipo) VALUES (?) ON CONFLICT DO NOTHING", ('x',))
checa('ON CONFLICT: sem RETURNING', 'RETURNING' not in s.upper(), s)
s = sql_enviado("INSERT INTO demandas (titulo) VALUES (?) RETURNING id", ('x',))
checa('RETURNING explicito nao duplica', s.upper().count('RETURNING') == 1, s)

print('\n== o ? virou %s e o % literal foi escapado ==')
s = sql_enviado("SELECT * FROM empresas WHERE nome LIKE ?", ('%belgo%',))
checa('placeholder convertido', '%s' in s and '?' not in s, s)

print('\n== a marca do schema usa exatamente este INSERT ==')
s = sql_enviado("INSERT INTO schema_versao (chave, valor, atualizado_em) "
                "VALUES ('ddl', ?, CURRENT_TIMESTAMP)", ('abc123',))
checa('INSERT real do init_db passa limpo', 'RETURNING' not in s.upper(), s)

print(f'\n==== {ok} OK, {len(falhas)} falha(s) ====')
for f in falhas:
    print('  !!', f)
sys.exit(1 if falhas else 0)
