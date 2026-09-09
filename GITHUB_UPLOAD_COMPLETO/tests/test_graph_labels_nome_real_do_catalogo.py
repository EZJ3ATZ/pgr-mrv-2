# -*- coding: utf-8 -*-
"""Labels do Planner a partir do NOME REAL do produto no catálogo do CRM.

Medido em 09/09/2026 (jornada completa): o produto de PGR no catálogo chama-se
'Documentação Especifica - PGR - Programa de Gerenciamento de Riscos - NR-01'
(categoria 'gestao'). Nenhuma regra de get_category_ids_by_names casava com o
label 'PGR/PCMSO' e a task de engenharia nascia sem label no quadro.
"""
import controle.graph as graph_mod

_CAT_MAP = {
    'category5': 'TREINAMENTO', 'category6': 'PGR/PCMSO', 'category7': 'PCMSO',
    'category8': 'LTCAT', 'category9': 'LIP', 'category10': 'MEDIÇÕES',
    'category11': 'RELATÓRIO TÉCNICO', 'category12': 'PPR', 'category23': 'Relatório Analítico',
}


def _labels(monkeypatch, nomes):
    monkeypatch.setattr(graph_mod, 'get_plan_category_map', lambda pid: dict(_CAT_MAP))
    return set(graph_mod.get_category_ids_by_names('PL', nomes))


def test_pgr_com_nome_completo_do_catalogo_cai_em_pgr_pcmso(monkeypatch):
    assert _labels(monkeypatch, [
        'Documentação Especifica - PGR - Programa de Gerenciamento de Riscos - NR-01']) == {'category6'}
    assert _labels(monkeypatch, [
        'Elaboração e Gestão de PGR - Programa de Gerenciamento de Riscos - NR-18 - Construção Cívil']) == {'category6'}


def test_pcmso_com_nome_completo_cai_no_exato_nao_no_composto(monkeypatch):
    assert _labels(monkeypatch, [
        'Documentação Especifica - PCMSO - Programa de Controle Médico e Saúde Ocupacional - NR-07']) == {'category7'}


def test_ltcat_nao_ganha_relatorio_tecnico_de_brinde(monkeypatch):
    assert _labels(monkeypatch, ['LTCAT - Laudo Técnico das Condições Ambientais do Trabalho']) == {'category8'}


def test_regras_antigas_continuam(monkeypatch):
    assert _labels(monkeypatch, ['PGR/PCMSO', 'TREINAMENTO']) == {'category6', 'category5'}
    assert _labels(monkeypatch, ['Treinamento NR-35']) == {'category5'}
    assert _labels(monkeypatch, ['Ruído']) == set()
