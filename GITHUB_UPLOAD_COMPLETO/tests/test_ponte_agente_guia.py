# -*- coding: utf-8 -*-
"""Ponte canônico do motor -> nome do guia do laboratorio (01/09/2026).

_buscar_metodos_agente resolve por substring escolhendo o nome MAIS CURTO que
contem a chave. Medido contra o guia real: dos 58 canonicos quimicos, 31 nao
tem nome exato la. A maioria cai em sinonimo certo, mas XILENO casava dentro de
"HEXILENO GLICOL" — outra substancia — e xileno e um terco do BTX, o combo mais
medido. Outros 7 sao genericos que o guia so tem em variantes especificas
(Niquel -> "Niquel Carbonila", Alcool -> "Alcool benzilico"): cada uma tem
metodo e limite proprios, entao responder qualquer uma e mentir com confianca.
"""
import pytest

from controle.routes import _buscar_metodos_agente


def _nome(agente):
    m = _buscar_metodos_agente(agente)
    if not m:
        return None
    return m[0].get('nome') if isinstance(m[0], dict) else str(m[0])


@pytest.mark.parametrize('agente,esperado', [
    ('Xileno', 'Xileno'),                       # era "Hexileno glicol"
    ('XILENO', 'Xileno'),
    ('Benzeno', 'Benzeno'),                     # BTX nao pode regredir
    ('Tolueno', 'Tolueno'),
    ('BTX', 'Benzeno'),
    ('Sílica Cristalina', 'Silica Livre'),
    ('MEK (Butanona)', 'Metil etil cetona'),
    ('Soda Cáustica (NaOH)', 'Hidroxido de sodio'),
    ('Ácido Clorídrico', 'Cloreto de hidrogenio'),
    ('Óxido de Ferro', 'Ferro'),
    ('Manganês', 'Manganes'),
    ('Hexano', 'Hexano'),
])
def test_resolve_para_a_substancia_certa(agente, esperado):
    import unicodedata

    def sem_acento(s):
        s = unicodedata.normalize('NFD', (s or '').lower())
        return ''.join(c for c in s if unicodedata.category(c) != 'Mn')

    got = _nome(agente)
    assert got is not None, f'{agente} nao resolveu'
    assert sem_acento(esperado) in sem_acento(got), f'{agente} -> {got}'


@pytest.mark.parametrize('agente', [
    'Níquel', 'Cromo', 'Mercúrio', 'Álcool', 'Estanho', 'Cobre', 'Querosene',
    # As mesmas sem acento: a lista de ambiguos passou a ser comparada sem
    # acento em 08/09, senao 'Niquel' escapava do bloqueio pela grafia.
    'Niquel', 'Mercurio', 'Alcool',
])
def test_generico_ambiguo_nao_chuta_variante(agente):
    """Melhor a tela dizer "metodo nao encontrado, preencha a vazao" do que
    trazer o metodo de outra variante com cara de certo."""
    assert _nome(agente) is None, f'{agente} chutou {_nome(agente)}'


@pytest.mark.parametrize('agente,esperado', [
    ('Manganes', 'Manganes'),
    ('Silica Cristalina', 'Silica Livre'),
    ('Acido Acetico', 'Acido acetico'),
    ('Oleo mineral, excluidos os fluidos de trabalho com metais', 'Oleo mineral'),
])
def test_grafia_sem_acento_resolve(agente, esperado):
    """Era limite registrado ("a ponte compara a grafia crua"), virou requisito
    em 08/09: o tecnico digita sem acento e o guia e todo acentuado.

    Enquanto a comparacao era crua, a chave sem acento nao alcancava o nome e
    caia no fuzzy — foi assim que 'Silica' virou Silicato de calcio. Medido nos
    408 nomes do guia em 4 grafias cada: 341 dos 1.632 casos resolviam para a
    entrada ERRADA; agora sao 0.
    """
    import unicodedata

    def sem_acento(s):
        s = unicodedata.normalize('NFD', (s or '').lower())
        return ''.join(c for c in s if unicodedata.category(c) != 'Mn')

    got = _nome(agente)
    assert got is not None, f'{agente} nao resolveu'
    assert sem_acento(esperado) in sem_acento(got), f'{agente} -> {got}'


def test_silica_nao_cai_em_silicato():
    """SILICA e prefixo de SILICATO, e substring solta aceitava isso.

    O tecnico escrevia "Silica" e recebia Silicato de calcio (Wollastonite):
    OSHA ID-121, 480 a 960 L, amostrador IEC — quando o certo e NIOSH 7500,
    400 a 1000 L, PVC. Duas coletas reais de 2026 (LCA 05/06 e Ecomining
    18/06) foram conferidas contra o metodo errado por causa disto.
    """
    m = _buscar_metodos_agente('Silica')
    assert m, 'Silica deixou de resolver'
    nome = (m[0].get('nome') or '').lower()
    assert 'silicato' not in nome and 'wollastonite' not in nome, nome
    assert m[0].get('metodoCod') == 'NIOSH 7500', m[0].get('metodoCod')
    assert 'PVC' in (m[0].get('amostradorCod') or '')


def test_nome_do_guia_com_quebra_de_linha_resolve_colado():
    """54 dos 408 nomes do guia tem \\n (o guia nasceu de PDF) e o <input> do
    navegador APAGA o \\n do value sem deixar espaco.

    'Poeira Respiravel + Silica Livre\\nCristalina' chega colado como
    '...LivreCristalina'; sem casamento cego a espaco isso caia na busca
    inversa e voltava o metodo da POEIRA sozinha (NIOSH 0600, 20 a 400 L),
    sem o NIOSH 7500 que e a parte da silica. Esta grafia colada esta gravada
    em coleta real de producao (LPC, 10/08/2026).
    """
    for grafia in ('Poeira Respirável + Sílica LivreCristalina',
                   'Poeira Respirável + Sílica Livre Cristalina',
                   'Poeira Respiravel + Silica LivreCristalina'):
        m = _buscar_metodos_agente(grafia)
        assert m, f'{grafia} nao resolveu'
        assert m[0].get('metodoCod') == 'NIOSH 0600 E NIOSH 7500', \
            f'{grafia} -> {m[0].get("metodoCod")}'
        assert '400' in (m[0].get('volume') or ''), \
            f'{grafia} -> volume {m[0].get("volume")}'
