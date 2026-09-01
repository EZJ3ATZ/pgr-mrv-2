# -*- coding: utf-8 -*-
"""O "(xN)" que o PROPRIO portal escreve nao era lido na volta (01/09/2026).

routes.py:386 e orquestrador.py:368 montam a descricao da task do Planner como
"- Silica Livre Cristalizada  (x2)". O lookback de quantidade so enxerga numero
ANTES do nome, entao o motor lia 1 e a quantidade morria no ida-e-volta
CRM -> Planner -> motor. Zero casos no corpus hoje (o CRM ainda nao gerou task
assim); e prevencao para quando gerar.

Trava os dois lados: o sufixo passa a valer, e o formato majoritario das OS de
hoje (numero ANTES: "20 pontos de ruido") nao pode mudar de comportamento.
"""
import pytest

from controle.inteligencia_demandas import extrair_agentes_multifonte


def _qtd(texto):
    return {a.canonical: a.quantidade for a in extrair_agentes_multifonte(descricao=texto)}


def test_sufixo_do_portal_vira_quantidade():
    assert _qtd('- Sílica Livre Cristalizada  (x2)')['Sílica Cristalina'] == 2


def test_sufixo_em_varios_itens():
    got = _qtd('- Ruído Ocupacional  (x3)\r\n- Calor (IBUTG)  (x2)')
    assert got['Ruído Ocupacional'] == 3
    assert got['Calor (IBUTG)'] == 2


def test_sem_sufixo_continua_um():
    assert _qtd('- Ruído Ocupacional')['Ruído Ocupacional'] == 1


@pytest.mark.parametrize('texto,esperado', [
    ('20 pontos de ruído', 20),
    ('05 Pontos de Ruído', 5),
    ('4x ruído', 4),
])
def test_numero_antes_do_nome_nao_muda(texto, esperado):
    """Formato majoritario das OS reais — nao pode regredir."""
    assert _qtd(texto)['Ruído Ocupacional'] == esperado


def test_prefixo_vence_o_sufixo():
    """Se os dois aparecem, o numero escrito antes e o que o comercial digitou."""
    assert _qtd('5 pontos de ruído (x2)')['Ruído Ocupacional'] == 5


def test_sufixo_de_outra_linha_nao_vaza():
    """O (x9) da linha de baixo nao pode virar quantidade do agente de cima."""
    got = _qtd('- Ruído Ocupacional\r\n- Calor (IBUTG)  (x9)')
    assert got['Ruído Ocupacional'] == 1
    assert got['Calor (IBUTG)'] == 9
