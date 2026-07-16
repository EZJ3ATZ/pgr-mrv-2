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
