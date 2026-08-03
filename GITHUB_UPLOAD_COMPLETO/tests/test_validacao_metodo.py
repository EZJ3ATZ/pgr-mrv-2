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
    ATENCAO, blocos_regime, regime_da_coleta, nominal, passivo,
    chave_metodo, escolher_metodo, escolha_incerta,
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


# ── o mesmo agente medido de mais de uma forma (03/08/2026) ────────────
# Textos literais do guia_metodos.json: o peróxido de hidrogênio escreve TWA,
# vapores e STEL no MESMO campo, e o benzeno tem 4 entradas na guia.

M_PEROXIDO = {
    'metodoCod': 'OSHA 1019',
    'vazao': '*TWA: 1L/MIN\n(240MIN)\n*VAPORES E\nMISTURAS\n2L/MIN (120MIN)\n*STEL: 2L/MIN\n(15MIN)',
    'volume': '*TWA: 240L\n(VAPORES E\nMISTURAS)\n*STEL: 30L',
    'amostradorCod': 'SKC 225-9030 (FVPH****)'}
M_PERACETICO = {'metodoCod': 'BOHS - AOH, VOL48, Nº8, P.P715', 'vazao': '1 L/MIN',
                'volume': '15 A 120 L', 'amostradorCod': 'SKC 226-193 (MTS*****)'}
M_BENZ_1501 = {'metodoCod': 'NIOSH 1501', 'vazao': '0,02 A 0,2 L/MIN',
               'volume': 'STEL: 3L TWA: 5 A\n30L', 'amostradorCod': 'SKC 226-01 (TCP*****)'}
M_BENZ_OSHA = {'metodoCod': 'OSHA 1005 (MODIFICADO)',
               'vazao': 'TWA: 0,02L/MIN\nSTEL:0,2L/MIN', 'volume': 'TWA: 8L STEL:3L',
               'amostradorCod': 'SKC 226-01 (TCP*****)'}
M_BENZ_PASSIVO = {'metodoCod': 'PASSIVO SKC', 'vazao': '0', 'volume': '0',
                  'amostradorCod': 'SKC 575-001 (OVM*****)'}
BENZENO = [M_BENZ_1501, M_BENZ_PASSIVO, M_BENZ_OSHA, M_BENZ_PASSIVO]


def test_blocos_separam_os_regimes_do_mesmo_campo():
    bs = blocos_regime(M_PEROXIDO['vazao'])
    assert [(b['regime'], b['texto'], b['minutos']) for b in bs] == [
        ('TWA', '1L/MIN', 240), ('VAPORES', '2L/MIN', 120), ('STEL', '2L/MIN', 15)]


def test_bloco_sem_numero_e_qualificador_nao_regime():
    # '(VAPORES E MISTURAS)' no volume só qualifica o TWA — não tem valor próprio.
    assert [(b['regime'], b['texto']) for b in blocos_regime(M_PEROXIDO['volume'])] == [
        ('TWA', '240L'), ('STEL', '30L')]


def test_bloco_sem_rotulo_antes_do_primeiro_vale_como_twa():
    # Cloro: a guia escreve o valor de jornada sem nome e rotula só o STEL.
    bs = blocos_regime('* 240L\n*AMOSTRAGEM\nSTEL: 30L')
    assert bs[0]['regime'] == 'TWA' and faixa(bs[0]['texto'])[1] == 240.0


def test_regime_sai_da_duracao():
    bs = blocos_regime(M_PEROXIDO['vazao'])
    assert regime_da_coleta(240, bs) == 'TWA'
    assert regime_da_coleta(15, bs) == 'STEL'
    assert regime_da_coleta(120, bs) == 'VAPORES'


def test_faixa_fundida_nao_reprova_mais_o_twa_legitimo():
    """FVPH da Hypofarma: 1,037 L/min × 240 min = 248,88 L.

    Lendo TWA e STEL juntos a faixa virava 30–240 L e isto reprovava; agora
    confere contra o TWA (240 L) e o desvio de 3,7% é da bomba, não do método.
    """
    r = validar_coleta(M_PEROXIDO, vazao=1.037, tipo_amostrador='FVPH', tempo_min=240)
    assert r['regime'] == 'TWA'
    assert r['volume_calculado'] == 248.88
    assert r['veredicto'] == ATENCAO, r['problemas']
    assert r['problemas'] == []
    assert len(r['avisos']) == 2


def test_faixa_fundida_nao_aprova_mais_stel_longo():
    """2 L/min × 100 min = 200 L passava porque 200 cabia em 30–240."""
    r = validar_coleta(M_PEROXIDO, vazao=2.0, tipo_amostrador='FVPH', tempo_min=100)
    assert r['veredicto'] == FORA
    assert any('volume' in p for p in r['problemas'])


def test_stel_de_verdade_passa():
    r = validar_coleta(M_PEROXIDO, vazao=2.0, tipo_amostrador='FVPH', tempo_min=15)
    assert r['regime'] == 'STEL'
    assert r['veredicto'] == OK, r['problemas']


# ── tolerância no limite de valor único ────────────────────────────────

def test_nominal_so_para_valor_unico():
    assert nominal('1 L/MIN') is True
    assert nominal('240L') is True
    assert nominal('0,02 A 0,2 L/MIN') is False
    assert nominal('MÁXIMO 6 L') is False


def test_desvio_de_calibracao_e_atencao_nao_reprovacao():
    """Bomba fecha em 1,04 contra nominal de 1 L/min: 4%, dentro dos 5%."""
    r = validar_coleta(M_PERACETICO, vazao=1.04, tipo_amostrador='MTS', tempo_min=35)
    assert r['veredicto'] == ATENCAO
    assert r['problemas'] == []
    assert any('calibração' in a for a in r['avisos'])


def test_desvio_acima_da_tolerancia_continua_fora():
    """MTS3091 da Hypofarma: 1,073 L/min é 7,3% — passa dos 5% e reprova."""
    r = validar_coleta(M_PERACETICO, vazao=1.073, tipo_amostrador='MTS', tempo_min=35)
    assert r['veredicto'] == FORA
    assert any('vazão' in p for p in r['problemas'])


def test_tolerancia_nao_vale_para_faixa_declarada():
    """0,201 contra 0,02–0,2 segue FORA — faixa é limite, não alvo."""
    r = validar_coleta(M_1403, vazao=0.201, tipo_amostrador='TCP',
                       hora_inicio='11:45', hora_final='12:03')
    assert r['veredicto'] == FORA


# ── método passivo: não tem bomba ──────────────────────────────────────

def test_passivo_nao_confere_vazao():
    assert passivo(M_BENZ_PASSIVO) is True
    r = validar_coleta(M_BENZ_PASSIVO, vazao=0.05, tipo_amostrador='OVM', tempo_min=480)
    assert r['veredicto'] == OK, r['problemas']
    assert r['passivo'] is True


# ── qual método vale: escolha do técnico ───────────────────────────────

def test_escolha_do_tecnico_vence_a_sugestao():
    ch = chave_metodo(M_BENZ_OSHA)
    assert escolher_metodo(BENZENO, ch, 'TCP')['metodoCod'] == 'OSHA 1005 (MODIFICADO)'
    assert escolher_metodo(BENZENO, None, 'TCP')['metodoCod'] == 'NIOSH 1501'


def test_chave_desempata_metodo_repetido():
    # 'PASSIVO SKC' aparece 2× no benzeno; só o amostrador diferencia.
    assert chave_metodo(M_BENZ_PASSIVO) == 'PASSIVO SKC|SKC 575-001 (OVM*****)'


def test_chave_invalida_cai_na_sugestao():
    assert escolher_metodo(BENZENO, 'AGENTE ORANGE|X', 'TCP')['metodoCod'] == 'NIOSH 1501'


def test_escolha_incerta_quando_dois_metodos_usam_o_mesmo_amostrador():
    # NIOSH 1501 e OSHA 1005 são ambos TCP: o amostrador não decide.
    assert escolha_incerta(BENZENO, 'TCP') is True
    assert escolha_incerta([M_1403, M_7303], 'TCP') is False
    assert escolha_incerta([M_1403], 'TCP') is False


def test_metodo_escolhido_muda_o_veredicto():
    """Mesma coleta: 20 L cabe no TWA do NIOSH 1501 (5–30 L) e não no OSHA
    1005 (8 L). Conferir contra o método errado inventa ou esconde problema."""
    kw = dict(vazao=0.05, tipo_amostrador='TCP', tempo_min=400)
    assert validar_coleta(M_BENZ_1501, **kw)['veredicto'] == OK
    assert validar_coleta(M_BENZ_OSHA, **kw)['veredicto'] == FORA
