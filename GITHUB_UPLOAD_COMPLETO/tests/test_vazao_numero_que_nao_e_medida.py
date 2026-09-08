# -*- coding: utf-8 -*-
"""`parse_vazao` para de ler duração e fórmula química como vazão.

Medido nas 80 strings de vazão que a guia realmente tem (08/09/2026): 4 tinham
número que não é medida, e 3 erravam por fator de 10 a 1000.

    '*TWA: 1L/MIN (240MIN) ... *STEL: 2L/MIN (15MIN)'      teto 240 L/min
    '0,025 L/MIN - 0,2 L/MIN (15 MINUTOS DE AMOSTRAGEM)'   teto  15 L/min
    'TWA: 0,01 A 0,05 L/MIN STEL CO2 MÁX: 0,3 L/MIN'       teto 2,0 (o 2 do CO2)
    '1,7 NYLON OU ... OU 2,75 GS-3'                        teto 3,0 (o 3 do GS-3)

Bomba de amostragem trabalha em torno de 2 L/min. A 'media' da primeira dava
**120,5 L/min**, e é ela que o botão "⚡ Média" da planilha de campo aplica no
amostrador; o "⬆ Teto" aplicava o `max`.

A DECISÃO de min/max/recomendada não mudou — mudou quais números entram na
conta. Quem sabe ler a guia é o `validacao_metodo` (duração entre parênteses,
concentração de referência, ponto de milhar); aqui é reuso, não regra nova.
"""
import json
import os
import re

import pytest

from controle.routes import faixa_volume, parse_vazao

CICLONES = '1,7 NYLON OU 2,0 SKC OU 2,2 HD OU 2,5 ALÚMINIO OU 2,75 GS-3'


@pytest.mark.parametrize('texto,mini,maxi', [
    # o numero da DURACAO nao e vazao
    ('*TWA: 1L/MIN (240MIN) *VAPORES E MISTURAS 2L/MIN (120MIN) *STEL: 2L/MIN (15MIN)',
     1.0, 2.0),
    ('0,025 L/MIN - 0,2 L/MIN (15 MINUTOS DE AMOSTRAGEM)', 0.025, 0.2),
    # o 2 de CO2 e formula
    ('TWA: 0,01 A 0,05 L/MIN STEL CO2 MÁX: 0,3 L/MIN', 0.01, 0.3),
    # o 3 de GS-3 e nome de ciclone: o teto real e o maior ALVO, 2,75
    (CICLONES, 1.7, 2.75),
])
def test_numero_que_nao_e_medida_fica_fora(texto, mini, maxi):
    r = parse_vazao(texto)
    assert (r['min'], r['max']) == (mini, maxi), r


def test_media_do_ciclone_sai_do_maior_alvo_real():
    """A media alimenta o botao que aplica a vazao no amostrador."""
    r = parse_vazao(CICLONES)
    assert r['media'] == 2.225, r['media']       # era 2.35, com teto 3,0
    assert r['recomendada'] == 1.7, r['recomendada']


@pytest.mark.parametrize('texto,esperado', [
    ('0,02 A 0,2 L/MIN', (0.02, 0.2)),
    ('1 A 4 L/MIN', (1.0, 4.0)),
    ('2 L/MIN', (2.0, 2.0)),
])
def test_formatos_normais_nao_mudaram(texto, esperado):
    r = parse_vazao(texto)
    assert (r['min'], r['max']) == esperado, r


def test_maximo_continua_sem_piso():
    r = parse_vazao('MÁXIMO 0,1 L/MIN')
    assert r['min'] is None and r['max'] == 0.1 and r['recomendada'] == 0.1, r


@pytest.mark.parametrize('texto', ['', '0', None])
def test_passivo(texto):
    assert parse_vazao(texto)['passivo'] is True


# ── volume: a tela para de reparsear a string ──────────────────────────

@pytest.mark.parametrize('texto,esperado', [
    ('45 A 1.000 L', {'min': 45.0, 'max': 1000.0}),   # cliente lia 1 a 45
    ('400L A 1000L', {'min': 400.0, 'max': 1000.0}),
    ('20 l a 400 l @ 5mg/m³', {'min': 20.0, 'max': 400.0}),
    ('MÍNIMO 480 L', {'min': 480.0, 'max': None}),
])
def test_faixa_volume_para_a_tela(texto, esperado):
    assert faixa_volume(texto) == esperado


def test_nenhum_valor_lido_vem_de_duracao_ou_de_formula():
    """Guarda no catálogo inteiro, e não em amostra.

    A régua aqui é escrita ao contrário da de produção de propósito: em vez de
    extrair o que vale, marca o que NÃO pode virar vazão — número dentro de
    parêntese de duração ('(240MIN)', '(15 MINUTOS DE AMOSTRAGEM)') e número
    colado em letra ou depois de hífen de código ('CO2', 'GS-3', 'GS-1'). Se
    algum dos dois voltar a entrar na faixa, quebra aqui.

    Não uso limite de grandeza: 16 L/min parecia absurdo e é real — é o método
    de asbesto (ABNT NBR 13.158/94, amostrador ASB).
    """
    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'guia_metodos.json')
    with open(caminho, encoding='utf-8') as f:
        guia = json.load(f)

    # Duração é número que ENCOSTA no MIN: '(240MIN)', '(15 MINUTOS ...)'.
    # '(1L/MIN)' não é duração, é vazão entre parênteses — o 'L/' no meio é o
    # que separa os dois casos, e a régua de produção usa a mesma distinção.
    dentro_de_duracao = re.compile(r'(\d+(?:[.,]\d+)?)\s*MIN(?:UTOS)?\b', re.I)
    colado_em_letra = re.compile(r'(?:[A-Za-zÀ-ÿ]|[A-Za-zÀ-ÿ]-)(\d+(?:[.,]\d+)?)')

    def _n(txt):
        return float(str(txt).replace('.', '').replace(',', '.')) \
            if ',' in str(txt) else float(str(txt))

    ruins = []
    for cas, entradas in guia['by_cas'].items():
        for e in (entradas if isinstance(entradas, list) else [entradas]):
            bruto = re.sub(r'\s+', ' ', e.get('vazao') or '')
            if not bruto:
                continue
            proibidos = {_n(x) for x in dentro_de_duracao.findall(bruto)}
            proibidos |= {_n(x) for x in colado_em_letra.findall(bruto)}
            # um numero pode aparecer nos dois papeis na mesma string; so
            # reprova o que NAO aparece tambem como token limpo. A duracao sai
            # antes de contar os limpos, senao o 240 de '(240MIN)' passaria por
            # token limpo (vem depois de '(') e a guarda ficaria cega
            # justamente no pior caso.
            sem_duracao = re.sub(r'\d+(?:[.,]\d+)?\s*MIN(?:UTOS)?\b', ' ',
                                 bruto, flags=re.I)
            limpos = {_n(x) for x in re.findall(
                r'(?<![A-Za-zÀ-ÿ0-9,.\-])(\d+(?:[.,]\d+)?)', sem_duracao)}
            proibidos -= limpos
            r = parse_vazao(bruto)
            for campo in ('min', 'max', 'recomendada'):
                v = r[campo]
                if v is not None and v in proibidos:
                    ruins.append((bruto, campo, v))
    assert ruins == [], ruins
