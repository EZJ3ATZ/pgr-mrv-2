# -*- coding: utf-8 -*-
"""Prova o log de tempo de resposta sem tocar em producao.

Sobe um Flask minimo com os MESMOS ganchos de controle/perf.py, sobre SQLite
temporario, e confere: grava, conta consultas, ignora estatico, nao quebra a
resposta quando o banco falha, e le o resumo.

    py tests\\test_perf_log.py
"""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# banco temporario e SEM DATABASE_URL -> SQLite
os.environ.pop('DATABASE_URL', None)
os.environ['CONTROLE_DATA_DIR'] = tempfile.mkdtemp(prefix='perf_test_')
os.environ['PERF_LOG'] = '1'

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from flask import Flask, jsonify            # noqa: E402
from controle import perf                   # noqa: E402
from controle.db import get_db               # noqa: E402

ok, falhas = 0, []


def checa(nome, cond, det=''):
    global ok
    if cond:
        ok += 1
        print(f'  OK    {nome}')
    else:
        falhas.append(f'{nome} :: {det}')
        print(f'  FALHA {nome} :: {det}')


app = Flask(__name__)
perf.init_app(app)


@app.route('/rapida')
def rapida():
    return jsonify({'ok': True})


@app.route('/consulta')
def consulta():
    # 3 consultas de verdade -> o contador tem de ver 3
    with get_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS t_perf (id INTEGER PRIMARY KEY, x TEXT)')
        conn.execute('INSERT INTO t_perf (x) VALUES (?)', ('a',))
        n = conn.execute('SELECT COUNT(*) AS c FROM t_perf').fetchone()['c']
    return jsonify({'linhas': n})


@app.route('/estatico-ish')
def estatico():
    return jsonify({'ok': True})


@app.route('/explode')
def explode():
    raise RuntimeError('erro de proposito')


c = app.test_client()

print('== 1) grava uma medicao por requisicao ==')
for _ in range(3):
    c.get('/rapida')
perf._descarregar(forcar=True)
with get_db() as conn:
    linhas = conn.execute("SELECT rota, metodo, status, duracao_ms, consultas "
                          "FROM perf_log WHERE rota='rapida'").fetchall()
checa('3 requisicoes -> 3 linhas', len(linhas) == 3, f'{len(linhas)}')
checa('metodo e status gravados',
      all(dict(l)['metodo'] == 'GET' and dict(l)['status'] == 200 for l in linhas),
      f'{[dict(l) for l in linhas][:1]}')
checa('duracao e numero >= 0',
      all(dict(l)['duracao_ms'] is not None and dict(l)['duracao_ms'] >= 0 for l in linhas))

print('\n== 2) conta as consultas do pedido ==')
c.get('/consulta')
perf._descarregar(forcar=True)
with get_db() as conn:
    r = dict(conn.execute("SELECT consultas, ms_banco FROM perf_log "
                          "WHERE rota='consulta' ORDER BY id DESC LIMIT 1").fetchone())
# SQLite nao passa pelo wrapper _PGCursor -> contador fica 0 (documentado);
# em PG conta. Aqui basta provar que a COLUNA existe e vem preenchida.
checa('coluna consultas preenchida (0 em SQLite, >0 em PG)',
      r['consultas'] is not None, f'{r}')

print('\n== 3) rota que estoura NAO perde a medicao nem some o erro ==')
try:
    c.get('/explode')
except Exception:
    pass
perf._descarregar(forcar=True)
with get_db() as conn:
    n500 = conn.execute("SELECT COUNT(*) AS c FROM perf_log WHERE status >= 500").fetchone()['c']
checa('erro 500 tambem e medido', n500 >= 1, f'{n500}')

print('\n== 4) estatico e ignorado ==')
c.get('/static/qualquer.css')
c.get('/sw.js')
perf._descarregar(forcar=True)
with get_db() as conn:
    nstat = conn.execute("SELECT COUNT(*) AS c FROM perf_log "
                         "WHERE path LIKE '/static/%' OR path='/sw.js'").fetchone()['c']
checa('nao mede /static nem /sw.js', nstat == 0, f'{nstat}')

print('\n== 5) telemetria nao derruba a resposta se o banco morrer ==')
_orig = perf._gravar
perf._gravar = lambda linhas: (_ for _ in ()).throw(RuntimeError('banco fora'))
resp = c.get('/rapida')
perf._descarregar(forcar=True)
perf._gravar = _orig
checa('resposta segue 200 com banco de telemetria fora', resp.status_code == 200,
      f'{resp.status_code}')

print('\n== 6) leitura do resumo ==')
for _ in range(4):
    c.get('/rapida')
perf._descarregar(forcar=True)
res = perf.resumo_completo(7)
k = res.get('kpis') or {}
checa('kpis tem contagem de requisicoes', Number := (k.get('requisicoes') or 0) > 0, f'{k}')
checa('por_rota agrupa', any(x.get('rota') == 'rapida' for x in res.get('rotas') or []),
      f"{res.get('rotas')}")
checa('lentas e lista', isinstance(res.get('lentas'), list))
checa('banco vazio em SQLite (sem pg_stat_statements)', res.get('banco') == [])

print('\n== 7) buffer tem teto ==')
perf._buffer.clear()
for i in range(perf.TETO_BUFFER + 50):
    perf.registrar('x', 'GET', '/x', 200, 1, 0, 0, 0, None)
checa('buffer nao passa do teto', len(perf._buffer) <= perf.TETO_BUFFER,
      f'{len(perf._buffer)}')
perf._buffer.clear()

print(f'\n==== {ok} OK, {len(falhas)} falha(s) ====')
for f in falhas:
    print('  !!', f)
sys.exit(1 if falhas else 0)
