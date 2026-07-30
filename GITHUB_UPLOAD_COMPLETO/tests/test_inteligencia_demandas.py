# -*- coding: utf-8 -*-
"""Motor de extração de agentes/OS das demandas (inteligencia_demandas.py).

Cobre os dedups que já quebraram em produção (vibração genérica vs VCI/VMB,
gases genérico vs VOC específico) e a expansão BTX → Benzeno+Tolueno+Xileno.
"""
from controle.inteligencia_demandas import (
    extrair_agentes_multifonte,
    extrair_os_multifonte,
)


def _canonicos(agentes):
    return {a.canonical for a in agentes}


# ── extrair_agentes_multifonte ────────────────────────────────────────

def test_dosimetria_de_ruido_no_titulo():
    ags = extrair_agentes_multifonte(titulo='6528094 - LORE - dosimetria de ruído')
    assert 'Ruído Ocupacional' in _canonicos(ags)


def test_quimicos_especificos_sem_generico_de_gases():
    ags = extrair_agentes_multifonte(descricao='avaliação de tolueno e xileno na pintura')
    nomes = _canonicos(ags)
    assert 'Tolueno' in nomes
    assert 'Xileno' in nomes
    assert 'Gases e Vapores (geral)' not in nomes


def test_vci_descarta_vibracao_generica():
    ags = extrair_agentes_multifonte(descricao='vibração de corpo inteiro no empilhadeirista')
    nomes = _canonicos(ags)
    assert 'Vibração de Corpo Inteiro (VCI)' in nomes
    assert 'Vibração (geral)' not in nomes


def test_btex_expande_para_trio():
    ags = extrair_agentes_multifonte(descricao='coleta de BTEX no abastecimento')
    nomes = _canonicos(ags)
    assert {'Benzeno', 'Tolueno', 'Xileno'} <= nomes


def test_agente_em_duas_fontes_ganha_bonus_de_confianca():
    so_titulo = extrair_agentes_multifonte(titulo='dosimetria de ruído')
    duas      = extrair_agentes_multifonte(titulo='dosimetria de ruído',
                                           descricao='medição de ruído ocupacional')
    conf_1 = next(a.confianca for a in so_titulo if a.canonical == 'Ruído Ocupacional')
    conf_2 = next(a.confianca for a in duas if a.canonical == 'Ruído Ocupacional')
    assert conf_2 > conf_1


def test_texto_sem_agente_retorna_vazio():
    assert extrair_agentes_multifonte(titulo='Reunião de alinhamento comercial') == []


# ── soma de quantidades por unidade (caso Belgo OS 6540528) ───────────

_BELGO_DESC = """
GGAL - 8 Ruídos, 4 VCI, 1 VMB, 1 Manganês
GCM - 4 Ruídos, 3 VCI, 1 Chumbo
GSIC - 6 Ruídos, 4 VCI, 1 Manganês, 1 Óxido de Ferro
GSOL - 4 Ruídos, 3 VCI, 1 Manganês, 1 Óxido de Ferro
GATC - 2 VCI, 2 Ruídos, 1 Benzeno, 1 Tolueno, 1 Xileno
EMINAS - 2 Ruídos, 1 Benzeno, 1 Tolueno, 1 Xileno
"""


def _qtd(agentes, canonical):
    return next(a.quantidade for a in agentes if a.canonical == canonical)


def test_soma_mencoes_repetidas_no_mesmo_texto():
    ags = extrair_agentes_multifonte(descricao=_BELGO_DESC)
    assert _qtd(ags, 'Ruído Ocupacional') == 26
    assert _qtd(ags, 'Vibração de Corpo Inteiro (VCI)') == 16
    assert _qtd(ags, 'Manganês') == 3
    assert _qtd(ags, 'Óxido de Ferro') == 2
    assert _qtd(ags, 'Benzeno') == 2
    assert _qtd(ags, 'Tolueno') == 2
    assert _qtd(ags, 'Xileno') == 2
    assert _qtd(ags, 'Chumbo') == 1


def test_entre_fontes_vale_o_maximo_nao_soma():
    # A mesma info repetida em título e descrição NÃO pode dobrar a quantidade.
    ags = extrair_agentes_multifonte(
        titulo='4 ruídos na obra',
        descricao='realizar 4 ruídos na obra',
    )
    assert _qtd(ags, 'Ruído Ocupacional') == 4


def test_aliases_do_mesmo_canonical_nao_somam_entre_si():
    # "ruído ocupacional" também casa o alias "ruído" — vale o máximo, não 4+4.
    ags = extrair_agentes_multifonte(descricao='4 ruído ocupacional no setor')
    assert _qtd(ags, 'Ruído Ocupacional') == 4


def test_quantidade_colada_no_agente_conta():
    # "2VCI" / "2Ruídos" sem espaço: fronteira de letra aceita o dígito colado.
    ags = extrair_agentes_multifonte(descricao='GGAL - 2Ruídos e 2VCI; GCM - 3 ruídos')
    assert _qtd(ags, 'Ruído Ocupacional') == 5
    assert _qtd(ags, 'Vibração de Corpo Inteiro (VCI)') == 2


def test_btx_soma_mencoes_por_unidade():
    # Sigla BTX repetida por unidade (Belgo real): trio expande com a SOMA.
    ags = extrair_agentes_multifonte(
        descricao='FX Serviços: 1 BTX. EMINAS: 1 BTX.',
    )
    assert _qtd(ags, 'Benzeno') == 2
    assert _qtd(ags, 'Tolueno') == 2
    assert _qtd(ags, 'Xileno') == 2


# ── "N medições de X" (caso Easy Equipamentos OS 6605694) ─────────────
# A OS listava 7 agentes e a tela mostrava 4: "medições" não estava na lista
# de unidades de contagem (quantidade virava 1) e agente fora do dicionário
# sumia calado.

_EASY_DESC = """Empresa: EASY EQUIPAMENTOS ODONTOLOGICOS LTDA
CNPJ: 03.440.703/0001-29
Serviço: Avaliações ambientais

1 medição de Ácido Nítrico
1 medição de Ácido Clorídrico
1 medição de Sais de Cianeto
1 medição de Hidróxido de Sódio
1 medição de Prata
1 medição de Níquel
7 medições de Ruído"""


def test_medicoes_conta_quantidade():
    # "7 medições de Ruído" precisa virar 7, não 1.
    ags = extrair_agentes_multifonte(descricao='7 medições de Ruído')
    assert _qtd(ags, 'Ruído Ocupacional') == 7


def test_easy_extrai_os_sete_agentes():
    ags = extrair_agentes_multifonte(descricao=_EASY_DESC)
    assert len(ags) == 7, [a.canonical for a in ags]
    assert _qtd(ags, 'Ruído Ocupacional') == 7
    assert sum(a.quantidade for a in ags) == 13
    nomes = _canonicos(ags)
    for esperado in ('Ácido Nítrico', 'Ácido Clorídrico', 'Cianetos',
                     'Prata', 'Níquel', 'Soda Cáustica (NaOH)'):
        assert esperado in nomes, f'{esperado} sumiu: {nomes}'


def test_agente_fora_do_dicionario_nao_some():
    # Lista de substâncias é aberta: o que não está no dicionário ainda
    # precisa chegar ao técnico (com confiança menor).
    ags = extrair_agentes_multifonte(descricao='3 medições de Fosfina')
    assert _qtd(ags, 'Fosfina') == 3


def test_agente_livre_preserva_preposicao_minuscula():
    ags = extrair_agentes_multifonte(descricao='1 medição de Metacrilato de Metila')
    assert 'Metacrilato de Metila' in _canonicos(ags)


def test_agente_livre_nao_inventa_a_partir_de_prosa():
    # Âncora "N <unidade> de X" existe, mas X é item de processo — não é agente.
    for texto in ('1 medição de campo prevista para segunda',
                  '1 avaliação de cliente novo',
                  '3 medições de funcionários do setor',
                  '1 medição de prazo de entrega do laudo'):
        assert extrair_agentes_multifonte(descricao=texto) == [], texto


def test_sigla_de_grupo_nao_vira_agente_proprio():
    # BTX/BTEX já expande em Benzeno+Tolueno+Xileno — não pode aparecer
    # também como agente "BTX", senão a coleta é contada duas vezes.
    for texto in ('2 medições de BTX', '1 medição de BTEX no abastecimento'):
        nomes = _canonicos(extrair_agentes_multifonte(descricao=texto))
        assert nomes == {'Benzeno', 'Tolueno', 'Xileno'}, (texto, nomes)


def test_btx_com_medicoes_conta_quantidade():
    ags = extrair_agentes_multifonte(descricao='2 medições de BTX')
    assert _qtd(ags, 'Benzeno') == 2


def test_agente_livre_corta_complemento_de_lugar():
    # "Fosfina na caldeira" é medição de Fosfina, não de um agente chamado
    # "Fosfina Na Caldeira".
    ags = extrair_agentes_multifonte(descricao='3 medições de Fosfina na caldeira')
    assert _canonicos(ags) == {'Fosfina'}
    assert _qtd(ags, 'Fosfina') == 3


def test_medicoes_nao_derruba_soma_multiunidade():
    # Estratégia de menção isolada não pode reduzir a soma por unidade.
    ags = extrair_agentes_multifonte(
        descricao='GGAL - 3 medições de ruído. GCM - 4 medições de ruído.'
    )
    assert _qtd(ags, 'Ruído Ocupacional') == 7


# ── formato antigo "N - Medição X" (caso Petronas OS 60102) ───────────
# Lista de 13 medições em que só 8 eram lidas: o nome do agente aceitava \s
# (incluindo \n), então o regex atravessava linhas e o finditer pulava itens.

_PETRONAS_DESC = """16 - Medições de Ruído
2 - Medições VMB
3 - Medições de Nevoas de Óleos
2 - Medições de Calor
2 - Medições Etanol
1 - Medição Tolueno
1 - Medição Querosene
1 - Medição Hidroxido de Sódio
1 - Medição Hidroxido de Potassio
1 - Medição Heptano
1 - Medição Eter Etilico
1 - Medição Cloroformio
1 - Medição Anilina"""


def test_lista_multilinha_nao_pula_itens():
    nomes = _canonicos(extrair_agentes_multifonte(descricao=_PETRONAS_DESC))
    for esperado in ('Querosene', 'Heptano', 'Anilina', 'Clorofórmio',
                     'Éter Etílico', 'Névoas de Óleo', 'Hidróxido de Potássio',
                     'Tolueno', 'Soda Cáustica (NaOH)'):
        assert esperado in nomes, f'{esperado} sumiu: {sorted(nomes)}'


def test_formato_com_hifen_conta_quantidade():
    ags = extrair_agentes_multifonte(descricao=_PETRONAS_DESC)
    assert _qtd(ags, 'Ruído Ocupacional') == 16
    assert _qtd(ags, 'Vibração de Mão-Braço (VMB)') == 2
    assert _qtd(ags, 'Névoas de Óleo') == 3


def test_unidade_sem_preposicao_de():
    # "1 - Medição Querosene" (sem "de") é formato real das OS antigas.
    ags = extrair_agentes_multifonte(descricao='1 - Medição Querosene')
    assert _canonicos(ags) == {'Querosene'}


def test_substancia_nao_vira_o_agente_contido_no_nome():
    # "Clorofórmio" contém "cloro"; "Etilbenzeno" contém "benzeno".
    assert _canonicos(extrair_agentes_multifonte(
        descricao='1 medição de Cloroformio')) == {'Clorofórmio'}
    assert _canonicos(extrair_agentes_multifonte(
        descricao='1 medição de Etilbenzeno')) == {'Etilbenzeno'}
    # mas o agente sozinho continua sendo lido
    assert _canonicos(extrair_agentes_multifonte(
        descricao='2 medições de Cloro')) == {'Cloro'}


def test_sigla_curta_conhecida_nao_duplica_no_fallback():
    # "04 Pontos de VMB" devolvia o canônico E um agente solto "VMB".
    assert _canonicos(extrair_agentes_multifonte(
        descricao='04 Pontos de VMB')) == {'Vibração de Mão-Braço (VMB)'}


# ── extrair_os_multifonte ─────────────────────────────────────────────

def test_os_explicita_no_titulo():
    numero, conf, fontes = extrair_os_multifonte(titulo='OS 6531907 - STRATA ENGENHARIA')
    assert numero == '6531907'
    assert conf >= 0.9
    assert fontes


def test_ano_nao_vira_os():
    numero, _, _ = extrair_os_multifonte(titulo='PGR 2026')
    assert numero is None


def test_mesmo_numero_em_duas_fontes_ganha_bonus():
    _, conf_1, _ = extrair_os_multifonte(titulo='OS 123456')
    _, conf_2, _ = extrair_os_multifonte(titulo='OS 123456', descricao='referente à OS 123456')
    assert conf_2 > conf_1
