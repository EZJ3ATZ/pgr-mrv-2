# -*- coding: utf-8 -*-
"""Acessório fora da lista digitado na coleta química (16/11/2026, Helbert no grupo Os Medidores:
"adiciona um espaço para que possamos coloca o tipo de acessório manual").

O digitado vai no fim do texto gravado como "Outro: ...", sai no PDF da planilha de campo ao lado das
caixinhas e, na releitura da planilha, não vira caixinha da lista.
"""
from reportlab.lib.pagesizes import A4

from controle.routes import _quimico_agente_flowables, _separa_ac_outro


def _textos(flowables):
    """Junta o texto de todos os Paragraph, entrando nas tabelas."""
    saida = []

    def anda(f):
        if hasattr(f, 'text') and isinstance(getattr(f, 'text'), str):
            saida.append(f.text)
        for linha in getattr(f, '_cellvalues', []) or []:
            for celula in linha:
                for item in (celula if isinstance(celula, (list, tuple)) else [celula]):
                    anda(item)

    for f in flowables:
        anda(f)
    return '\n'.join(saida)


def test_separa_o_digitado_da_lista():
    assert _separa_ac_outro('Ciclone Nylon, Suporte IOM, Outro: Impinger midget') == \
        ('Ciclone Nylon, Suporte IOM, ', 'Impinger midget')
    assert _separa_ac_outro('Ciclone Nylon, Suporte IOM') == ('Ciclone Nylon, Suporte IOM', '')
    assert _separa_ac_outro('Outro: Filtro PVC, mangueira extra') == ('', 'Filtro PVC, mangueira extra')
    assert _separa_ac_outro(None) == ('', '')


def test_digitado_com_palavra_da_lista_nao_marca_a_caixinha():
    lista, outro = _separa_ac_outro('Ciclone Nylon, Outro: Termômetro de bulbo')
    assert 'termo' not in lista.lower()
    assert outro == 'Termômetro de bulbo'


def test_pdf_da_planilha_mostra_o_outro_acessorio_escapado():
    ag = {'ac_ciclone_ny': True, 'ac_outro': 'Impinger <midget> & filtro', 'substancias': 'Tolueno'}
    texto = _textos(_quimico_agente_flowables(ag, 0, A4[0] - 60))
    assert '[X]</b> CICLONE DE NYLON' in texto
    assert 'Outro: Impinger &lt;midget&gt; &amp; filtro' in texto


def test_pdf_sem_outro_fica_igual():
    texto = _textos(_quimico_agente_flowables({'ac_iom': True}, 0, A4[0] - 60))
    assert 'Outro:' not in texto.split('ID Calibrador')[-1].split('DADOS DA AMOSTRAGEM')[0]
