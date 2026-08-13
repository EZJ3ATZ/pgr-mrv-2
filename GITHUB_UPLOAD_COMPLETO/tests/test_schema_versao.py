# -*- coding: utf-8 -*-
"""Prova o guarda de migration (schema_versao) sem tocar em producao.

O DDL rodava a cada boot do gunicorn; `CREATE INDEX IF NOT EXISTS` pega
ShareLock na tabela ANTES de ver que o indice existe, e travava as escritas de
planner_raw_tasks (54 min numa unica espera em prod). Agora so roda quando a
impressao digital do DDL muda. Este teste garante os 3 comportamentos:
migra na 1a vez, PULA na 2a, e volta a migrar quando o schema muda.

    py tests\\test_schema_versao.py
"""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ.pop('DATABASE_URL', None)          # SQLite temporario
os.environ['CONTROLE_DATA_DIR'] = tempfile.mkdtemp(prefix='schema_test_')

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


# conta quantas vezes o DDL pesado roda
vezes = []
_orig_migrate = db._migrate


def _espiao(conn):
    vezes.append(1)
    return _orig_migrate(conn)


db._migrate = _espiao

print('== 1) banco novo: migra ==')
db.init_db()
checa('rodou a migration', len(vezes) == 1, f'{len(vezes)}')
with db.get_db() as conn:
    r = conn.execute("SELECT valor FROM schema_versao WHERE chave='ddl'").fetchone()
    tag1 = dict(r)['valor'] if r else None
    tabelas = conn.execute(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table'").fetchone()['c']
    indices = conn.execute(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'idx_%'").fetchone()['c']
checa('gravou a impressao digital', bool(tag1), f'{tag1}')
checa('tabelas criadas de verdade', tabelas > 20, f'{tabelas} tabelas')
checa('indices criados de verdade', indices > 20, f'{indices} indices')

print('\n== 2) mesmo schema, novo processo: PULA ==')
db._db_ready = False
db.init_db()
checa('NAO rodou a migration de novo', len(vezes) == 1, f'{len(vezes)}')

print('\n== 3) schema mudou: volta a migrar sozinho ==')
db.SCHEMA_INDEXES += '\nCREATE INDEX IF NOT EXISTS idx_teste_guarda ON eventos(usuario);'
db._esquema_tag_cache = None
db._db_ready = False
db.init_db()
checa('rodou a migration apos mudanca', len(vezes) == 2, f'{len(vezes)}')
with db.get_db() as conn:
    r = conn.execute("SELECT valor FROM schema_versao WHERE chave='ddl'").fetchone()
    tag2 = dict(r)['valor'] if r else None
    novo = conn.execute("SELECT COUNT(*) AS c FROM sqlite_master "
                        "WHERE name='idx_teste_guarda'").fetchone()['c']
checa('impressao digital mudou', tag1 != tag2, f'{tag1} -> {tag2}')
checa('o indice novo foi criado', novo == 1, f'{novo}')

print('\n== 4) sem impressao digital, migra sempre (fail-open) ==')
db._esquema_tag_cache = ''
db._db_ready = False
antes = len(vezes)
db.init_db()
checa('sem tag -> migrou', len(vezes) == antes + 1, f'{antes} -> {len(vezes)}')

print('\n== 5) marcador apagado a mao forca a migration ==')
db._esquema_tag_cache = None
with db.get_db() as conn:
    conn.execute("DELETE FROM schema_versao WHERE chave='ddl'")
db._db_ready = False
antes = len(vezes)
db.init_db()
checa('apagar o marcador remigra', len(vezes) == antes + 1, f'{antes} -> {len(vezes)}')

print(f'\n==== {ok} OK, {len(falhas)} falha(s) ====')
for f in falhas:
    print('  !!', f)
sys.exit(1 if falhas else 0)
