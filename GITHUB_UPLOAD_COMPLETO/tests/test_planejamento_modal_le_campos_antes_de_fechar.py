# -*- coding: utf-8 -*-
"""O modal "+ Ruído / + Químico / + Vibração" do planejamento tem de ser lido ANTES de sair da tela.

18/11/2026 (Wesley, Os Medidores): "mesmo marcando Inlite eles não aparecem". O `planConfirmarAgente`
(templates/index.html) removia o modal (`plan-add-modal`) e só depois lia `plan-modal-marca-dos`: o
`getElementById` já voltava nulo e a marca caía sempre no padrão Chrompack, então a lista de dosímetros e de
calibradores da Inlite nunca aparecia. O mesmo valia para o tipo de coleta do químico (sempre "pessoal") e para
o subtipo e o aparelho de vibração (sempre VCI e Chrompack 1).
"""
import os
import re

INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates', 'index.html')


def _corpo_confirmar_agente():
    html = open(INDEX, encoding='utf-8').read()
    ini = html.index('function planConfirmarAgente(tipo)')
    fim = html.index('\n}\n', ini)
    return html[ini:fim]


def test_campos_do_modal_lidos_antes_de_remover_o_modal():
    corpo = _corpo_confirmar_agente()
    remove = corpo.index("getElementById('plan-add-modal')?.remove()")
    lidos = [m.start() for m in re.finditer(r"getElementById\('plan-modal-[^']+'\)", corpo)]
    assert lidos, 'planConfirmarAgente não lê nenhum campo do modal'
    depois = [corpo[p:p + 60] for p in lidos if p > remove]
    assert not depois, f'campo do modal lido depois de o modal sair da tela: {depois}'


def test_marca_do_dosimetro_e_os_outros_campos_chegam_ao_agente():
    corpo = _corpo_confirmar_agente()
    for campo in ('plan-modal-marca-dos', 'plan-modal-tipo-coleta', 'plan-modal-vib-subtipo', 'plan-modal-vib-aparelho'):
        assert campo in corpo, f'{campo} deixou de ser lido'
    assert re.search(r"obj\.marca_dosimetro\s*=\s*marcaDos\s*\|\|\s*'chrompack'", corpo)
    assert re.search(r"obj\.tipo_coleta\s*=\s*tipoColeta\s*\|\|\s*'pessoal'", corpo)
