# -*- coding: utf-8 -*-
"""O nome do agente não pode ser quebrado na vírgula.

`_agentes_da_linha` separava por `[;,/]` e ` e `. Só que 98 dos 408 agentes do
guia de métodos TÊM vírgula no nome canônico, então a cadeia mandava ao
laboratório:

  "Ferro, óxido (Fe2O3)"                              -> 2 agentes
  "Manganês elementar e compostos inorgânicos, como Mn" -> 3 agentes

Cada agente a mais na coluna AGENTE é uma análise a mais cobrada. Quem decide
agora é o guia: vírgula (e " e ") só separam quando CADA pedaço é, ele próprio,
um agente do catálogo. ';' e '/' seguem separando sempre — é assim que o técnico
lista mais de um agente, e é o separador que a própria tela usa.

Os nomes daqui são os que estão gravados no Postgres de produção.
"""
import pytest

from controle.cadeia_custodia import _agentes_da_linha, _nomes_do_guia


def test_o_guia_carrega():
    assert len(_nomes_do_guia()) > 300, 'sem catálogo não dá para decidir a vírgula'


# ── o bug: nome canônico partido ──────────────────────────────────────

def test_ferro_oxido_continua_um_agente_so():
    assert _agentes_da_linha('Ferro, óxido (Fe2O3)') == ['Ferro, óxido (Fe2O3)']


def test_manganes_com_virgula_e_com_e_continua_um_agente_so():
    nome = 'Manganês elementar e compostos inorgânicos, como Mn'
    assert _agentes_da_linha(nome) == [nome]


@pytest.mark.parametrize('nome', [
    'Acetato de butila, todos os isômeros',
    'Xileno, todos os isômeros',
    'GRÃOS, POEIRA (AVEIA, TRIGO, CEVADA)',
    'Óleo mineral, excluídos os fluidos de trabalho com metais',
    'Madeira – Poeiras, Todas as outras espécies',
    'Poeira Respirável + Sílica LivreCristalina',
    'BTX (Benzeno + Tolueno + Xileno)',
])
def test_nomes_reais_do_banco_nao_sao_partidos(nome):
    assert _agentes_da_linha(nome) == [nome]


# ── o que ainda tem de separar ────────────────────────────────────────

def test_ponto_e_virgula_separa_sempre():
    assert _agentes_da_linha('Tolueno; Xileno') == ['Tolueno', 'Xileno']


def test_barra_separa_sempre_e_preserva_a_virgula_de_dentro():
    """Caso real (coletas_quimico): dois agentes no mesmo tubo, o 1º com vírgula."""
    assert _agentes_da_linha('Ferro, óxido (Fe2O3) / Manganês e seus compostos') == \
        ['Ferro, óxido (Fe2O3)', 'Manganês e seus compostos']


def test_virgula_separa_quando_os_dois_lados_sao_agentes_do_guia():
    assert _agentes_da_linha('Tolueno, Acetona') == ['Tolueno', 'Acetona']
    assert _agentes_da_linha('Tolueno e Acetona') == ['Tolueno', 'Acetona']


def test_na_duvida_nao_inventa_agente():
    """Pedaço que não é agente do guia => não separa. Errar para MENOS é o lado
    barato: agente a mais é análise a mais cobrada, e o técnico ainda vê o campo
    na tela e separa com ';' se quiser."""
    assert _agentes_da_linha('Tolueno, restos de tinta') == ['Tolueno, restos de tinta']


# ── higiene ───────────────────────────────────────────────────────────

def test_vazio_continua_vazio():
    assert _agentes_da_linha('') == []
    assert _agentes_da_linha(None) == []


def test_repetido_entra_uma_vez_so():
    assert _agentes_da_linha('Tolueno; tolueno ; TOLUENO') == ['Tolueno']


def test_quebra_de_linha_e_espaco_extra_nao_viram_agente():
    assert _agentes_da_linha('  Tolueno \n ; \n Acetona ') == ['Tolueno', 'Acetona']


def test_teto_de_dez_agentes_do_formulario():
    muitos = '; '.join(f'Agente {i}' for i in range(1, 15))
    assert len(_agentes_da_linha(muitos)) == 10
