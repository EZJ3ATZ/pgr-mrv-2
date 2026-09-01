# -*- coding: utf-8 -*-
"""Dois furos da conferencia de metodo, medidos na guia real (01/09/2026).

1. AMOSTRADOR EM PAR. O padrao antigo exigia os asteriscos encostados no
   fecha-parenteses, entao 'SKC 226-09 E 226-01 (TCG**** E TCP****)' devolvia
   [] e a conferencia era PULADA em silencio. Eram 46 dos 512 metodos — todos
   par de tubos em serie ou sufixo colado ('(PVC*****NBR)').

2. VAZAO POR MATERIAL. '1,7 NYLON OU 2,0 SKC OU ... OU 2,75 GS-3' era lido como
   faixa continua 1,7-3,0: o teto 3,0 vem do NOME 'GS-3', nao de vazao nenhuma.
   Sao 26 metodos, os de poeira respiravel e silica.
"""
import json
import os

import pytest

from controle.validacao_metodo import (
    _tipos_do_metodo, alvos_por_material, faixa, validar_coleta,
)

_GUIA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'guia_metodos.json')
VAZAO_5 = '1,7 NYLON OU 2,0 SKC OU 2,2 HD OU 2,5 ALUMÍNIO OU 2,75 GS-3'


@pytest.mark.parametrize('cod,esperado', [
    ('SKC 226-01 (TCP*****)', ['TCP']),                       # forma simples
    ('SKC 226-09 E 226- 01 (TCG**** E TCP****)', ['TCG', 'TCP']),
    ('SKC 225-7 E SKC 226- (IOL***** E X2P**** )', ['IOL', 'X2P']),
    ('PVC NBR 17037 (PVC*****NBR)', ['PVC']),                 # sufixo colado
    ('226-09 E 226-01 (CLM**** )', ['CLM']),                  # espaco antes do )
    ('SKC 226-30-08 (X8P**** E X8P****)', ['X8P']),           # repetido = 1
    ('', []),
    (None, []),
])
def test_le_o_par_de_amostradores(cod, esperado):
    assert _tipos_do_metodo(cod) == esperado


def test_nenhum_metodo_da_guia_escapa_da_conferencia():
    """Antes: 46 dos 512 devolviam [] e passavam sem conferir."""
    guia = json.load(open(_GUIA, encoding='utf-8'))
    metodos = [e for lst in guia['by_cas'].values()
               for e in (lst if isinstance(lst, list) else [lst])
               if isinstance(e, dict)]
    sem_tipo = [e.get('nome') for e in metodos
                if not _tipos_do_metodo(e.get('amostradorCod'))]
    assert sem_tipo == [], f'{len(sem_tipo)} metodos ainda sem tipo: {sem_tipo[:5]}'


def test_alvos_por_material_separa_valor_e_ciclone():
    assert alvos_por_material(VAZAO_5) == [
        (1.7, 'NYLON'), (2.0, 'SKC'), (2.2, 'HD'),
        (2.5, 'ALUMÍNIO'), (2.75, 'GS-3'),
    ]


def test_faixa_continua_nao_serve_para_esse_texto():
    """Registro do defeito: o teto 3,0 vem do nome 'GS-3'."""
    assert faixa(VAZAO_5) == (1.7, 3.0)


def _vazao(v):
    met = {'nome': 'Sílica', 'vazao': VAZAO_5, 'volume': '',
           'amostradorCod': 'PVC NBR 17037 (PVC*****NBR)', 'metodoCod': 'NIOSH 7500'}
    r = validar_coleta(met, vazao=v, hora_inicio='08:00', hora_final='12:00')
    return [i for i in r['itens'] if i['campo'] == 'vazão'][0]


@pytest.mark.parametrize('v,ciclone', [
    (1.7, 'NYLON'), (2.0, 'SKC'), (2.2, 'HD'), (2.5, 'ALUMÍNIO'), (2.75, 'GS-3'),
])
def test_vazao_de_ciclone_real_e_aprovada_dizendo_qual(v, ciclone):
    item = _vazao(v)
    assert item['ok'] is True
    assert item['alvo_material'] == ciclone


def test_vazao_acima_do_maior_ciclone_reprova():
    """3,0 era APROVADO porque a faixa ia ate 3,0 (o '3' de 'GS-3')."""
    item = _vazao(3.0)
    assert item['ok'] is False, item
