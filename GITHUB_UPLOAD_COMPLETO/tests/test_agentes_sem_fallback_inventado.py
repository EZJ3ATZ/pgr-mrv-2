# -*- coding: utf-8 -*-
"""3º fallback (parser_agentes.extrair_agentes) aposentado — 01/09/2026.

Medido nas 285 demandas de produção: o fallback era alcançado em 94 e
contribuiu agente em 0. Não existe UMA demanda em que o motor volte vazio e o
parser antigo produza algo (0/285) — ou seja, ele nunca acrescentou nada. O que
ele fazia era inventar: rodado nas 194 descrições não vazias, o catch-all
`_normalizar_tipo -> 'quimico'` fabricava substância em 93 delas ("Ltda",
"Serviços", "cada mês", "Mineração Morro do Sino", "Medição: Avaliação de").

O caso abaixo é o pior deles: a descrição é ergonomia pura. O motor reconhece
"Ergonomia", o filtro `_eh_agente_medicao` a descarta (não é medição de campo)
e a lista fica vazia — mas `so_nao_medicao` só olha o extracao_json, então a
demanda caía no parser antigo, que devolvia um AGENTE QUÍMICO chamado
"posto de trabalho do operador". Esse agente ia para o planejamento e para a
planilha de campo.
"""
import json

from app import app
from controle import routes as routes_mod
from controle.db import get_db, init_db


def _agentes(did):
    """Chama a view direto (o before_request do blueprint exige sessao)."""
    with app.test_request_context('/controle/demandas/%d/agentes' % did):
        resp = routes_mod.get_demanda_agentes(did)
    body = resp[0] if isinstance(resp, tuple) else resp
    return json.loads(body.get_data(as_text=True))


def _seed(descricao, os_num):
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA FALLBACK TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, titulo, descricao, status) "
            "VALUES (?, ?, ?, ?, 'pendente')",
            (eid, os_num, f'{os_num} - EMPRESA FALLBACK TESTE', descricao))
        return cur.lastrowid


def test_prosa_de_ergonomia_nao_vira_agente_quimico():
    did = _seed('Avaliar o posto de trabalho do operador', 'OS-FB-1')
    agentes = _agentes(did)['agentes']
    assert agentes == [], f'inventou agente a partir de prosa: {agentes}'


def test_prosa_sem_agente_nenhum_nao_vira_quimico():
    did = _seed('Retorno para avaliar a documentacao pendente do setor', 'OS-FB-2')
    agentes = _agentes(did)['agentes']
    assert agentes == [], f'inventou agente a partir de prosa: {agentes}'


def test_os_de_verdade_continua_extraindo():
    """Trava o outro lado: remover o fallback não pode zerar OS legítima.
    Texto real do corpus (id=129, OS 6267066)."""
    did = _seed('OBS na O.S: Foi acordado com o cliente a realização das seguintes '
                'medições:\r\n05 Pontos de Ruído\r\n04 Pontos de VMB\r\n05 Pontos de PNOS',
                'OS-FB-3')
    saida = _agentes(did)
    resumo = saida['resumo']
    assert resumo['ruido'] == 5, saida
    assert resumo['vibracao_vbma'] == 4, saida
