# -*- coding: utf-8 -*-
"""Ambiente de teste: nunca tocar o Postgres de produção nem ligar o scheduler.

Roda ANTES de qualquer import dos módulos do app (pytest carrega conftest primeiro),
então controle/db.py já nasce apontando para um SQLite temporário.
"""
import os
import sys
import tempfile

os.environ['DISABLE_SCHEDULER'] = '1'
os.environ.pop('DATABASE_URL', None)
os.environ['CONTROLE_DATA_DIR'] = tempfile.mkdtemp(prefix='sst-tests-')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Estes nasceram como script (`py tests\\test_perf_log.py`) e chamam sys.exit()
# no corpo do modulo: coletados direto, pytest morre com INTERNALERROR na COLETA
# e os outros 356 testes nunca rodam. Pior, cada um mexe em os.environ no import
# (test_pg_returning liga o modo PostgreSQL) e contaminaria o controle.db
# compartilhado da sessao. Rodam em processo proprio, via
# tests/test_scripts_isolados.py, que le esta lista.
SCRIPTS_ISOLADOS = [
    'test_perf_log.py',
    'test_pg_returning.py',
    'test_schema_versao.py',
    'test_uso_por_pessoa.py',
    'test_alerta.py',
]

collect_ignore = list(SCRIPTS_ISOLADOS)
