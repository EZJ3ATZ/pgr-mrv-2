# -*- coding: utf-8 -*-
"""Laudo de calor — Quadro 1 do Anexo 3 da NR-15 (Portaria 1.359/2019) e IBUTG.

Protege contra a regressão que truncava a tabela em 346 W (todo trabalho
pesado ganhava limite 27,5 ºC, mais permissivo que a norma).
"""
import pytest

from app import get_limite_nr15, _ibutg_ponto


# ── get_limite_nr15: âncoras exatas da tabela oficial ─────────────────

@pytest.mark.parametrize('m, limite', [
    (100, 33.7),   # primeiro ponto
    (135, 32.2),
    (209, 30.0),
    (346, 27.5),   # onde a tabela antiga truncava
    (467, 26.0),
    (606, 24.7),   # último ponto
])
def test_limite_nr15_ancoras_da_tabela(m, limite):
    assert get_limite_nr15(m) == limite


def test_limite_nr15_clamp_abaixo_e_acima():
    assert get_limite_nr15(50) == 33.7    # abaixo da tabela → primeiro limite
    assert get_limite_nr15(999) == 24.7   # acima da tabela → último limite


def test_limite_nr15_trabalho_pesado_nao_e_mais_27_5():
    # Regressão: com a tabela truncada, 550 W devolvia 27,5 (permissivo demais).
    assert get_limite_nr15(550) < 26.0


def test_limite_nr15_monotonicamente_decrescente():
    limites = [get_limite_nr15(m) for m in range(100, 607)]
    for anterior, atual in zip(limites, limites[1:]):
        assert atual <= anterior


def test_limite_nr15_interpolacao_fica_entre_vizinhos():
    # 350 W está entre 346 (27,5) e 353 (27,4)
    v = get_limite_nr15(350)
    assert 27.4 <= v <= 27.5


# ── _ibutg_ponto: com e sem carga solar ───────────────────────────────

def test_ibutg_interno_sem_tbs():
    ibutg, formula = _ibutg_ponto({'tbn': 25, 'tg': 30})
    assert ibutg == 26.5                      # 0,7·25 + 0,3·30
    assert '0,3' in formula
    assert '0,1' not in formula


def test_ibutg_externo_com_tbs():
    ibutg, formula = _ibutg_ponto({'tbn': 25, 'tbs': 32, 'tg': 30})
    assert ibutg == 26.7                      # 0,7·25 + 0,1·32 + 0,2·30
    assert '0,1' in formula


@pytest.mark.parametrize('tbs', [None, '', 0, '0', 'abc'])
def test_ibutg_tbs_vazio_ou_invalido_usa_formula_interna(tbs):
    ibutg, formula = _ibutg_ponto({'tbn': 25, 'tbs': tbs, 'tg': 30})
    assert ibutg == 26.5
    assert '0,3' in formula


def test_ibutg_arredonda_para_uma_casa():
    ibutg, _ = _ibutg_ponto({'tbn': 25.55, 'tg': 28.33})
    assert ibutg == round(0.7 * 25.55 + 0.3 * 28.33, 1)
