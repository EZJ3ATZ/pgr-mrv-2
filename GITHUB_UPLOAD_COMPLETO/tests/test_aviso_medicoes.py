# -*- coding: utf-8 -*-
"""Demanda de medição nova: dados da OS na tela e e-mail ao grupo Medições (16/10/2026).

Wesley: a OS do CRM chegava no planejamento sem endereço nem contato.
Matheus: toda demanda de medição que chegar vai por e-mail ao grupo Medições
citando a Fabi e o Luiz, mesmo caindo no portal.
"""
import pytest

from app import app
from controle import aviso_medicoes
from controle.db import get_db, init_db, row_to_dict
from controle.routes import get_demanda_por_os

SECRET = 'aviso-secret-teste'
OBS = 'Endereço: PC ITATIANE, 45 - IGARAPÉ/MG\nDados do cliente: Ramon (31) 9138-5004'


def _payload():
    return {
        'origem': 'crm', 'origem_id': 'neg-aviso-1', 'numero_os': 'LIP-AVISO-001',
        'empresa': {'nome': 'AVISO Teste Ltda', 'cnpj': '11.222.333/0001-44'},
        'local': {'endereco': 'R DOS TIMBIRAS 638', 'cidade': 'BELO HORIZONTE', 'uf': 'MG', 'cep': '30140-068'},
        'contato_local': {'nome': 'Jayne', 'telefone': '(31) 3218-5014'},
        'itens': [{'produto': 'Ruído', 'quantidade': 4}],
        'observacoes': OBS, 'solicitante_email': 'comercial2@ocupacional.com.br',
    }


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv('CRM_PLANNER_SECRET', SECRET)
    monkeypatch.setenv('AVISO_MEDICOES_ATIVO', '0')
    init_db()
    yield
    with get_db() as conn:
        for r in conn.execute("SELECT id FROM demandas WHERE origem_ref='crm:neg-aviso-1'").fetchall():
            did = row_to_dict(r)['id']
            conn.execute('DELETE FROM medicoes WHERE demanda_id=?', (did,))
            conn.execute("DELETE FROM eventos WHERE ref_id=? AND tipo='aviso_medicoes_email'", (did,))
            conn.execute('DELETE FROM demandas WHERE id=?', (did,))
        conn.execute("DELETE FROM empresas WHERE nome='AVISO Teste Ltda'")


def _criar():
    with app.test_client() as c:
        r = c.post('/controle/os/medicao', headers={'x-crm-secret': SECRET}, json=_payload())
    assert r.status_code == 201, r.get_json()
    return r.get_json()['demanda_id']


def test_planejamento_recebe_observacoes_e_cadastro():
    _criar()
    with app.test_request_context():
        dc = get_demanda_por_os('LIP-AVISO-001').get_json()['dados_campo']
    assert 'IGARAPÉ' in dc['observacoes']            # local real da medição
    assert 'R DOS TIMBIRAS 638' in dc['endereco']    # cadastro da empresa
    assert 'Jayne' in dc['contato'] and dc['solicitante'] == 'comercial2@ocupacional.com.br'


def test_email_vai_ao_grupo_com_fabi_e_luiz_e_uma_vez_so(monkeypatch):
    did = _criar()
    enviados = []
    import controle.graph as graph
    monkeypatch.setattr(graph, 'graph_post', lambda path, payload: enviados.append(payload))
    aviso_medicoes._rodar(did, 0)
    aviso_medicoes._rodar(did, 0)                    # segunda vez não manda
    assert len(enviados) == 1
    m = enviados[0]['message']
    assert [x['emailAddress']['address'] for x in m['toRecipients']] == ['medicoes@ocupacional.com.br']
    assert {x['emailAddress']['address'] for x in m['ccRecipients']} == {
        'gestaoengenharia@ocupacional.com.br', 'coordenacao@ocupacional.com.br'}
    assert m['body']['content'].startswith('Fabi e Luiz,')
    assert 'LIP-AVISO-001' in m['subject'] and 'IGARAPÉ' in m['body']['content']


def test_fora_de_producao_nao_dispara(monkeypatch):
    monkeypatch.delenv('AVISO_MEDICOES_ATIVO', raising=False)
    monkeypatch.delenv('RAILWAY_ENVIRONMENT_NAME', raising=False)
    assert aviso_medicoes.avisar(123) is False
