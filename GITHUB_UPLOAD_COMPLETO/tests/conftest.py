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
