# -*- coding: utf-8 -*-
"""Leitura do RA: a mesma extração serve upload do técnico e caixa do laboratório.

O valor do laudo (agente, unidade, concentração, LT NR-15, ACGIH TWA/STEL) já era
extraído em `/api/convert_laudo`, mas SÓ quando o técnico baixava o anexo do
Outlook e subia o PDF — trabalho que o servidor já fazia de madrugada
(`backfill_ras` abre ~384 PDFs/dia). A extração virou `ler_laudo_ra_pdf(raw)` para
`/api/laudo_do_lab` puxar o mesmo laudo direto da caixa.

O PDF de RA real não entra no repositório: ele traz nome, função e setor de
trabalhador (dado pessoal). Os testes que precisam do arquivo rodam na máquina de
quem tem uma amostra em `Downloads` e são skipados no CI — por isso o contrato da
função também é verificado sem PDF.
"""
import glob
import os

import pytest

import app as A


def _amostra_ra():
    """Primeiro PDF de RA no Downloads: nome '<ra>-<seq>-<amostrador>-...pdf'.

    O padrão do nome é o do laboratório; PDF de nome UUID (que também começa com
    dígito) não serve e derrubava o teste com dados vazios.
    """
    import re
    padrao = os.path.join(os.path.expanduser('~'), 'Downloads', '*-*-*.pdf')
    achados = [p for p in sorted(glob.glob(padrao))
               if re.match(r'^\d{6,}-\d+-[A-Za-z0-9]{4,14}-', os.path.basename(p))]
    return achados[0] if achados else None


def test_funcao_existe_e_a_rota_delega():
    """Contrato mínimo, sem PDF: a rota não pode voltar a ter parser próprio."""
    assert callable(A.ler_laudo_ra_pdf)
    import inspect
    fonte = inspect.getsource(A.api_convert_laudo)
    assert 'ler_laudo_ra_pdf(' in fonte
    assert 'get_pixmap' not in fonte      # extração mora na função, não na rota


def test_rota_do_lab_exige_chave():
    """Sem ?ra= nem ?amostrador= não sai varredura de caixa nenhuma."""
    A.app.config['LOGIN_DISABLED'] = True
    regras = {str(r) for r in A.app.url_map.iter_rules()}
    assert '/api/laudo_do_lab' in regras
    fonte = __import__('inspect').getsource(A.api_laudo_do_lab)
    assert "request.args.get('ra'" in fonte and "request.args.get('amostrador'" in fonte
    assert '400' in fonte                 # responde 400, não varre a caixa toda


def _pdf_com_linhas(linhas):
    """PDF sintético com uma linha por texto — reproduz o layout da tabela do RA
    sem versionar laudo real (que traz dado de trabalhador)."""
    import fitz
    doc = fitz.open()
    pg = doc.new_page()
    y = 60
    for l in linhas:
        pg.insert_text((50, y), l, fontsize=9)
        y += 14
    raw = doc.tobytes()
    doc.close()
    return raw


# Tabela do RA: agente / unidade / resultado / NR15 MP 8h / NR15 Teto / ACGIH TWA /
# ACGIH STEL / (Ceiling, LD, LQ)
def _tabela(agente, unidade, resultado, mp8h='-', teto='-', twa='-', stel='-'):
    return ['4 - RESULTADO (s) CONCENTRAÇÃO**', agente, unidade, resultado,
            mp8h, teto, twa, stel, '-', '0,04', '0,12']


def test_fracao_colada_na_unidade_nao_derruba_a_leitura():
    """Poeira/metal vem 'mg/m³ (I)'. Exigir unidade pura fazia o laudo sair VAZIO
    — 3 dos 7 laudos do RA 81962595 (Cromo metálico, cassete IOM), 05/08/2026."""
    raw = _pdf_com_linhas(_tabela('Cromo metálico, como Cr(0)', 'mg/m³ (I)', '0,00041',
                                  twa='0,5'))
    _, d = A.ler_laudo_ra_pdf(raw)
    assert d.get('agente') == 'Cromo metálico, como Cr(0)'
    assert d.get('concentracao') == '0,00041 mg/m³'   # unidade limpa, sem o (I)
    assert d.get('fracao') == 'Inalável'
    assert d.get('ltTWA') == '0,5'


@pytest.mark.parametrize('marca,nome', [('(I)', 'Inalável'), ('(R)', 'Respirável'),
                                        ('( T )', 'Torácica')])
def test_as_tres_fracoes_viram_nome(marca, nome):
    _, d = A.ler_laudo_ra_pdf(_pdf_com_linhas(_tabela('Poeira', f'mg/m³ {marca}', '1,25')))
    assert d.get('fracao') == nome
    assert d.get('concentracao') == '1,25 mg/m³'


def test_unidade_pura_segue_sem_fracao():
    """Não pode inventar fração onde o laudo não declara (caso ppm, o mais comum)."""
    _, d = A.ler_laudo_ra_pdf(_pdf_com_linhas(_tabela('Tolueno', 'ppm', '47,28085',
                                                      mp8h='78', twa='20', stel='-')))
    assert d.get('concentracao') == '47,28085 ppm'
    assert not d.get('fracao')
    assert d.get('ltNR15') == '78' and d.get('ltTWA') == '20'


@pytest.mark.skipif(_amostra_ra() is None,
                    reason='sem PDF de RA no Downloads (não versionado: dado pessoal)')
def test_extrai_os_campos_do_laudo_real():
    with open(_amostra_ra(), 'rb') as fh:
        imgs, dados = A.ler_laudo_ra_pdf(fh.read())
    assert imgs and imgs[0].startswith('data:image/jpeg;base64,')
    # o que o laudo químico consome: sem isto a avaliação sai sem resultado
    for campo in ('filtroNumero', 'agente', 'concentracao', 'dataColeta'):
        assert dados.get(campo), f'{campo} não extraído de {os.path.basename(_amostra_ra())}'
    # concentração vem com unidade colada ("<0,007 ppm"), é isso que a tela espera
    assert any(u in dados['concentracao'] for u in ('ppm', 'mg/m³', 'mg/m3', 'mg', 'µg', 'f/cc'))
    # nº do amostrador do PDF tem de ser o mesmo do NOME do arquivo (chave do cruzamento)
    cod_nome = os.path.basename(_amostra_ra()).split('-')[2].upper()
    assert dados['filtroNumero'].upper() == cod_nome
