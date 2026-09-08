# -*- coding: utf-8 -*-
"""BTX e BTXE: uma definição só, lida pelos dois endpoints.

Os combos que o laboratório analisa num tubo só não existem como entrada no
guia — lá há Benzeno, Tolueno, Xileno e Etilbenzeno separados. A definição
estava cravada dentro do `/controle/agentes`, e só aquele endpoint sabia dela:
`/controle/agente/<nome>/estoque` caía no fuzzy e devolvia o **Benzeno**, com a
faixa de volume dele ('STEL: 3L TWA: 5 A 30L') no lugar da faixa do combo. Dois
endpoints, o mesmo agente, respostas diferentes — e quem a planilha de campo
consulta é o `/estoque`.

Pior: `AGENTE_ALIASES` mandava 'BTX', 'BTXE' e as grafias por extenso
('BENZENO, TOLUENO, XILENO') todas para 'BENZENO'. Então digitar o combo
entregava um teto de 30 L para um tubo que também carrega Tolueno, que satura
em 8 L.

Os números da definição são os que já estavam em produção e não foram mexidos
neste commit — trocar '2 A 10 L' muda quanto tempo a bomba fica ligada no
campo, e a nota em AGENTE_GRUPOS registra a pergunta aberta.
"""
import re

import pytest

from app import app
from controle.routes import AGENTE_GRUPOS, _buscar_metodos_agente

BTX = 'BTX (Benzeno + Tolueno + Xileno)'
BTXE = 'BTXE (Benzeno + Tolueno + Xileno + Etilbenzeno)'


@pytest.mark.parametrize('grafia,esperado', [
    ('BTX', BTX),
    ('btx', BTX),
    ('BTX (Benzeno + Tolueno + Xileno)', BTX),
    ('Benzeno, Tolueno e Xileno', BTX),
    ('Benzeno, Tolueno, Xileno', BTX),
    ('BTXE', BTXE),
    ('BTXE (Benzeno + Tolueno + Xileno + Etilbenzeno)', BTXE),
])
def test_grafias_do_combo_caem_no_combo(grafia, esperado):
    m = _buscar_metodos_agente(grafia)
    assert m, f'{grafia} não resolveu'
    assert m[0].get('nome') == esperado, m[0].get('nome')
    assert m[0].get('volume') == '2 A 10 L', m[0].get('volume')


def test_btx_nao_responde_pelo_btxe():
    """Chaves distintas: uma sigla não pode cair no combo da outra."""
    assert _buscar_metodos_agente('BTX')[0]['nome'] == BTX
    assert _buscar_metodos_agente('BTXE')[0]['nome'] == BTXE


@pytest.mark.parametrize('agente,volume', [
    ('Benzeno', 'STEL: 3L TWA: 5 A 30L'),
    ('Tolueno', '1 A 8 L'),
])
def test_componentes_sozinhos_continuam_intactos(agente, volume):
    """O lookup devolve a entrada CRUA do guia, e ela vem quebrada do PDF
    (o volume do Benzeno tem uma quebra de linha antes do '30L'), entao a
    comparacao e feita em uma linha."""
    m = _buscar_metodos_agente(agente)
    assert m, f'{agente} nao resolveu'
    achado = re.sub(r'\s+', ' ', m[0].get('volume') or '').strip()
    assert achado == volume, achado


def test_os_dois_endpoints_dao_a_mesma_resposta():
    """O defeito, em uma linha: /agentes e /agente/<nome>/estoque discordavam."""
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = '1'
            s['_fresh'] = True
        lista = cli.get('/controle/agentes').get_json()
        est = cli.get('/controle/agente/' + BTX.replace(' ', '%20') + '/estoque').get_json()

    # o endpoint devolve {'agentes': [...]}, nao a lista solta
    agentes = lista.get('agentes') if isinstance(lista, dict) else lista
    do_endpoint_de_lista = [a for a in (agentes or []) if a.get('nome') == BTX]
    assert do_endpoint_de_lista, 'BTX saiu da lista de agentes'
    a = do_endpoint_de_lista[0]
    m = (est.get('metodos') or [])[0]
    assert m, est.get('aviso')
    assert (a['metodo'], a['volume'], a['amostrador']) == \
        (m['metodoCod'], m['volume'], m['amostradorCod']), (a, m)


def test_definicao_e_unica_e_tem_o_que_a_tela_precisa():
    assert set(AGENTE_GRUPOS) == {
        'BTX (BENZENO + TOLUENO + XILENO)',
        'BTXE (BENZENO + TOLUENO + XILENO + ETILBENZENO)'}
    for chave, g in AGENTE_GRUPOS.items():
        for campo in ('nome', 'cas', 'metodoCod', 'vazao', 'volume',
                      'amostradorCod', 'amostradorDesc', 'unidade'):
            assert str(g.get(campo) or '').strip(), (chave, campo)
