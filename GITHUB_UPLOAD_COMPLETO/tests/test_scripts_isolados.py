# -*- coding: utf-8 -*-
"""Roda os testes-script em processo proprio — e o que faz `py -m pytest` valer de gate.

Quatro testes nasceram como script e terminam com sys.exit() no corpo do modulo.
Coletados direto, pytest nem chegava a rodar: morria com INTERNALERROR na COLETA
e a suite inteira (356 testes) ficava invisivel. Cada um tambem mexe em
os.environ no import — o test_pg_returning liga o modo PostgreSQL — entao nem
convertidos em assert poderiam dividir processo com os outros: envenenariam o
controle.db da sessao, que o conftest aponta de proposito para SQLite temporario.

Aqui cada um roda isolado, com ambiente proprio. Exit code 0 = passou; a saida
do script so aparece quando falha. Continuam funcionando a mao, do jeito que o
docstring de cada um manda:

    py tests\\test_perf_log.py

A lista mora no conftest (`SCRIPTS_ISOLADOS`), que e quem os tira da coleta —
fonte unica, para a lista nao divergir do collect_ignore.
"""
import os
import subprocess
import sys

import pytest

from conftest import SCRIPTS_ISOLADOS

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)


@pytest.mark.parametrize('script', SCRIPTS_ISOLADOS)
def test_script_passa_em_processo_proprio(script):
    caminho = os.path.join(AQUI, script)
    assert os.path.exists(caminho), f'{script} sumiu — tirar do SCRIPTS_ISOLADOS'

    # ambiente limpo: cada script escolhe o proprio DATABASE_URL / data dir
    env = dict(os.environ)
    env.pop('DATABASE_URL', None)
    env.pop('CONTROLE_DATA_DIR', None)
    env['PYTHONIOENCODING'] = 'utf-8'

    r = subprocess.run([sys.executable, caminho], cwd=RAIZ, env=env,
                       capture_output=True, timeout=600)
    saida = (r.stdout + r.stderr).decode('utf-8', 'replace')
    assert r.returncode == 0, f'{script} falhou (exit {r.returncode}):\n{saida}'
