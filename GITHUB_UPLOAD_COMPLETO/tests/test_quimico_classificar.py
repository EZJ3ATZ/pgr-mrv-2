# -*- coding: utf-8 -*-
"""Veredicto químico da aba de resultados do laboratório.

A grade do lab exibia REGULAR em avaliação que o laudo assinava como
IRREGULAR: o JS comparava com UM limite só (`ltNR15 || ltTWA`) e sem a
correção Brief & Scala (×0,88). Com LT-TWA 20 ppm (17,6 corrigido) uma
concentração de 18,5 ficava verde na tela e reprovava no documento.

Agora a tela consome /quimico/classificar, que usa a mesma
_classificar_quimico do laudo — estes testes travam a equivalência.
"""
import pytest

from app import app, _classificar_quimico


def _c():
    app.config['TESTING'] = True
    app.config['LOGIN_DISABLED'] = True
    return app.test_client()


def _post(client, avaliacoes):
    r = client.post('/quimico/classificar', json={'avaliacoes': avaliacoes})
    assert r.status_code == 200, r.status_code
    return r.get_json()['resultados']


# ── os casos que divergiam ────────────────────────────────────────────

def test_acgih_reprova_mesmo_sem_limite_nr15():
    # 18,5 ppm com LT-TWA 20 → 17,6 corrigido → IRREGULAR.
    # A tela antiga comparava com 20 cru e dava REGULAR.
    (r,) = _post(_c(), [{'concentracao': '18,5 ppm', 'ltNR15': '', 'ltTWA': '20 ppm'}])
    assert r['txt'] == 'IRREGULAR'
    assert r['nivel'] == 'atencao'      # NR-15 não se aplica → âmbar, não vermelho


def test_nr15_folgada_nao_esconde_acgih_estourada():
    # NR-15 25 (passa) mas ACGIH 20→17,6 (não passa). O `||` só via a NR-15.
    (r,) = _post(_c(), [{'concentracao': '22 ppm', 'ltNR15': '25 ppm', 'ltTWA': '20 ppm'}])
    assert r['txt'] == 'IRREGULAR'
    assert 'NR-15: REGULAR' in r['detalhe'] and 'ACGIH: IRREGULAR' in r['detalhe']


def test_lt_nr15_textual_nao_bloqueia_avaliacao_pela_acgih():
    # "Não estabelecido" é truthy no JS: o `||` parava nele e a linha ficava
    # "sem limite" mesmo havendo LT-TWA utilizável.
    (r,) = _post(_c(), [{'concentracao': '5 ppm', 'ltNR15': 'Não estabelecido',
                         'ltTWA': '10 ppm'}])
    assert r['txt'] == 'REGULAR'
    assert r['nivel'] == 'ok'


def test_abaixo_do_limite_de_deteccao_e_regular():
    # "<0,007" era lido como 0.007 e comparado; o laudo trata "<" como N.D.
    (r,) = _post(_c(), [{'concentracao': '<0,007 ppm', 'ltNR15': '0,005 ppm', 'ltTWA': ''}])
    assert r['nivel'] == 'nd'


# ── semântica de cor (mesma do quadro IX) ─────────────────────────────

def test_estouro_de_nr15_e_vermelho_e_acgih_e_ambar():
    vermelho, ambar = _post(_c(), [
        {'concentracao': '30 ppm', 'ltNR15': '10 ppm', 'ltTWA': ''},
        {'concentracao': '18,5 ppm', 'ltNR15': '', 'ltTWA': '20 ppm'},
    ])
    assert vermelho['nivel'] == 'ruim'    # legalmente irregular
    assert ambar['nivel'] == 'atencao'    # conforme na NR-15, acima na ACGIH


def test_sem_nenhum_limite_nao_inventa_veredicto():
    (r,) = _post(_c(), [{'concentracao': '5 ppm', 'ltNR15': '', 'ltTWA': ''}])
    assert r['nivel'] == 'sem_limite'


# ── equivalência com o laudo e robustez ───────────────────────────────

@pytest.mark.parametrize('ev', [
    {'concentracao': '18,5 ppm', 'ltNR15': '', 'ltTWA': '20 ppm'},
    {'concentracao': '22 ppm', 'ltNR15': '25 ppm', 'ltTWA': '20 ppm'},
    {'concentracao': '3 ppm', 'ltNR15': '10 ppm', 'ltTWA': ''},
    {'concentracao': '30 ppm', 'ltNR15': '10 ppm', 'ltTWA': ''},
])
def test_endpoint_concorda_com_a_funcao_do_laudo(ev):
    (r,) = _post(_c(), [ev])
    cl = _classificar_quimico(ev)
    esperado = 'REGULAR' if cl['ok_geral'] else 'IRREGULAR'
    assert r['txt'] == esperado


def test_avaliacao_malformada_nao_derruba_o_lote():
    r = _post(_c(), [{'concentracao': '3 ppm', 'ltNR15': '10 ppm'},
                     'nao-e-dict',
                     {'concentracao': '30 ppm', 'ltNR15': '10 ppm'}])
    assert len(r) == 3
    assert r[0]['txt'] == 'REGULAR' and r[2]['txt'] == 'IRREGULAR'


def test_guarda_de_entrada():
    c = _c()
    assert c.post('/quimico/classificar', json={'avaliacoes': 'x'}).status_code == 400
    assert c.post('/quimico/classificar', json={'avaliacoes': [{}] * 501}).status_code == 400
    assert c.post('/quimico/classificar', json={}).get_json()['resultados'] == []
