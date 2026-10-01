# -*- coding: utf-8 -*-
"""OS de mais de um dia: cada linha de agente tem o seu "dia da medição".

Caso real (prod, 01/10/2026): o plano 54 do Helbert (Belgo Bekaert, OS 6718294)
tinha dois Ácido Sulfúrico marcados para 30/09 e 26/10, estava `confirmado` SEM
data prevista e a estimativa dizia 1 dia. O único campo de data da linha era a
"data da calibração da bomba", sem rótulo, e os dois técnicos a preenchiam com o
dia da visita (24 de 35 iguais à data prevista, 11 datas futuras).

O que este arquivo garante, no servidor:
  - data prevista vazia assume o PRIMEIRO dia programado nas linhas (POST e PUT);
  - o calendário do mês marca `agendado` em CADA dia programado, não só na data
    prevista;
  - o diário do dia lista o plano em qualquer um dos dias programados;
  - legado: linha com `data_calibracao` e sem `dia_medicao` conta como dia.
"""
import json

import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

OS = 'MULTIDIA'
D1, D2 = '2031-03-10', '2031-04-07'   # datas longe de qualquer outro teste


def _cli():
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    cli = app.test_client()
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM planejamentos WHERE numero_os LIKE ?', (OS + '%',))
    yield {'eid': eid}
    with get_db() as conn:
        conn.execute('DELETE FROM planejamentos WHERE numero_os LIKE ?', (OS + '%',))


def _linhas(chave='dia_medicao'):
    return [
        {'tipo': 'quimico', 'agente': 'Ácido Sulfúrico', 'qtd': 1, chave: D1},
        {'tipo': 'quimico', 'agente': 'Ácido Sulfúrico', 'qtd': 1, chave: D2},
    ]


def _plano(conn, os_):
    r = conn.execute('SELECT * FROM planejamentos WHERE numero_os=?', (os_,)).fetchone()
    return row_to_dict(r) if r else None


def test_post_sem_data_prevista_assume_o_primeiro_dia_programado(_cenario):
    cli = _cli()
    r = cli.post('/controle/planejamentos', json={
        'empresa_id': _cenario['eid'], 'tecnico': 'Tecnico', 'numero_os': OS,
        'data_prevista': '', 'status': 'rascunho', 'agentes_previstos': _linhas()})
    assert r.status_code == 200, r.get_data(as_text=True)
    with get_db() as conn:
        p = _plano(conn, OS)
    assert p and p['data_prevista'] == D1, p


def test_calendario_marca_cada_dia_programado(_cenario):
    cli = _cli()
    r = cli.post('/controle/planejamentos', json={
        'empresa_id': _cenario['eid'], 'tecnico': 'Tecnico', 'numero_os': OS,
        'data_prevista': '', 'status': 'rascunho', 'agentes_previstos': _linhas()})
    assert r.status_code == 200, r.get_data(as_text=True)
    m1 = cli.get('/controle/diario_calendario?mes=' + D1[:7]).get_json()['dias']
    m2 = cli.get('/controle/diario_calendario?mes=' + D2[:7]).get_json()['dias']
    assert m1.get(D1, {}).get('agendado', 0) >= 1, m1
    assert m2.get(D2, {}).get('agendado', 0) >= 1, m2


def test_diario_do_dia_lista_o_plano_no_segundo_dia(_cenario):
    cli = _cli()
    r = cli.post('/controle/planejamentos', json={
        'empresa_id': _cenario['eid'], 'tecnico': 'Tecnico Multi', 'numero_os': OS,
        'data_prevista': '', 'status': 'rascunho', 'agentes_previstos': _linhas()})
    assert r.status_code == 200, r.get_data(as_text=True)
    for dia in (D1, D2):
        ag = cli.get('/controle/diario_tecnicos?data=' + dia).get_json()['agendados']
        assert any(a['os'] == OS for a in ag), (dia, ag)


def test_legado_data_calibracao_conta_como_dia(_cenario):
    """Plano salvo ANTES do campo existir: a linha química só tem data_calibracao."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO planejamentos (empresa_id, numero_os, tecnico, data_prevista, "
            "status, agentes_previstos, criado_em) VALUES (?, ?, 'Tecnico', '', 'confirmado', ?, '2031-03-01')",
            (_cenario['eid'], OS + 'L', json.dumps(_linhas('data_calibracao'))))
    cli = _cli()
    m2 = cli.get('/controle/diario_calendario?mes=' + D2[:7]).get_json()['dias']
    assert m2.get(D2, {}).get('agendado', 0) >= 1, m2
    ag = cli.get('/controle/diario_tecnicos?data=' + D1).get_json()['agendados']
    assert any(a['os'] == OS + 'L' for a in ag), ag


def test_put_sem_data_prevista_tambem_assume(_cenario):
    cli = _cli()
    r = cli.post('/controle/planejamentos', json={
        'empresa_id': _cenario['eid'], 'tecnico': 'Tecnico', 'numero_os': OS,
        'data_prevista': '', 'status': 'rascunho', 'agentes_previstos': []})
    assert r.status_code == 200, r.get_data(as_text=True)
    with get_db() as conn:
        pid = _plano(conn, OS)['id']
    r = cli.put(f'/controle/planejamentos/{pid}', json={
        'data_prevista': '', 'status': 'rascunho', 'agentes_previstos': _linhas()})
    assert r.status_code == 200, r.get_data(as_text=True)
    with get_db() as conn:
        p = _plano(conn, OS)
    assert p['data_prevista'] == D1, p


def test_data_prevista_informada_prevalece(_cenario):
    """Quem preenche a data prevista manda; as linhas só completam o que está vazio."""
    cli = _cli()
    r = cli.post('/controle/planejamentos', json={
        'empresa_id': _cenario['eid'], 'tecnico': 'Tecnico', 'numero_os': OS,
        'data_prevista': '2031-03-03', 'status': 'rascunho', 'agentes_previstos': _linhas()})
    assert r.status_code == 200, r.get_data(as_text=True)
    with get_db() as conn:
        p = _plano(conn, OS)
    assert p['data_prevista'] == '2031-03-03', p
