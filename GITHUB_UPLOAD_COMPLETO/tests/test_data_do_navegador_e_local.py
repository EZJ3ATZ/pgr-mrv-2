# -*- coding: utf-8 -*-
"""A data que o navegador preenche e a LOCAL, nunca a de UTC (03/09/2026).

`new Date().toISOString().slice(0,10)` devolve a data em UTC. Em BRT (UTC-3),
das 21h em diante ela ja e o dia seguinte. O idiom estava em 23 lugares do
index.html, e varios pre-preenchem campo que a pessoa salva sem olhar: a data
da coleta, a do envio ao laboratorio, a da baixa, a do resultado do RA.

Data errada em silencio e pior que campo em branco: a trava de data que subiu
em 02/09/2026 aceita '2026-09-04' numa visita feita dia 03, porque e ISO
valida. Nenhuma guarda pega isso.

Este teste e por texto porque o alvo e JavaScript de template, que a suite nao
executa. Ele nao prova que a tela funciona — prova que o idiom errado nao
voltou, que e o modo real de isso reaparecer (copiar e colar de outra linha).
"""
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(RAIZ, 'templates', 'index.html')


def _html():
    with open(INDEX, encoding='utf-8') as f:
        return f.read()


def test_nenhum_toisostring_de_data_sobrou():
    html = _html()
    achados = [m.start() for m in re.finditer(r'toISOString\(\)\.slice\(0,\s*10\)', html)]
    if achados:
        linhas = [html[:i].count('\n') + 1 for i in achados]
        raise AssertionError(
            'data em UTC voltou ao index.html nas linhas %s — use _hojeISO() '
            'ou _dataISO(d)' % linhas)


def test_os_helpers_existem():
    html = _html()
    assert 'function _dataISO(d) {' in html, '_dataISO sumiu'
    assert 'function _hojeISO()' in html, '_hojeISO sumiu'


def test_helper_usa_os_getters_locais():
    """getFullYear/getMonth/getDate sao locais; getUTC* trariam o bug de volta
    com outra roupa."""
    html = _html()
    i = html.index('function _dataISO(d) {')
    corpo = html[i:i + 400]
    for g in ('getFullYear()', 'getMonth()', 'getDate()'):
        assert g in corpo, 'o helper nao usa %s' % g
    assert 'getUTC' not in corpo, 'o helper voltou a ler a data em UTC'


def test_nvreset_repoe_a_data():
    """nvReset zerava ctrl-nv-data e nao repunha; quem repunha era ctrlNvInit,
    chamada so ao NAVEGAR para a aba. Pelo botao Reiniciar a 2a visita seguida
    comecava sem data — era assim que coleta sem data nascia."""
    html = _html()
    i = html.index('function nvReset() {')
    corpo = html[i:i + 2600]
    assert "getElementById('ctrl-nv-data')" in corpo and '_hojeISO()' in corpo, \
        'nvReset nao repoe mais a data de hoje'


def test_o_campo_de_data_da_coleta_nasce_preenchido():
    """ctrlNvInit continua preenchendo a data ao abrir a tela."""
    html = _html()
    i = html.index('async function ctrlNvInit()')
    corpo = html[i:i + 900]
    assert "getElementById('ctrl-nv-data').value = _hojeISO()" in corpo, \
        'ctrlNvInit nao preenche mais a data'
