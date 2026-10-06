# -*- coding: utf-8 -*-
"""Cadeia de custódia cadastra na hora o tubo da coleta que não está no estoque.

06/10/2026: em 30 dias nenhuma cadeia foi gerada pelo sistema. A tela parava em
"cadastre em Amostradores e gere de novo" e o técnico voltava para a planilha.
"""
import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

COD = ('TCP9301CT', 'PVC9402CT', 'TCPCT03AV1')


@pytest.fixture(autouse=True)
def _limpa():
    init_db()
    with get_db() as conn:
        for c in COD:
            conn.execute('DELETE FROM amostradores WHERE codigo=?', (c,))
    yield
    with get_db() as conn:
        for c in COD:
            conn.execute('DELETE FROM amostradores WHERE codigo=?', (c,))


def _post(codigos):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        return cli.post('/controle/cadeia-custodia/cadastrar-tubos', json={'codigos': codigos})


def test_cadastra_o_que_falta_e_devolve_os_ids():
    with get_db() as conn:
        conn.execute("INSERT INTO amostradores (codigo, tipo, status, arquivado) VALUES ('TCPCT03AV1','TCP','disponivel',0)")
        existente = conn.execute("SELECT id FROM amostradores WHERE codigo='TCPCT03AV1'").fetchone()['id']
    r = _post(['pvc9402ct', 'TCPCT03AV1'])
    j = r.get_json()
    assert r.status_code == 200 and j['ok'], j
    assert j['criados'] == ['PVC9402CT']
    assert j['ids']['TCPCT03AV1'] == existente
    with get_db() as conn:
        novo = row_to_dict(conn.execute("SELECT tipo, status, observacao FROM amostradores WHERE codigo='PVC9402CT'").fetchone())
    assert novo['tipo'] == 'PVC' and novo['status'] == 'disponivel' and 'cadeia' in novo['observacao']


def test_arquivado_e_reativado_nao_duplicado():
    with get_db() as conn:
        conn.execute("INSERT INTO amostradores (codigo, tipo, status, arquivado) VALUES ('TCP9301CT','CC','concluido',1)")
    j = _post(['TCP9301CT']).get_json()
    assert j['reativados'] == ['TCP9301CT'] and not j['criados']
    with get_db() as conn:
        rows = [row_to_dict(x) for x in conn.execute(
            "SELECT arquivado FROM amostradores WHERE codigo='TCP9301CT'").fetchall()]
    assert len(rows) == 1 and int(rows[0]['arquivado'] or 0) == 0


def test_sem_codigo_400():
    assert _post([]).status_code == 400
