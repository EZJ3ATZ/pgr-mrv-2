# -*- coding: utf-8 -*-
"""Lista numerada nao e quantidade — sinaliza, nao corrige (01/09/2026).

Duas OS do corpus (6197187 e 6196200) escrevem a descricao assim:

    1 ponto de poeiras madeira (todas as especies)
    2 ponto de poeiras carvao (antracito)
    3 pontos de silica livre
    4 ponto de vibracao de corpo inteiro (VCI)
    5 ponto de vibracao de maos e bracos (VMB)
    6 pontos de ruido

Os numeros ENUMERAM os itens; o motor os lia como pontos a medir (VCI=8,
Silica=6, Ruido=10). A deteccao NAO altera a quantidade de proposito: errar
para baixo manda o tecnico a campo com amostrador de menos, e quem sabe o que
foi contratado e quem abriu a OS. Ela marca a demanda para revisao humana.

Medido nas 285 demandas: pega exatamente essas 2 e nenhum formato correto.
"""
import pytest

from controle.inteligencia_demandas import (
    analisar_tarefa_planner, parece_lista_numerada,
)

LISTA = (
    '1 ponto de poeiras madeira (todas as espécies)\r\n'
    '2 ponto de poeiras carvão (antracito)\r\n'
    '3 pontos de sílica livre\r\n'
    '4 ponto de vibração de corpo inteiro (VCI)\r\n'
    '5 ponto de vibração de mãos e braços (VMB)\r\n'
    '6 pontos de ruído'
)


def test_detecta_a_sequencia():
    assert parece_lista_numerada(LISTA) == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize('texto', [
    # formatos reais e corretos do corpus — nao podem casar
    '20 pontos de ruído\r\n4 pontos de calor\r\n2 poeiras silica',
    '05 Pontos de Ruído\r\n04 Pontos de VMB\r\n05 Pontos de PNOS',
    '1 ponto de A\r\n2 pontos de B\r\n3 pontos de C',          # so 3 itens
    '2 ponto de A\r\n3 ponto de B\r\n4 ponto de C\r\n5 ponto de D',  # nao comeca em 1
    '',
])
def test_formato_correto_nao_casa(texto):
    assert parece_lista_numerada(texto) == []


def _analisa(descricao):
    return analisar_tarefa_planner(
        task={'id': 't', 'title': '6197187 - Empresa Teste', 'percentComplete': 0},
        task_details={'description': descricao, 'checklist': {}},
        group_id='g', bucket_nome='Engenharia - Novas Demandas', graph_get_fn=None)


def test_marca_para_revisao_sem_mexer_na_quantidade():
    res = _analisa(LISTA)
    assert any('lista numerada' in i for i in res.inconsistencias), res.inconsistencias
    assert res.needs_review is True
    # as quantidades seguem como o motor leu: a deteccao avisa, nao corrige
    qtds = {a.canonical: a.quantidade for a in res.agentes}
    assert qtds.get('Ruído Ocupacional', 0) > 1, qtds


def test_os_normal_nao_e_marcada_por_isso():
    res = _analisa('20 pontos de ruído\r\n4 pontos de calor')
    assert not any('lista numerada' in i for i in res.inconsistencias)
