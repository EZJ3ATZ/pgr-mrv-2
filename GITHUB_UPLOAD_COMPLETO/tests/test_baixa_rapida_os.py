# -*- coding: utf-8 -*-
"""baixa_rapida: fecha TODAS as medições pendentes da OS num clique (13/08/2026).

Pedido do Helbert: "coloca alguma forma de dar baixa na empresa, sem precisar
selecionar amostrador ou equipamento — tem muitas empresas antigas, só pra dar
baixa". O amostrador já era opcional desde 10/08; o gargalo era repetir
OS → agente → bomba → vazão para cada agente de cada OS antiga.

O que os testes travam:
  · a baixa acontece sem amostrador e sem equipamento, com motivo obrigatório;
  · medição 'aguardando_lab' NÃO é tocada (espera o RA) e segura a OS aberta;
  · estoque de amostrador não se move em nenhum caso;
  · OS antiga sem medição nenhuma fecha mesmo assim (é o caso do Helbert).
"""
import json

from app import app
from controle import routes as routes_mod
from controle.db import get_db, init_db, row_to_dict


def _seed(agentes=('Silica', 'Ruido'), lab=(), os_num='OS-BXR-1'):
    """Empresa + demanda + medições pendentes; `lab` cria medição aguardando_lab."""
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA BXR TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) VALUES (?, ?, 'pendente')",
            (eid, os_num))
        did = cur.lastrowid
        mids = []
        for ag in agentes:
            cur = conn.execute(
                "INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, "
                "qtd_pontos_feita, status) VALUES (?, ?, 3, 0, 'pendente')", (did, ag))
            mids.append(cur.lastrowid)
        lab_ids = []
        for ag in lab:
            cur = conn.execute(
                "INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, "
                "qtd_pontos_feita, status) VALUES (?, ?, 1, 0, 'aguardando_lab')", (did, ag))
            lab_ids.append(cur.lastrowid)
    return eid, did, mids, lab_ids


def _post(did, payload):
    with app.test_request_context('/controle/demandas/%d/baixa_rapida' % did, json=payload):
        resp = routes_mod.baixa_rapida_demanda(did)
    body = resp[0] if isinstance(resp, tuple) else resp
    status = resp[1] if isinstance(resp, tuple) else 200
    return status, json.loads(body.get_data(as_text=True))


def _med(mid):
    with get_db() as conn:
        return row_to_dict(conn.execute(
            "SELECT status, qtd_pontos_feita FROM medicoes WHERE id=?", (mid,)).fetchone())


def _dem(did):
    with get_db() as conn:
        return row_to_dict(conn.execute(
            "SELECT status FROM demandas WHERE id=?", (did,)).fetchone())


def test_fecha_todos_os_agentes_da_os_de_uma_vez():
    _, did, mids, _ = _seed()
    status, body = _post(did, {
        'motivo': 'Lançamento retroativo de medição antiga',
        'avaliador': 'Helbert', 'data_medicao': '2026-08-10',
        'observacao': 'Empresa antiga — só baixa',
    })
    assert status == 200 and body['ok']
    assert body['baixas'] == 2 and body['os_concluida'] is True
    assert sorted(body['agentes']) == ['Ruido', 'Silica']

    # Cada medição vira realizada com os pontos previstos completos
    for mid in mids:
        m = _med(mid)
        assert m['status'] == 'realizado' and m['qtd_pontos_feita'] == 3

    # E cada uma deixou baixa auditável: sem amostrador, sem bomba, com motivo
    with get_db() as conn:
        bxs = [row_to_dict(r) for r in conn.execute(
            "SELECT b.amostrador_id, b.bomba, b.vazao_calibrada, b.avaliador, "
            "b.data_medicao, b.motivo_sem_amostrador FROM baixas b "
            "JOIN medicoes m ON m.id = b.medicao_id WHERE m.demanda_id=?", (did,)).fetchall()]
    assert len(bxs) == 2
    for bx in bxs:
        assert bx['amostrador_id'] is None
        assert not bx['bomba'] and not bx['vazao_calibrada']
        assert bx['avaliador'] == 'Helbert' and bx['data_medicao'] == '2026-08-10'
        assert bx['motivo_sem_amostrador'] == 'Lançamento retroativo de medição antiga'

    assert _dem(did)['status'] == 'concluida'


def test_aguardando_lab_nao_e_tocada_e_segura_a_os_aberta():
    """A medição que espera o RA do laboratório não pode ser fechada aqui —
    fecharia a cadeia sem resultado. Enquanto sobrar uma, a OS segue pendente."""
    _, did, mids, lab_ids = _seed(agentes=('Silica',), lab=('Poeira mineral',),
                                  os_num='OS-BXR-2')
    status, body = _post(did, {'motivo': 'Avaliação feita por empresa terceira'})
    assert status == 200
    assert body['baixas'] == 1 and body['agentes'] == ['Silica']
    assert body['os_concluida'] is False and body['aguardando_lab'] == 1

    assert _med(mids[0])['status'] == 'realizado'
    assert _med(lab_ids[0])['status'] == 'aguardando_lab'
    assert _med(lab_ids[0])['qtd_pontos_feita'] == 0
    assert _dem(did)['status'] == 'pendente'

    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM baixas WHERE medicao_id=?",
                         (lab_ids[0],)).fetchone()
    assert (n['c'] if hasattr(n, 'keys') else n[0]) == 0


def test_sem_motivo_e_recusado_sem_gravar_nada():
    _, did, mids, _ = _seed(agentes=('Silica',), os_num='OS-BXR-3')
    status, body = _post(did, {'avaliador': 'Helbert'})
    assert status == 400 and 'motivo' in body['erro']
    assert _med(mids[0])['status'] == 'pendente'
    assert _dem(did)['status'] == 'pendente'
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM baixas WHERE medicao_id=?",
                         (mids[0],)).fetchone()
    assert (n['c'] if hasattr(n, 'keys') else n[0]) == 0


def test_os_antiga_sem_medicao_ainda_fecha():
    """O caso que o Helbert descreveu: empresa antiga que nunca teve medição
    cadastrada. Não há baixa para registrar, mas a OS tem de sair da fila."""
    _, did, _, _ = _seed(agentes=(), os_num='OS-BXR-4')
    status, body = _post(did, {'motivo': 'Lançamento retroativo de medição antiga'})
    assert status == 200
    assert body['baixas'] == 0 and body['os_concluida'] is True
    assert _dem(did)['status'] == 'concluida'


def test_nao_move_estoque_de_amostrador():
    """Baixa rápida é sem dispositivo nosso: nada pode virar 'laboratorio'."""
    _, did, _, _ = _seed(agentes=('Silica',), os_num='OS-BXR-5')
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo='BXR-EST-1'")
        conn.execute("INSERT INTO amostradores (codigo, tipo, status) "
                     "VALUES ('BXR-EST-1', 'tubo', 'disponivel')")

    status, _ = _post(did, {'motivo': 'Amostrador não cadastrado no sistema'})
    assert status == 200

    with get_db() as conn:
        am = row_to_dict(conn.execute(
            "SELECT status, empresa_id FROM amostradores WHERE codigo='BXR-EST-1'").fetchone())
    assert am['status'] == 'disponivel' and not am['empresa_id']


def test_demanda_inexistente_da_404():
    status, body = _post(999999, {'motivo': 'Lançamento retroativo de medição antiga'})
    assert status == 404 and 'ncontrada' in body['erro']
