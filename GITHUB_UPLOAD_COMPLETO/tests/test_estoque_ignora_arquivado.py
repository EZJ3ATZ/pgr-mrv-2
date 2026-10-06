# -*- coding: utf-8 -*-
"""Amostrador arquivado não é estoque.

06/10/2026, grupo "Os Medidores": a Previsão de estoque mostrava 29 TCP e a
prateleira tinha zero. 10 dos 29 eram o estoque velho que o Wesley arquivou na
recontagem de 05/08 — `arquivar_amostradores_estoque` marca `arquivado=1` e
deixa o status 'disponivel', e a contagem da tela não filtrava o arquivo morto.
O mesmo furo punha o tubo arquivado como opção no select da baixa.
"""
from app import app
from controle.db import get_db, init_db
from controle.routes import estoque_para_agente, previsao_estoque

PREFIXO = 'ESTARQ'
TIPO_SEM_GUIA = 'ZZESTARQ'   # tipo inventado: só este teste mexe nele


def _limpa():
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo LIKE 'ESTARQ%'")
        conn.execute("DELETE FROM medicoes WHERE agente='Agente Teste ESTARQ'")
        conn.execute("DELETE FROM demandas WHERE numero_os='ESTARQ-1'")
        conn.execute("DELETE FROM empresas WHERE nome='EMPRESA ESTARQ'")


def _seed_amostrador(sufixo, tipo, arquivado):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, arquivado) "
            "VALUES (?, ?, 'disponivel', ?)", (f'{PREFIXO}{sufixo}', tipo, arquivado))


def setup_function(_):
    init_db()
    _limpa()


def teardown_function(_):
    _limpa()


def test_previsao_nao_conta_arquivado():
    with get_db() as conn:
        eid = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA ESTARQ')").lastrowid
        did = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) VALUES (?, 'ESTARQ-1', 'pendente')",
            (eid,)).lastrowid
        conn.execute(
            "INSERT INTO medicoes (demanda_id, agente, tipo_amostrador, qtd_pontos_prevista, "
            "qtd_pontos_feita, status) VALUES (?, 'Agente Teste ESTARQ', ?, 5, 0, 'pendente')",
            (did, TIPO_SEM_GUIA))
    _seed_amostrador('01', TIPO_SEM_GUIA, 0)
    _seed_amostrador('02', TIPO_SEM_GUIA, 1)
    _seed_amostrador('03', TIPO_SEM_GUIA, 1)

    with app.test_request_context('/controle/previsao_estoque'):
        d = previsao_estoque().get_json()

    linha = next(n for n in d['necessidades'] if n['tipo'] == TIPO_SEM_GUIA)
    assert linha['em_estoque'] == 1
    assert linha['falta'] == 4


def test_baixa_nao_oferece_amostrador_arquivado():
    # Tolueno -> NIOSH 1501 -> TCP no guia de métodos
    _seed_amostrador('10', 'TCP', 0)
    _seed_amostrador('11', 'TCP', 1)

    with app.test_request_context('/controle/agente/Tolueno/estoque'):
        d = estoque_para_agente('Tolueno').get_json()

    codigos = {a['codigo'] for a in d['amostradores']}
    assert f'{PREFIXO}10' in codigos
    assert f'{PREFIXO}11' not in codigos
