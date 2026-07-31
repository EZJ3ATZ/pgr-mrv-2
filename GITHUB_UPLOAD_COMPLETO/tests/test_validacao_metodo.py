# -*- coding: utf-8 -*-
"""Conferência da coleta contra a guia de métodos.

Os textos de faixa aqui são cópias literais do `guia_metodos.json` — foi lendo
esses formatos que apareceram os dois erros de parsing que quase reprovaram uma
coleta boa (ponto de milhar lido como decimal, e comparação de tipo de amostrador
contra o texto completo do método).
"""
import pytest

from controle.validacao_metodo import (
    num_br, faixa, minutos_entre, validar_coleta, melhor_metodo,
    _tipos_do_metodo, _tipo_confere, OK, FORA, SEM_METODO, SEM_DADO,
)


# ── número pt-BR: ponto é milhar ───────────────────────────────────────

def test_num_br_ponto_e_milhar():
    # '45 A 1.000 L' lido como 1–45 reprovava volume de 120,6 L que está dentro.
    assert num_br('1.000') == 1000.0
    assert num_br('12.500') == 12500.0


def test_num_br_virgula_e_decimal():
    assert num_br('0,02') == 0.02
    assert num_br('2,5') == 2.5
    assert num_br('1.234,56') == 1234.56


def test_num_br_lixo():
    assert num_br('') is None
    assert num_br(None) is None
    assert num_br('abc') is None


# ── faixas, nos formatos que a guia realmente usa ──────────────────────

@pytest.mark.parametrize('texto,esperado', [
    ('0,02 A 0,2 L/MIN', (0.02, 0.2)),
    ('1 A 10 L', (1.0, 10.0)),
    ('45 A 1.000 L', (45.0, 1000.0)),
    ('3 A 30 L', (3.0, 30.0)),
    ('1 A 4 L/MIN', (1.0, 4.0)),
    ('1,7 NYLON OU 2,0\nSKC OU\n2,2 HD OU 2,5\nALÚMINIO', (1.7, 2.5)),
])
def test_faixa_dos_textos_reais(texto, esperado):
    assert faixa(texto) == esperado


def test_faixa_maximo_abre_o_piso():
    assert faixa('MÁXIMO 6 L') == (0.0, 6.0)


def test_faixa_vazia():
    assert faixa('') == (None, None)
    assert faixa(None) == (None, None)


def test_minutos_entre():
    assert minutos_entre('14:45', '15:45') == 60
    assert minutos_entre('11:45', '12:03') == 18
    assert minutos_entre('23:30', '00:30') == 60      # vira o dia
    assert minutos_entre('', '12:00') is None


# ── tipo de amostrador ─────────────────────────────────────────────────

def test_tipos_do_metodo():
    assert _tipos_do_metodo('SKC 226-01 (TCP*****)') == ['TCP']
    assert _tipos_do_metodo('SKC 225-5 (EC*****)') == ['EC']
    assert _tipos_do_metodo('') == []


def test_tipo_confere_com_texto_completo():
    # O campo da coleta às vezes guarda o texto todo — comparar direto dava
    # falso positivo de "amostrador errado".
    assert _tipo_confere('SKC 225-5 (EC*****)', 'SKC 225-5 (EC*****)') is True
    assert _tipo_confere('EC', 'SKC 225-5 (EC*****)') is True
    assert _tipo_confere('TCP', 'SKC 225-5 (EC*****)') is False


def test_tipo_sem_dado_nao_reprova():
    assert _tipo_confere('', 'SKC 226-01 (TCP*****)') is None
    assert _tipo_confere('TCP', '') is None


# ── o caso real da Destak ──────────────────────────────────────────────

M_1403 = {'metodoCod': 'NIOSH 1403', 'vazao': '0,02 A 0,2 L/MIN',
          'volume': '1 A 10 L', 'amostradorCod': 'SKC 226-01 (TCP*****)'}
M_7303 = {'metodoCod': 'NIOSH 7303',
          'vazao': '1,7 NYLON OU 2,0\nSKC OU\n2,2 HD OU 2,5\nALÚMINIO',
          'volume': '45 A 1.000 L', 'amostradorCod': 'SKC 225-5 (EC*****)'}


def test_tcp2912av3_reprova_por_vazao():
    """0,201 L/min contra máximo 0,2 — fora por 0,001 (meio por cento)."""
    r = validar_coleta(M_1403, vazao=0.201, tipo_amostrador='TCP',
                       hora_inicio='11:45', hora_final='12:03')
    assert r['veredicto'] == FORA
    assert any('vazão' in p for p in r['problemas'])
    assert not any('volume' in p for p in r['problemas'])


def test_ec98029a_passa_o_volume_de_120_litros():
    """120,6 L está DENTRO de 45–1.000. Com o ponto de milhar lido errado,
    esta coleta boa era reprovada."""
    r = validar_coleta(M_7303, vazao=2.01, tipo_amostrador='SKC 225-5 (EC*****)',
                       hora_inicio='14:45', hora_final='15:45')
    assert r['volume_calculado'] == 120.6
    assert r['veredicto'] == OK, r['problemas']


def test_coletas_dentro_do_metodo_passam():
    for vz in (0.196, 0.198, 0.194):
        r = validar_coleta(M_1403, vazao=vz, tipo_amostrador='TCP',
                           hora_inicio='11:45', hora_final='12:03')
        assert r['veredicto'] == OK, (vz, r['problemas'])


# ── comportamento nas bordas ───────────────────────────────────────────

def test_limite_exato_passa():
    r = validar_coleta(M_1403, vazao=0.2, tipo_amostrador='TCP',
                       hora_inicio='11:45', hora_final='12:03')
    assert r['veredicto'] == OK


def test_agente_fora_da_guia():
    r = validar_coleta(None, vazao=0.2)
    assert r['veredicto'] == SEM_METODO


def test_sem_vazao_nem_tipo_nao_inventa_reprovacao():
    r = validar_coleta(M_1403)
    assert r['veredicto'] == SEM_DADO
    assert r['problemas'] == []


def test_volume_gravado_vence_o_calculado():
    r = validar_coleta(M_1403, vazao=0.1, volume=5.0,
                       hora_inicio='11:45', hora_final='12:03')
    assert r['volume_calculado'] == 5.0


def test_amostrador_errado_e_apontado():
    r = validar_coleta(M_7303, vazao=2.0, tipo_amostrador='TCP',
                       hora_inicio='14:45', hora_final='15:45')
    assert r['veredicto'] == FORA
    assert any('amostrador' in p for p in r['problemas'])


# ── escolha do método quando o agente tem vários ───────────────────────

def test_melhor_metodo_segue_o_amostrador_usado():
    ms = [M_7303, M_1403]
    assert melhor_metodo(ms, 'TCP')['metodoCod'] == 'NIOSH 1403'
    assert melhor_metodo(ms, 'EC')['metodoCod'] == 'NIOSH 7303'


def test_melhor_metodo_sem_pista_pega_o_primeiro():
    assert melhor_metodo([M_7303, M_1403])['metodoCod'] == 'NIOSH 7303'
    assert melhor_metodo([]) is None
