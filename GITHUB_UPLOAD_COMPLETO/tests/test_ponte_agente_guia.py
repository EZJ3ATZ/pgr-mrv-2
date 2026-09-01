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
])
def test_generico_ambiguo_nao_chuta_variante(agente):
    """Melhor a tela dizer "metodo nao encontrado, preencha a vazao" do que
    trazer o metodo de outra variante com cara de certo."""
    assert _nome(agente) is None, f'{agente} chutou {_nome(agente)}'


def test_grafia_sem_acento_nao_e_coberta():
    """Registro do limite: a ponte compara a grafia crua, entao 'Manganes' sem
    acento nao resolve. O motor sempre emite o canonico acentuado, entao isso
    nao afeta o fluxo — mas quem chamar a rota na mao precisa saber."""
    assert _nome('Manganes') is None
