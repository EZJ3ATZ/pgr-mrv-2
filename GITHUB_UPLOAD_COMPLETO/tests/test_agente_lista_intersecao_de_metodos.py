# -*- coding: utf-8 -*-
"""Campo com mais de um agente devolve o método COMUM, ou nada.

Existe em produção: a coleta da AABB de 16/07/2026 gravou
"Ferro, óxido (Fe2O3) / Manganês e seus compostos" num campo só. Um nome desses
não casa com nenhuma entrada do guia e caía no fuzzy, que devolvia o método de
UM dos dois — antes do fix de 08/09 era o Ferro (nome mais curto contido na
chave), depois passou a ser o Manganês (nome mais longo). Nenhum dos dois está
certo por si.

A resposta certa é a INTERSEÇÃO: um tubo carrega um método e nele cabem dois
analitos. Ferro e Manganês compartilham exatamente NIOSH 7303 com amostrador
SKC 225-5 (EC), e o tubo daquela coleta é EC97917A, um EC — o técnico estava
certo em juntar. Quando não existe método comum, devolve vazio: Tolueno e
Sílica não dividem tubo, e escolher um dos dois manda o campo coletar com a
vazão e o amostrador errados.

A régua de "isto é lista?" é a mesma da cadeia de custódia
(`_agentes_da_linha`), de propósito: separa por ';' e '/', e por vírgula só
quando os dois lados são agentes do guia — 98 dos 408 nomes do guia têm vírgula
DENTRO do nome. Conferido antes de escrever: dos 408 nomes do guia, zero é
quebrado por engano.
"""
import json
import os

import pytest

from controle.routes import _buscar_metodos_agente
from controle.cadeia_custodia import _agentes_da_linha

FERRO = 'Ferro, óxido (Fe2O3)'
MANGANES = 'Manganês e seus compostos'


def _ids(metodos):
    return {(m.get('metodoCod') or '', m.get('amostradorCod') or '')
            for m in (metodos or [])}


def test_caso_de_producao_devolve_o_metodo_comum():
    m = _buscar_metodos_agente(f'{FERRO} / {MANGANES}')
    assert m, 'o campo com dois agentes deixou de resolver'
    assert _ids(m) == {('NIOSH 7303', 'SKC 225-5 (EC*****)')}, _ids(m)
    # e é de fato a interseção dos dois, não o método de um deles
    assert _ids(m) == _ids(_buscar_metodos_agente(FERRO)) & \
        _ids(_buscar_metodos_agente(MANGANES))


def test_variante_que_so_um_dos_dois_tem_fica_fora():
    """Manganês também aceita MDHS 14/3 com amostrador IEC; Ferro não.

    Antes essa variante vinha na lista e a tela podia pré-selecionar um
    amostrador IEC para um tubo EC.
    """
    so_manganes = _ids(_buscar_metodos_agente(MANGANES))
    juntos = _ids(_buscar_metodos_agente(f'{FERRO} / {MANGANES}'))
    assert ('MDHS 14/3 - NIOSH 7303', 'SKC 225-1930 (IEC*****)') in so_manganes
    assert ('MDHS 14/3 - NIOSH 7303', 'SKC 225-1930 (IEC*****)') not in juntos


@pytest.mark.parametrize('campo', [
    'Tolueno / Sílica Cristalina',
    'Sílica Cristalina; Acetona',
])
def test_sem_metodo_comum_devolve_vazio(campo):
    """Melhor a tela pedir preenchimento manual do que mandar coletar errado."""
    assert _buscar_metodos_agente(campo) == [], campo


@pytest.mark.parametrize('campo,esperado', [
    ('Tolueno; Xileno, todos os isômeros', ('NIOSH 1501', 'SKC 226-01 (TCP*****)')),
])
def test_dois_agentes_do_mesmo_tubo_de_carvao(campo, esperado):
    assert esperado in _ids(_buscar_metodos_agente(campo)), campo


@pytest.mark.parametrize('nome', [
    'Acetato de butila, todos os isômeros',
    'Ferro, óxido (Fe2O3)',
    'Manganês elementar e compostos inorgânicos, como Mn',
    'Óleo mineral, excluídos os fluidos de trabalho com metais',
    'GRÃOS, POEIRA (AVEIA, TRIGO, CEVADA)',
])
def test_virgula_dentro_do_nome_nao_vira_lista(nome):
    """98 dos 408 nomes do guia têm vírgula. Nenhum pode ser quebrado."""
    assert len(_agentes_da_linha(nome)) == 1, _agentes_da_linha(nome)
    assert _buscar_metodos_agente(nome), f'{nome} deixou de resolver'


def test_nenhum_nome_do_guia_e_quebrado_em_lista():
    """A guarda que autoriza a regra: rodada no catálogo inteiro, não em amostra."""
    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'guia_metodos.json')
    with open(caminho, encoding='utf-8') as f:
        guia = json.load(f)
    quebrados = [k for k in guia['by_name']
                 if len(_agentes_da_linha(k.replace('\n', ' '))) > 1]
    assert quebrados == [], quebrados
