# -*- coding: utf-8 -*-
"""Orquestrador da OS (substitui o MAESTRO) — v1.

Cobre os requisitos aprovados pelo Bernardo (22/07): nº de OS lógico com data,
fan-out por raia com SLA, medição direto na tabela demandas (sem Planner),
onboarding só p/ cliente novo >300 vidas, credenciamento = e-mail, aprovação
(Valéria/Luiz) cria a task no Planner e marca opcional a linha do BI.
Graph mockado — nada toca o Planner real; e-mails ficam pendente_envio
(ORQ_ENVIAR_EMAILS desligado nos testes)."""
import re
import json

import pytest

import controle.graph as graph_mod
import controle.orquestrador as orq
from controle.db import get_db, init_db, row_to_dict
from app import app

SECRET = 'orq-secret-teste'

PAYLOAD = {
    'empresa': 'ORQ Teste Ltda', 'cnpj': '11.222.333/0001-44',
    'vidas': 450, 'cliente_novo': True, 'consultor': 'Jéssica',
    'contato_nome': 'Ana', 'contato_email': 'ana@cliente.com', 'contato_tel': '(31) 99999-0000',
    'negocio_crm_id': 'neg-1', 'vencimento': '05/08/2026', 'parcelamento': '2X',
    'envolve_credenciamento': True,
    'servicos': [
        {'nome': 'PGR', 'categoria': 'engenharia', 'valor': 844, 'quantidade': 1},
        {'nome': 'Ruído', 'categoria': 'medicao', 'valor': 300, 'quantidade': 2},
        {'nome': 'AET', 'valor': 500},
        {'nome': 'Treinamento NR-35', 'valor': 200},
    ],
}


@pytest.fixture(autouse=True)
def _orq_ativo_por_padrao(monkeypatch):
    # A maioria dos testes valida o comportamento ATIVO do orquestrador.
    # O interruptor mestre nasce DESLIGADO em produção; aqui ligamos por padrão
    # e os testes de modo dormente sobrescrevem com ORQ_ATIVO=0.
    monkeypatch.setenv('ORQ_ATIVO', '1')


def _limpar():
    init_db()
    with get_db() as conn:
        orq._ensure_schema(conn)
        conn.execute("DELETE FROM os_raias")
        conn.execute("DELETE FROM os_ordens")
        conn.execute("DELETE FROM demandas WHERE origem='crm_os'")


def test_classificacao_por_keyword():
    assert orq.classificar_servico({'nome': 'AET'}) == 'ergonomia'
    assert orq.classificar_servico({'nome': 'Treinamento NR-35'}) == 'treinamento'
    assert orq.classificar_servico({'nome': 'LTCAT'}) == 'engenharia'
    assert orq.classificar_servico({'nome': 'Dosimetria de ruído'}) == 'medicao'


def test_dry_run_fanout_completo():
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=True)
    assert r['ok'] and r['dry_run']
    assert re.fullmatch(r'\d{4}\.\d{4}-\d{3}', r['numero'])   # 2026.0722-001
    raias = {x['raia'] for x in r['raias']}
    assert raias == {'cobranca', 'medicao', 'engenharia', 'ergonomia',
                     'treinamento', 'credenciamento', 'onboarding'}
    cob = next(x for x in r['raias'] if x['raia'] == 'cobranca')
    corpo = cob['detalhe']['email']['corpo']
    assert 'SERVIÇOS | VALOR | VENCIMENTO | O.S | PARCELAMENTO' in corpo
    assert r['numero'] in corpo and '2X' in corpo


def test_onboarding_so_acima_de_300_vidas():
    _limpar()
    p = dict(PAYLOAD); p['vidas'] = 120
    raias = {x['raia'] for x in orq.abrir_os(p, dry_run=True)['raias']}
    assert 'onboarding' not in raias
    p = dict(PAYLOAD); p['cliente_novo'] = False
    raias = {x['raia'] for x in orq.abrir_os(p, dry_run=True)['raias']}
    assert 'onboarding' not in raias


def test_abrir_os_real_cria_demanda_de_medicao():
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    assert r['ok'] and not r['dry_run']
    med = next(x for x in r['raias'] if x['raia'] == 'medicao')
    assert med['demanda_id']
    with get_db() as conn:
        d = row_to_dict(conn.execute(
            "SELECT * FROM demandas WHERE id=?", (med['demanda_id'],)).fetchone())
        assert d['numero_os'] == r['numero'] and d['origem'] == 'crm_os'
        assert 'Ruído' in (d['descricao'] or '')
        cob = row_to_dict(conn.execute(
            "SELECT status FROM os_raias WHERE raia='cobranca'").fetchone())
        assert cob['status'] == 'pendente_envio'   # envio desligado nos testes


def _mock_graph(monkeypatch):
    monkeypatch.setattr(graph_mod, 'graph_ok', lambda: True)
    monkeypatch.setattr(graph_mod, 'criar_planner_task',
                        lambda *a, **k: {'id': 'TASK-MOCK'})
    monkeypatch.setattr(graph_mod, 'set_task_description', lambda *a, **k: True)
    monkeypatch.setattr(graph_mod, 'get_category_ids_by_names',
                        lambda pid, nomes: {'category6': True})
    monkeypatch.setattr(graph_mod, 'get_bucket_id_by_name',
                        lambda *a, **k: 'BK-ENG')
    monkeypatch.setattr(graph_mod, 'get_plan_id_by_title', lambda *a, **k: 'PL-ERG')


def test_aprovacao_cria_task_e_marca_bi(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    with get_db() as conn:
        eng = row_to_dict(conn.execute(
            "SELECT id FROM os_raias WHERE raia='engenharia'").fetchone())
    resp, code = orq.aprovar_raia(r['numero'], eng['id'], 'Evelyn Duarte',
                                  'Luiz Fernando', criar_linha_bi=True)
    assert code == 200 and resp['ok']
    assert resp['planner_task_id'] == 'TASK-MOCK' and resp['bi_linha_pendente']
    with get_db() as conn:
        ra = row_to_dict(conn.execute(
            "SELECT * FROM os_raias WHERE id=?", (eng['id'],)).fetchone())
        assert ra['status'] == 'em_andamento'
        assert ra['tecnico_definido'] == 'Evelyn Duarte'
        assert json.loads(ra['detalhe_json'])['criar_linha_bi'] is True
    # aprovar de novo → 409 (não está mais aguardando)
    resp2, code2 = orq.aprovar_raia(r['numero'], eng['id'], 'X', 'Y')
    assert code2 == 409


def test_concluir_todas_fecha_a_os(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    p = {'empresa': 'Mini Ltda', 'servicos': [{'nome': 'PGR'}]}
    r = orq.abrir_os(p, dry_run=False)
    with get_db() as conn:
        ids = [row_to_dict(x)['id'] for x in conn.execute(
            "SELECT id FROM os_raias ORDER BY id").fetchall()]
    for i, rid in enumerate(ids):
        resp, code = orq.concluir_raia(r['numero'], rid)
        assert code == 200
    assert resp['os_concluida'] is True
    with get_db() as conn:
        o = row_to_dict(conn.execute(
            "SELECT status FROM os_ordens WHERE numero=?", (r['numero'],)).fetchone())
        assert o['status'] == 'concluida'


def test_rota_abrir_exige_secret(monkeypatch):
    _limpar()
    monkeypatch.setenv('CRM_PLANNER_SECRET', SECRET)
    c = app.test_client()
    r = c.post('/controle/os/abrir', json={'empresa': 'X', 'dry_run': True})
    assert r.status_code == 401
    r = c.post('/controle/os/abrir', headers={'x-crm-secret': SECRET},
               json={'empresa': 'X', 'servicos': [], 'dry_run': True})
    assert r.status_code == 200 and r.get_json()['ok']


def test_painel_sla(monkeypatch):
    _limpar()
    orq.abrir_os({'empresa': 'SLA Ltda', 'servicos': [{'nome': 'PGR'}]}, dry_run=False)
    p = orq.painel()
    assert p['ok'] and p['ordens']
    raia = p['ordens'][0]['raias'][0]
    assert raia['horas_decorridas'] is not None and raia['horas_decorridas'] >= 0


def test_painel_filtro_por_negocio():
    # Rastreio do vendedor (pedido da M. Fernanda): o CRM consulta o painel
    # por negocio_crm_id e recebe SÓ as OS daquele negócio, sem LIMIT.
    _limpar()
    orq.abrir_os({'empresa': 'A Ltda', 'negocio_crm_id': '123',
                  'servicos': [{'nome': 'PGR'}]}, dry_run=False)
    orq.abrir_os({'empresa': 'B Ltda', 'negocio_crm_id': '456',
                  'servicos': [{'nome': 'Ruído'}]}, dry_run=False)
    p = orq.painel(negocio='123')
    assert p['ok'] and len(p['ordens']) == 1
    assert p['ordens'][0]['empresa'] == 'A Ltda'
    assert p['ordens'][0]['raias']          # raias vêm junto p/ montar o status
    assert orq.painel(negocio='999')['ordens'] == []
    # int também vale (o CRM manda number)
    assert len(orq.painel(negocio=456)['ordens']) == 1


# ── Interruptor mestre ORQ_ATIVO (CRM não está em uso: dormente por padrão) ──
def test_dormente_nao_grava_nada(monkeypatch):
    """ORQ_ATIVO=0 ⇒ abrir_os vira preview mesmo com dry_run=False:
    nada entra em os_ordens nem em demandas."""
    _limpar()
    monkeypatch.setenv('ORQ_ATIVO', '0')
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    assert r['ok'] and r['dry_run'] and r['dormente'] is True
    assert re.fullmatch(r'\d{4}\.\d{4}-\d{3}', r['numero'])
    with get_db() as conn:
        n_os = row_to_dict(conn.execute(
            "SELECT COUNT(*) AS c FROM os_ordens").fetchone())['c']
        n_dem = row_to_dict(conn.execute(
            "SELECT COUNT(*) AS c FROM demandas WHERE origem='crm_os'").fetchone())['c']
    assert n_os == 0 and n_dem == 0


def test_dormente_bloqueia_aprovacao(monkeypatch):
    """Abre OS real (ativo), depois adormece: aprovar não toca o Planner
    nem muda o status da raia."""
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)          # ativo (fixture)
    with get_db() as conn:
        eng = row_to_dict(conn.execute(
            "SELECT id FROM os_raias WHERE raia='engenharia'").fetchone())
    chamadas = []
    def _spy(*a, **k):
        chamadas.append((a, k))
        return {'id': 'NAO-DEVIA'}
    monkeypatch.setattr(graph_mod, 'criar_planner_task', _spy)
    monkeypatch.setenv('ORQ_ATIVO', '0')
    resp, code = orq.aprovar_raia(r['numero'], eng['id'], 'Evelyn', 'Luiz')
    assert code == 200 and resp.get('dormente') is True
    assert not chamadas                                     # Planner não foi chamado
    with get_db() as conn:
        ra = row_to_dict(conn.execute(
            "SELECT status, planner_task_id FROM os_raias WHERE id=?",
            (eng['id'],)).fetchone())
    assert ra['status'] == 'aguardando_aprovacao' and not ra['planner_task_id']


def test_dormente_bloqueia_conclusao(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os({'empresa': 'Dorm Ltda', 'servicos': [{'nome': 'PGR'}]},
                     dry_run=False)
    with get_db() as conn:
        rid = row_to_dict(conn.execute(
            "SELECT id FROM os_raias LIMIT 1").fetchone())['id']
    monkeypatch.setenv('ORQ_ATIVO', '0')
    resp, code = orq.concluir_raia(r['numero'], rid)
    assert code == 200 and resp.get('dormente') is True
    with get_db() as conn:
        ra = row_to_dict(conn.execute(
            "SELECT status FROM os_raias WHERE id=?", (rid,)).fetchone())
    assert ra['status'] != 'concluida'


# ── Fila de aprovação (backend consumido pela UI do Assinador) ──────────────
def test_fila_aprovacao_lista_raias_pendentes():
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)          # ativo (fixture)
    fila = orq.fila_aprovacao()
    assert fila['ok'] and fila['total'] >= 3
    tipos = {x['raia'] for x in fila['fila']}
    assert {'engenharia', 'ergonomia', 'treinamento'} <= tipos
    eng = next(x for x in fila['fila'] if x['raia'] == 'engenharia')
    assert eng['numero'] == r['numero'] and eng['raia_id']
    assert eng['empresa'] == 'ORQ Teste Ltda'
    assert any('PGR' in (i.get('nome', '')) for i in eng['itens'])
    ergo = next(x for x in fila['fila'] if x['raia'] == 'ergonomia')
    assert ergo.get('modalidade')                            # interna/externa
    # filtro por tipo
    so_eng = orq.fila_aprovacao(raias=['engenharia'])
    assert {x['raia'] for x in so_eng['fila']} == {'engenharia'}


def test_fila_some_apos_aprovar(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    eng = next(x for x in orq.fila_aprovacao(raias=['engenharia'])['fila'])
    orq.aprovar_raia(r['numero'], eng['raia_id'], 'Evelyn', 'Luiz')
    assert orq.fila_aprovacao(raias=['engenharia'])['total'] == 0


def test_fila_vazia_quando_dormente(monkeypatch):
    _limpar()
    monkeypatch.setenv('ORQ_ATIVO', '0')
    orq.abrir_os(dict(PAYLOAD), dry_run=False)               # preview, não grava
    assert orq.fila_aprovacao()['total'] == 0
