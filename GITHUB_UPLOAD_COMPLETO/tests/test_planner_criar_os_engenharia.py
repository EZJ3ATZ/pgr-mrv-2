# -*- coding: utf-8 -*-
"""Handoff CRM → Planner: o endpoint /controle/planner/criar-os agora serve
DUAS pontas — 'medicao' (comportamento original) e 'engenharia' (novo).

Engenharia usa o MESMO plano das medições (Entregas Técnicas), trocando o label
(por serviço: PGR/PCMSO, TREINAMENTO, PCMSO, LTCAT, LIP, PPR, RELATÓRIO TÉCNICO…)
e nascendo no bucket "Engenharia - Novas Demandas". A task de engenharia NÃO é
lida pelo sync de medições (que filtra só o label MEDIÇÕES).

Graph é mockado — nenhuma task é criada no Planner real. Só o caminho dry_run.
"""
import controle.graph as graph_mod
from app import app

SECRET = 'segredo-de-teste'

# Mapa de labels que imita o plano real "Entregas Técnicas".
_CAT_MAP = {
    'category5': 'TREINAMENTO', 'category6': 'PGR/PCMSO', 'category7': 'PCMSO',
    'category8': 'LTCAT', 'category9': 'LIP', 'category10': 'MEDIÇÕES',
    'category11': 'RELATÓRIO TÉCNICO', 'category12': 'PPR',
}
_BUCKETS = [{'id': 'BK-ENG', 'name': 'Engenharia - Novas Demandas'},
            {'id': 'BK-DONE', 'name': 'Entregue / Concluído'}]


def _mock_graph(monkeypatch):
    monkeypatch.setenv('CRM_PLANNER_SECRET', SECRET)
    monkeypatch.setattr(graph_mod, 'graph_ok', lambda: True)
    monkeypatch.setattr(graph_mod, 'get_plan_category_map', lambda pid: dict(_CAT_MAP))
    monkeypatch.setattr(graph_mod, 'get_plan_buckets', lambda pid: list(_BUCKETS))


def _post(body, secret=SECRET):
    headers = {'x-crm-secret': secret} if secret else {}
    return app.test_client().post('/controle/planner/criar-os',
                                  headers=headers, json=body)


def test_engenharia_labels_explicitos(monkeypatch):
    _mock_graph(monkeypatch)
    r = _post({'destino': 'engenharia', 'empresa': 'ACME LTDA',
               'labels': ['PGR/PCMSO', 'TREINAMENTO'], 'dry_run': True})
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] and d['destino'] == 'engenharia'
    assert set(d['labels'].keys()) == {'category6', 'category5'}
    assert d['bucket_id'] == 'BK-ENG'
    assert 'SERVIÇOS DE ENGENHARIA' in d['descricao']
    assert 'A ABRIR - ACME LTDA' == d['titulo']


def test_engenharia_deriva_labels_dos_itens(monkeypatch):
    """Sem 'labels' explícitos, deriva dos itens do negócio. PCMSO deve cair em
    category7 (exato), não em category6 (PGR/PCMSO)."""
    _mock_graph(monkeypatch)
    r = _post({'destino': 'engenharia', 'empresa': 'Beta SA',
               'itens': [{'nome': 'PCMSO'}, {'nome': 'LTCAT'}], 'dry_run': True})
    d = r.get_json()
    assert r.status_code == 200 and d['ok']
    assert set(d['labels'].keys()) == {'category7', 'category8'}


def test_engenharia_sem_label_reconhecido_400(monkeypatch):
    _mock_graph(monkeypatch)
    r = _post({'destino': 'engenharia', 'empresa': 'Gama',
               'labels': ['serviço inexistente'], 'dry_run': True})
    assert r.status_code == 400
    assert not r.get_json()['ok']


def test_medicao_nao_regrediu(monkeypatch):
    """Destino default (medição): label MEDIÇÕES, SEM bucket, seção de agentes."""
    _mock_graph(monkeypatch)
    r = _post({'empresa': 'Delta', 'itens': [{'nome': 'Ruído'},
               {'nome': 'Calor'}], 'dry_run': True})
    d = r.get_json()
    assert r.status_code == 200 and d['ok'] and d['destino'] == 'medicao'
    assert d['labels'] == {'category10': True}
    assert d['bucket_id'] is None
    assert 'AGENTES / MEDIÇÕES A REALIZAR' in d['descricao']
    assert 'Ruído' in d['descricao'] and 'Calor' in d['descricao']


def test_auth_sem_secret_401(monkeypatch):
    _mock_graph(monkeypatch)
    r = _post({'empresa': 'X', 'dry_run': True}, secret=None)
    assert r.status_code == 401


def test_empresa_obrigatoria_400(monkeypatch):
    _mock_graph(monkeypatch)
    r = _post({'destino': 'engenharia', 'labels': ['PGR'], 'dry_run': True})
    assert r.status_code == 400
