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
