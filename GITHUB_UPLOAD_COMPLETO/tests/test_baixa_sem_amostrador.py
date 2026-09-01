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
        'motivo_sem_amostrador': 'Avaliação feita por empresa terceira',
        'avaliador': 'Helbert', 'data_medicao': '2026-08-10',
        'observacao': 'Avaliacao feita pela Opus',
    })
    assert status == 200 and body['ok'] and body['sem_amostrador'] is True

    med = _medicao(mid)
    assert med['status'] == 'realizado' and med['qtd_pontos_feita'] == 1

    with get_db() as conn:
        bx = row_to_dict(conn.execute(
            "SELECT amostrador_id, avaliador, motivo_sem_amostrador "
            "FROM baixas WHERE medicao_id=?", (mid,)).fetchone())
        dem = row_to_dict(conn.execute(
            "SELECT status FROM demandas WHERE id=?", (did,)).fetchone())
    assert bx['amostrador_id'] is None and bx['avaliador'] == 'Helbert'
    assert bx['motivo_sem_amostrador'] == 'Avaliação feita por empresa terceira'
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


def test_sem_amostrador_e_sem_motivo_e_recusado():
    _, _, mid, _ = _seed()
    status, body = _post({'medicao_id': mid, 'amostrador_id': None, 'avaliador': 'Helbert'})
    assert status == 400 and 'motivo' in body['erro']
    # Nada gravado: nem baixa, nem medicao mexida
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM baixas WHERE medicao_id=?", (mid,)).fetchone()
    assert (n['c'] if hasattr(n, 'keys') else n[0]) == 0
    assert _medicao(mid)['status'] == 'pendente'


def test_avulso_sem_motivo_nao_cria_medicao_orfa():
    """A medicao avulsa e criada on-the-fly; se o motivo travasse depois disso,
    sobraria medicao fantasma na demanda. O gate roda antes de qualquer escrita."""
    _, did, _, _ = _seed()
    with get_db() as conn:
        antes = conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=?", (did,)).fetchone()
        antes = antes['c'] if hasattr(antes, 'keys') else antes[0]

    status, body = _post({'demanda_id': did, 'agente_avulso': 'Silica', 'amostrador_id': None})
    assert status == 400 and 'motivo' in body['erro']

    with get_db() as conn:
        depois = conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=?", (did,)).fetchone()
        depois = depois['c'] if hasattr(depois, 'keys') else depois[0]
    assert depois == antes


def test_motivo_e_ignorado_quando_ha_amostrador():
    """Campo existe para o caso sem amostrador. Com amostrador a baixa nao
    precisa dele e nao pode travar por causa dele."""
    _, _, mid, aid = _seed(codigo='BXSEM02')
    status, body = _post({
        'medicao_id': mid, 'amostrador_id': aid,
        'avaliador': 'Wesley', 'vazao_calibrada': 0.15,
    })
    assert status == 200 and body['ok']

def test_amostrador_ja_usado_e_recusado():
    """Amostrador que ja teve baixa nao aceita outra: a segunda sobrescrevia
    empresa/avaliador/data_medicao da primeira, calada."""
    _, _, mid, aid = _seed('BX-USADO-1')
    with get_db() as conn:
        conn.execute("UPDATE amostradores SET status='laboratorio' WHERE id=?", (aid,))

    status, body = _post({'medicao_id': mid, 'amostrador_id': aid, 'avaliador': 'Helbert'})
    assert status == 409, body
    assert 'No laborat' in body['erro']
    assert _medicao(mid)['status'] == 'pendente'


def test_avulso_com_amostrador_usado_nao_cria_medicao_orfa():
    """Espelha test_avulso_sem_motivo_nao_cria_medicao_orfa para a guarda de
    status: se ela rodasse DEPOIS do INSERT avulso, sobraria medicao fantasma
    pendurada na demanda — e a demanda nunca fecharia."""
    _, did, _, aid = _seed('BX-USADO-2')
    with get_db() as conn:
        conn.execute("UPDATE amostradores SET status='concluido' WHERE id=?", (aid,))
        antes = conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=?", (did,)).fetchone()
        antes = antes['c'] if hasattr(antes, 'keys') else antes[0]

    status, body = _post({'demanda_id': did, 'agente_avulso': 'Silica',
                          'amostrador_id': aid, 'avaliador': 'Helbert'})
    assert status == 409, body

    with get_db() as conn:
        depois = conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=?", (did,)).fetchone()
        depois = depois['c'] if hasattr(depois, 'keys') else depois[0]
    assert depois == antes, 'guarda deixou medicao orfa na demanda'


def test_amostrador_reservado_ainda_aceita_baixa():
    """reservado = separado para um plano, ainda vai a campo. Nao pode travar."""
    _, _, mid, aid = _seed('BX-RESERV-1')
    with get_db() as conn:
        conn.execute("UPDATE amostradores SET status='reservado' WHERE id=?", (aid,))

    status, body = _post({'medicao_id': mid, 'amostrador_id': aid, 'avaliador': 'Helbert'})
    assert status == 200, body
    assert _medicao(mid)['status'] == 'realizado'
