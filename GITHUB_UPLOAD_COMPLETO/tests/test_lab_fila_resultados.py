# -*- coding: utf-8 -*-
"""Fila de resultados (RA) do laboratório: dedupe e classificação da ação.

Levantado pelo Matheus em 30/07/2026: "se o laboratório manda vários resultados
como que vai anexar a só um?". Investigando os 5 RAs reais da fila:

- 3 linhas eram o MESMO laudo reencaminhado ("ENC: RA ..."), contadas como
  resultado novo porque o dedupe usava o assunto inteiro.
- 2 RAs já tinham sido lidos automaticamente pelo código no nome do anexo, e
  continuavam na fila pedindo "Vincular" — trabalho já feito.
- 3 não casaram porque o código do laudo (TCP4806AV3, TCP4908AV3, PVC04V50)
  NÃO existe no inventário. Aí vincular a outro amostrador gravaria o resultado
  no tubo errado, concluindo em silêncio.
"""
from controle.lab_inbox import _ra_do_assunto, _classificar_acao_resultados


# ── dedupe pelo número do RA ───────────────────────────────────────────

def test_ra_do_assunto_extrai_numero():
    assert _ra_do_assunto('RA 81962593 - ASSISTE ENGENHARIA // AVALIADA: X') == '81962593'
    assert _ra_do_assunto('ENC: RA 81962593 - ASSISTE ENGENHARIA') == '81962593'
    assert _ra_do_assunto('RA: 81961870 - ...') == '81961870'
    assert _ra_do_assunto('RA-81961870 - ...') == '81961870'


def test_encaminhado_tem_o_mesmo_ra_do_original():
    """É o que fazia 5 RAs virarem 8 linhas na fila."""
    a = _ra_do_assunto('RA 81962594 - ASSISTE // AVALIADA: FMM METALMECANICA LTDA')
    b = _ra_do_assunto('ENC: RA 81962594 - ASSISTE // AVALIADA: FMM METALMECANICA LTDA')
    assert a == b == '81962594'


def test_assunto_sem_ra_nao_inventa():
    assert _ra_do_assunto('Pendentes de retorno - julho') is None
    assert _ra_do_assunto('') is None
    assert _ra_do_assunto(None) is None


# ── classificação da ação ──────────────────────────────────────────────

def test_casou_sem_pendencia_sai_da_fila():
    r = [{'ra_num': '81961870', 'casou': ['40U15', 'PVC07U97'], 'nao_cadastrados': []}]
    _classificar_acao_resultados(r)
    assert r[0]['acao'] == 'resolvido'


def test_codigo_fora_do_inventario_pede_cadastro():
    # Caso real: 81962594-4-TCP4806AV3-... e TCP4806AV3 não existe.
    r = [{'ra_num': '81962594', 'casou': [], 'nao_cadastrados': ['TCP4806AV3']}]
    _classificar_acao_resultados(r)
    assert r[0]['acao'] == 'cadastrar'


def test_pendencia_vence_o_que_ja_casou():
    """Casou 1 de 2 e o outro não está no inventário → ainda precisa ação."""
    r = [{'ra_num': '81955409', 'casou': ['75A1'], 'nao_cadastrados': ['75B1']}]
    _classificar_acao_resultados(r)
    assert r[0]['acao'] == 'cadastrar'


def test_sem_nada_extraido_cai_para_manual():
    # e-mail de RA sem PDF, ou nome de anexo fora do padrão do lab
    r = [{'ra_num': '81963406', 'casou': [], 'nao_cadastrados': []}]
    _classificar_acao_resultados(r)
    assert r[0]['acao'] == 'manual'


def test_classifica_os_cinco_ras_reais():
    """Snapshot do que a fila real devolvia em 30/07/2026."""
    fila = [
        {'ra_num': '81961870', 'casou': ['40U15', 'PVC07U97'], 'nao_cadastrados': []},
        {'ra_num': '81962593', 'casou': ['TCP4794AV3', 'TCP4807AV3'], 'nao_cadastrados': []},
        {'ra_num': '81962594', 'casou': [], 'nao_cadastrados': ['TCP4806AV3']},
        {'ra_num': '81962596', 'casou': [], 'nao_cadastrados': ['TCP4908AV3']},
        {'ra_num': '81963406', 'casou': [], 'nao_cadastrados': ['PVC04V50']},
    ]
    _classificar_acao_resultados(fila)
    acoes = [x['acao'] for x in fila]
    assert acoes == ['resolvido', 'resolvido', 'cadastrar', 'cadastrar', 'cadastrar']
    # a tela cobra 3 e informa 2 já lidos — antes cobrava 8
    assert sum(1 for a in acoes if a != 'resolvido') == 3
