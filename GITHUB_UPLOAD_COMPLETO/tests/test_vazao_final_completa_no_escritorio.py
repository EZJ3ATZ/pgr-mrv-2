# -*- coding: utf-8 -*-
"""Vazão final em branco não trava a planilha de campo; completa no escritório.

Wesley, 21/10/2026 (grupo Os Medidores): "A Vazão Final eu só consigo colocar
depois que faço o cálculo aí no escritório". Na tela, o campo em branco virava
0 (`parseFloat(...)||0`) e a guarda de física recusava "a vazão tem de ser
maior que zero" — a planilha não finalizava. Resultado no banco: 26 de 85
amostradores com a vazão final IGUAL à inicial, ou seja, chute para destravar.

E quando passava em branco pelo servidor, `save_coleta_quimico` gravava
vazão final 0 e ΔV de 100 %: o tubo nascia `divergente`.

Agora: em branco (ou 0, que é o que a tela antiga manda no lugar do branco)
grava NULL e o tubo fica `pendente`; a aba Planilhas Feitas lista os
pendentes e o técnico completa ali, com Vm, volume e ΔV recalculados.
"""
import pytest

from app import app
from controle.db import get_db, init_db

PREFIXO = 'VFPEND'


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (PREFIXO,))
        conn.execute(
            "INSERT INTO demandas (numero_os, empresa_id, status, criado_em) "
            "VALUES (?, ?, 'aberta', '2026-10-01')", (PREFIXO, eid))
        conn.execute("DELETE FROM usuarios WHERE email='vfpend.tecnico@teste.local'")
        conn.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, role) "
            "VALUES ('Tecnico VF', 'vfpend.tecnico@teste.local', '', 'tecnico')")
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM coletas_quimico_amostr WHERE id_amostrador LIKE ?", (PREFIXO + '%',))
        conn.execute("DELETE FROM coletas_quimico WHERE demanda_id IN "
                     "(SELECT id FROM demandas WHERE numero_os=?)", (PREFIXO,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (PREFIXO,))
        conn.execute("DELETE FROM usuarios WHERE email='vfpend.tecnico@teste.local'")


def _cliente():
    with get_db() as conn:
        uid = conn.execute("SELECT id FROM usuarios WHERE email='vfpend.tecnico@teste.local'"
                           ).fetchone()['id']
    cli = app.test_client()
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


def _finalizar(cli, tubo, vazao_final):
    with get_db() as conn:
        d = conn.execute('SELECT id, empresa_id FROM demandas WHERE numero_os=?',
                         (PREFIXO,)).fetchone()
    am = {'id_amostrador': tubo, 'tipo_amostrador': 'PVC', 'substancia': 'Poeira Total',
          'vazao_inicial': 2.0, 'vazao_final': vazao_final,
          'hora_inicio': '08:00', 'hora_final': '16:00'}
    r = cli.post('/controle/medicoes', json={
        'tipo': 'quimico', 'empresa_id': d['empresa_id'], 'empresa_nome': 'Teste VF',
        'demanda_id': d['id'], 'data': '2026-10-21', 'avaliador': 'Wesley',
        'campo_quimico': {'func_nome': 'Trabalhador ' + tubo, 'substancias': 'Poeira Total',
                          'amostradores': [am]}})
    return r


def _linha(tubo):
    with get_db() as conn:
        return dict(conn.execute(
            'SELECT * FROM coletas_quimico_amostr WHERE id_amostrador=?', (tubo,)).fetchone())


@pytest.mark.parametrize('em_branco', [None, '', 0])
def test_finaliza_sem_vazao_final_e_fica_pendente(em_branco):
    cli = _cliente()
    tubo = f'{PREFIXO}{em_branco!r}'.replace("'", '')
    r = _finalizar(cli, tubo, em_branco)
    assert r.status_code == 200, r.get_json()
    assert (r.get_json() or {}).get('ok') is True
    am = _linha(tubo)
    assert am['vazao_final'] is None
    assert am['variacao_vazao'] is None
    assert am['status_variacao'] == 'pendente'
    # Enquanto falta a final, o volume sai da inicial (2,0 L/min x 480 min).
    assert am['vazao_media'] == pytest.approx(2.0)
    assert am.get('volume_L', am.get('volume_l')) == pytest.approx(960.0)


def test_escritorio_completa_e_recalcula():
    cli = _cliente()
    tubo = PREFIXO + 'A'
    assert _finalizar(cli, tubo, None).status_code == 200

    pend = cli.get('/controle/coletas/quimico/vazao-pendente')
    assert pend.status_code == 200
    linhas = [p for p in pend.get_json()['itens'] if p['id_amostrador'] == tubo]
    assert len(linhas) == 1
    assert linhas[0]['vazao_inicial'] == pytest.approx(2.0)
    aid = linhas[0]['id']

    # Física continua valendo no escritório.
    for ruim in (0, -1.9, 19974):
        r = cli.post(f'/controle/coletas/quimico/amostr/{aid}/vazao-final',
                     json={'vazao_final': ruim})
        assert r.status_code == 400, ruim
    assert _linha(tubo)['vazao_final'] is None

    r = cli.post(f'/controle/coletas/quimico/amostr/{aid}/vazao-final',
                 json={'vazao_final': '1,9'})
    assert r.status_code == 200, r.get_json()
    am = _linha(tubo)
    assert am['vazao_final'] == pytest.approx(1.9)
    assert am['vazao_media'] == pytest.approx(1.95)
    assert am.get('volume_L', am.get('volume_l')) == pytest.approx(936.0)
    assert am['variacao_vazao'] == pytest.approx(5.0)
    assert am['status_variacao'] == 'conforme'

    pend = cli.get('/controle/coletas/quimico/vazao-pendente').get_json()['itens']
    assert not [p for p in pend if p['id_amostrador'] == tubo]


def test_completar_fora_de_5_porcento_marca_divergente():
    cli = _cliente()
    tubo = PREFIXO + 'B'
    assert _finalizar(cli, tubo, None).status_code == 200
    aid = _linha(tubo)['id']
    r = cli.post(f'/controle/coletas/quimico/amostr/{aid}/vazao-final',
                 json={'vazao_final': 1.8})
    assert r.status_code == 200
    am = _linha(tubo)
    assert am['variacao_vazao'] == pytest.approx(10.0)
    assert am['status_variacao'] == 'divergente'


def test_tubo_inexistente_404():
    r = _cliente().post('/controle/coletas/quimico/amostr/99999999/vazao-final',
                        json={'vazao_final': 1.9})
    assert r.status_code == 404
