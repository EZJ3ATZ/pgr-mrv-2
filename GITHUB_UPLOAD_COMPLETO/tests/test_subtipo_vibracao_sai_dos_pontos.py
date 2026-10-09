# -*- coding: utf-8 -*-
"""O subtipo da planilha de vibração remontada vem do TIPO DE CADA PONTO.

Helbert, 08/10/2026 (OS 6681026, Açoliver): a reimpressão em "Planilhas Feitas"
saía com cabeçalho "Subtipo: Corpo Inteiro (VCI)" numa visita só de VMB.

A causa era o `tipo_vibr` da antiga Etapa 2 (Planejamento) da planilha de
campo: o select nascia em "VCI — Vibração de Corpo Inteiro" e ia para o
`dados_json` sem ninguém olhar. Em produção 16 das 17 planilhas de vibração o
têm, 6 delas só VMB (coletas_outros 11, 12, 13, 20, 23, 24) e 2 mistas (16, 27).
E `_visita_payload_campo_completo` lia ESSE texto primeiro. As tabelas por
ponto saíam certas, porque cada ponto guarda o seu `tipo` ('vci'/'vmb').

Sem migração: o remonte passa a derivar o subtipo dos pontos e só cai no
`tipo_vibr` quando os pontos não têm tipo (planilhas anteriores a 11/06).
"""
import pytest

from controle.db import get_db, init_db, save_coleta_outros, visita_chave
from controle.routes import _visita_payload_campo_completo

OS = 'VIBSUBT'
TIPO_VIBR_ETAPA2 = 'VCI — Vibração de Corpo Inteiro'   # o default que era gravado


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))
        conn.execute("INSERT INTO demandas (numero_os, empresa_id, status, criado_em) "
                     "VALUES (?, ?, 'aberta', '2026-10-01')", (OS, eid))
        did = conn.execute('SELECT id FROM demandas WHERE numero_os=?', (OS,)).fetchone()['id']
    yield {'did': did, 'eid': eid}
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_outros WHERE demanda_id=?', (did,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))


def _gravar(cen, tipo, pontos, tipo_vibr=TIPO_VIBR_ETAPA2):
    save_coleta_outros({
        'tipo': tipo, 'empresa_id': cen['eid'], 'empresa_nome': 'Açoliver',
        'demanda_id': cen['did'], 'numero_os': OS, 'avaliador': 'Helbert',
        'data_coleta': '2026-10-08', 'status': 'concluida',
        'tipo_vibr': tipo_vibr, 'vibr_pontos': pontos,
    })
    return visita_chave(cen['did'], 'Açoliver', '2026-10-08')


def _ponto(tipo, nome):
    p = {'nome': nome, 'funcao': 'Operador', 'setor': 'Produção',
         'hora_inicio': '09:00', 'hora_final': '09:30', 'equip': 'Esmerilhadeira'}
    if tipo is not None:
        p['tipo'] = tipo
    return p


def test_so_vmb_sai_vbma_mesmo_com_tipo_vibr_vci_gravado(_cenario):
    """O caso do Helbert: visita só VMB, tipo_vibr 'VCI' sobrado da Etapa 2."""
    chave = _gravar(_cenario, 'vibracao', [_ponto('vmb', 'A'), _ponto('vmb', 'B')])
    d = _visita_payload_campo_completo(chave)
    assert d['vibracao']['subtipo'] == 'vbma', d['vibracao']


def test_mista_sai_ambos(_cenario):
    chave = _gravar(_cenario, 'vibracao', [_ponto('vci', 'A'), _ponto('vmb', 'B')])
    d = _visita_payload_campo_completo(chave)
    assert d['vibracao']['subtipo'] == 'ambos'


def test_so_vci_continua_vci(_cenario):
    chave = _gravar(_cenario, 'vibracao', [_ponto('vci', 'A')])
    d = _visita_payload_campo_completo(chave)
    assert d['vibracao']['subtipo'] == 'vci'


def test_ponto_sem_tipo_cai_no_tipo_vibr_gravado(_cenario):
    """Planilha anterior à planilha única (ponto sem `tipo`): o texto gravado
    ainda manda — não pode regredir o que já saía certo."""
    chave = _gravar(_cenario, 'vibracao', [_ponto(None, 'A')],
                    tipo_vibr='VBMA — Vibração de Mãos e Braços')
    d = _visita_payload_campo_completo(chave)
    assert d['vibracao']['subtipo'] == 'vbma'


def test_tipo_vibr_novo_vci_mais_vbma_vira_ambos(_cenario):
    """A planilha nova grava 'VCI + VBMA' quando há os dois; sem ponto tipado
    isso tem de virar 'ambos', não 'vbma' (o texto contém as duas siglas)."""
    chave = _gravar(_cenario, 'vibracao', [_ponto(None, 'A')], tipo_vibr='VCI + VBMA')
    d = _visita_payload_campo_completo(chave)
    assert d['vibracao']['subtipo'] == 'ambos'


def test_sem_ponto_e_sem_texto_cai_no_sufixo_do_tipo(_cenario):
    chave = _gravar(_cenario, 'vibracao_vbma', [], tipo_vibr='')
    d = _visita_payload_campo_completo(chave)
    assert d['vibracao']['subtipo'] == 'vbma'
