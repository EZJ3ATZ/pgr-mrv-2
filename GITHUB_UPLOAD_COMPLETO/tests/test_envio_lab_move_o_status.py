# -*- coding: utf-8 -*-
"""Despachar ao laboratório tem de mover o STATUS, não só gravar a data.

Medido em produção em 10/09/2026: **179 amostradores** tinham `data_envio_lab`
preenchida e nenhum `data_resultado`, mas só **16** estavam com
`status='laboratorio'`. Os outros 163 estavam invisíveis.

Invisíveis porque tudo que cobra o laboratório filtra por status, não pela data
de envio:

  - `lab_inbox.py` `_alertar_resultados_atrasados` — alerta de resultado atrasado
  - `lab_inbox.py` `_alertar_nunca_despachado`     — alerta de nunca despachado
  - `lab_inbox.py` — a fila de RAs e o `UPDATE ... WHERE id=? AND
    status='laboratorio'` que grava o resultado quando o RA chega

O último é o mais caro: com o status errado, **o resultado do laboratório não é
gravado no amostrador nem quando chega**.

A causa estava em `marcar_envio_lab`: gravava data, dias de validade e lote, e
não tocava no status. Quem movia o status era só a Cadeia de Custódia
(`cadeia_custodia.py`, `marcar_despacho`) — então todo despacho feito pelo botão
direto de envio deixava o tubo com data nova e estado velho. Média de 81 dias
parados, o mais antigo desde 28/01/2026.

Promove pela mesma regra que a Cadeia de Custódia já usava: só de `disponivel`
ou `reservado`. Tubo `concluido` ou `devolvido` não é rebaixado por um reenvio —
esses são histórico, e reabri-los aqui sujaria a fila com amostra que já fechou.
"""
import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

PREFIXO = 'ENVLAB'


def _cria(codigo, status):
    with get_db() as conn:
        conn.execute('DELETE FROM amostradores WHERE codigo=?', (codigo,))
        cur = conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, data_entrada) "
            "VALUES (?, 'PVC', ?, '2026-09-01')", (codigo, status))
        return cur.lastrowid


def _cliente_logado(cli):
    """Toda rota /controle/* passa pelo gate de login no before_request."""
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


def _le(aid):
    with get_db() as conn:
        return row_to_dict(
            conn.execute('SELECT * FROM amostradores WHERE id=?', (aid,)).fetchone())


@pytest.fixture(autouse=True)
def _limpa():
    init_db()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo LIKE ?", (PREFIXO + '%',))


@pytest.mark.parametrize('status_antes', ['disponivel', 'reservado'])
def test_envio_promove_o_tubo_que_estava_na_prateleira(status_antes):
    aid = _cria(PREFIXO + '1', status_antes)
    with app.test_client() as cli:
        _cliente_logado(cli)
        r = cli.post(f'/controle/amostradores/{aid}/envio_lab',
                     json={'data_envio_lab': '2026-09-09', 'lote': 'L1'})
    assert r.status_code == 200, r.get_data(as_text=True)
    a = _le(aid)
    assert a['data_envio_lab'] == '2026-09-09', a
    assert a['status'] == 'laboratorio', (
        'sem isso o tubo some da fila e do alerta do laboratorio', a)


@pytest.mark.parametrize('status_antes', ['concluido', 'devolvido', 'descartado'])
def test_envio_nao_rebaixa_tubo_que_ja_fechou(status_antes):
    aid = _cria(PREFIXO + '2', status_antes)
    with app.test_client() as cli:
        _cliente_logado(cli)
        r = cli.post(f'/controle/amostradores/{aid}/envio_lab',
                     json={'data_envio_lab': '2026-09-09'})
    assert r.status_code == 200, r.get_data(as_text=True)
    a = _le(aid)
    assert a['status'] == status_antes, (
        'reenvio nao pode ressuscitar amostra que ja fechou', a)


def test_envio_em_lote_promove_igual_ao_individual():
    a1 = _cria(PREFIXO + 'A', 'disponivel')
    a2 = _cria(PREFIXO + 'B', 'reservado')
    a3 = _cria(PREFIXO + 'C', 'concluido')
    with app.test_client() as cli:
        _cliente_logado(cli)
        r = cli.post('/controle/amostradores/envio_lab_lote',
                     json={'ids': [a1, a2, a3], 'data_envio_lab': '2026-09-09'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _le(a1)['status'] == 'laboratorio'
    assert _le(a2)['status'] == 'laboratorio'
    assert _le(a3)['status'] == 'concluido', 'o concluido continua concluido'
    for aid in (a1, a2, a3):
        assert _le(aid)['data_envio_lab'] == '2026-09-09'
