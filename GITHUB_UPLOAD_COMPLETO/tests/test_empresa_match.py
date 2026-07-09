# -*- coding: utf-8 -*-
"""Matching Planner → empresa: extração de OS/CNPJ/nome e normalização.

Protege contra a regressão em que 'ABC Engenharia' e 'ABC Comercio'
normalizavam para 'abc' e casavam com score 1.0 (demanda vinculada
à empresa errada).
"""
from controle.empresa_match import extrair_campos, normalizar_nome, similaridade


# ── extrair_campos: formatos reais de título do Planner ──────────────

def test_os_com_dash():
    r = extrair_campos('6077430 - CONSTRUTORA ALFA LTDA')
    assert r['os'] == '6077430'
    assert r['nome'] == 'CONSTRUTORA ALFA LTDA'


def test_os_com_virgula_e_sufixo_de_status():
    r = extrair_campos('54394, EMPRESA BETA, EMPRESA BETA, 516 DIAS - PROCESSO ANTIGO')
    assert r['os'] == '54394'
    assert r['nome'] == 'EMPRESA BETA'


def test_os_com_espaco_minimo_5_digitos():
    r = extrair_campos('6466572 GAMA ENGENHARIA')
    assert r['os'] == '6466572'


def test_os_apos_label_de_setor():
    r = extrair_campos('MEDIÇÕES - 6076827 - ACME INDUSTRIAL')
    assert r['os'] == '6076827'
    assert r['nome'] == 'ACME INDUSTRIAL'


def test_nome_comecando_com_numero_nao_vira_os():
    assert extrair_campos('2A Engenharia')['os'] is None
    assert extrair_campos('3 Corações')['os'] is None


def test_cnpj_extraido_e_normalizado():
    r = extrair_campos('EMPRESA X 12.345.678/0001-90')
    assert r['cnpj'] == '12345678000190'


# ── normalizar_nome / similaridade ────────────────────────────────────

def test_normalizar_remove_sufixo_societario():
    assert normalizar_nome('Construtora Alfa LTDA.') == 'construtora alfa'


def test_normalizar_preserva_palavra_de_ramo():
    # 'engenharia' e 'comercio' distinguem empresas — não podem ser removidas.
    assert normalizar_nome('ABC Engenharia') != normalizar_nome('ABC Comercio')


def test_similaridade_mesma_empresa_com_sufixo_e_alta():
    assert similaridade('ABC Engenharia LTDA', 'ABC Engenharia') > 0.95


def test_similaridade_ramos_diferentes_nao_e_match_perfeito():
    mesmo  = similaridade('ABC Engenharia LTDA', 'ABC Engenharia')
    outro  = similaridade('ABC Engenharia', 'ABC Comercio')
    assert outro < 1.0
    assert outro < mesmo


def test_similaridade_vazio_e_zero():
    assert similaridade('', 'ABC') == 0.0
    assert similaridade('ABC', '') == 0.0
