# -*- coding: utf-8 -*-
"""dar_baixa: amostrador é OPCIONAL (10/08/2026).

Pedido do Helbert: a Opus (empresa terceira) fez a avaliação, então não existe
amostrador nosso para mandar ao laboratório — mas a baixa da medição precisa
acontecer. Antes o endpoint devolvia 400 'amostrador_id obrigatorio' e a tela
travava com alert('Selecione o amostrador').

Cobre os dois lados: sem amostrador nada de estoque se move; com amostrador o
comportamento antigo (→ laboratório) continua igual.
"""
import json

from app import app
from controle import routes as routes_mod
from controle.db import get_db, init_db, row_to_dict


def _seed(codigo=None):
    """Cria empresa + demanda + medicao pendente (e amostrador, se codigo)."""
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA BAIXA TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) VALUES (?, 'OS-BX-1', 'pendente')",
            (eid,))
        did = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, qtd_pontos_feita, status) "
            "VALUES (?, 'Silica', 1, 0, 'pendente')", (did,))
        mid = cur.lastrowid
        aid = None
        if codigo:
            conn.execute("DELETE FROM amostradores WHERE codigo=?", (codigo,))
            cur = conn.execute(
                "INSERT INTO amostradores (codigo, tipo, status) VALUES (?, 'tubo', 'disponivel')",
                (codigo,))
            aid = cur.lastrowid
    return eid, did, mid, aid


def _post(payload):
    with app.test_request_context('/controle/baixa', json=payload):
        resp = routes_mod.dar_baixa()
    body = resp[0] if isinstance(resp, tuple) else resp
    status = resp[1] if isinstance(resp, tuple) else 200
    return status, json.loads(body.get_data(as_text=True))


def _medicao(mid):
    with get_db() as conn:
        return row_to_dict(conn.execute(
            "SELECT status, qtd_pontos_feita FROM medicoes WHERE id=?", (mid,)).fetchone())


def test_baixa_sem_amostrador_registra_medicao():
    _, did, mid, _ = _seed()
    status, body = _post({
        'medicao_id': mid, 'amostrador_id': None,
        'avaliador': 'Helbert', 'data_medicao': '2026-08-10',
        'observacao': 'Avaliacao feita pela Opus',
    })
    assert status == 200 and body['ok'] and body['sem_amostrador'] is True

    med = _medicao(mid)
    assert med['status'] == 'realizado' and med['qtd_pontos_feita'] == 1

    with get_db() as conn:
        bx = row_to_dict(conn.execute(
            "SELECT amostrador_id, avaliador FROM baixas WHERE medicao_id=?", (mid,)).fetchone())
        dem = row_to_dict(conn.execute(
            "SELECT status FROM demandas WHERE id=?", (did,)).fetchone())
    assert bx['amostrador_id'] is None and bx['avaliador'] == 'Helbert'
    assert dem['status'] == 'concluida'


def test_baixa_com_amostrador_continua_movendo_para_o_lab():
    _, _, mid, aid = _seed(codigo='BXSEM01')
    status, body = _post({
        'medicao_id': mid, 'amostrador_id': aid,
        'avaliador': 'Wesley', 'vazao_calibrada': 0.15,
        'volume_min': 30, 'volume_max': 60, 'data_medicao': '2026-08-10',
    })
    assert status == 200 and body['ok'] and body['sem_amostrador'] is False

    with get_db() as conn:
        am = row_to_dict(conn.execute(
            "SELECT status, empresa_id, avaliador FROM amostradores WHERE id=?", (aid,)).fetchone())
    assert am['status'] == 'laboratorio' and am['empresa_id'] and am['avaliador'] == 'Wesley'
    assert _medicao(mid)['status'] == 'realizado'


def test_amostrador_inexistente_ainda_da_404():
    _, _, mid, _ = _seed()
    status, body = _post({'medicao_id': mid, 'amostrador_id': 999999})
    assert status == 404 and 'amostrador' in body['erro']
