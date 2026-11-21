# -*- coding: utf-8 -*-
"""A planilha de campo de ruído herda os dosímetros de TODOS os itens de ruído do planejamento.

21/11/2026 (Wesley, Os Medidores, 19/11 16h30): com 10 Chrompack e 4 Inlite o planejamento abre duas linhas de
"Ruído (dosimetria)", uma por marca, cada uma com o seu calibrador. Na etapa 2 da planilha só apareciam os 10
Chrompack: o bloco "Ruído: herdar do planejamento" (templates/index.html) usava `.find(...)` e lia só o primeiro
item de ruído. Agora usa `.filter(...)`: uma linha por dosímetro de cada item e os calibradores juntos no campo.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates', 'index.html')
INICIO = '// 21/11/2026 (Wesley): o plano pode ter MAIS de um "Ruído (dosimetria)"'
FIM = 'window._nvRuidoHerdado = true;\n  }'


def _bloco():
    html = open(INDEX, encoding='utf-8').read()
    ini = html.index(INICIO)
    fim = html.index(FIM, ini) + len(FIM)
    return html[ini:fim]


def test_bloco_le_todos_os_itens_de_ruido():
    corpo = _bloco()
    assert re.search(r"\.filter\(it => \(it\.tipo \|\| ''\) === 'ruido'\)", corpo)
    assert not re.search(r"\.find\(it => \(it\.tipo \|\| ''\) === 'ruido'\)", corpo), 'voltou a ler só o 1º item'
    assert "cals.join(' / ')" in corpo


def _rodar_no_node(itens):
    """Roda o bloco de verdade num DOM falso e devolve (linhas da planilha, calibrador)."""
    script = f"""
const window = {{ _ctrlNvItens: {json.dumps(itens)}, _nvPlanejamentoId: 7, _nvRuidoHerdado: false }};
const campo = {{ value: '' }};
const document = {{ getElementById: id => id === 'nv-cr-calibrador' ? campo : null }};
let _nvCrTrabs = [];
const nvCrRenderTrabs = () => {{}};
const temRuido = true;
{_bloco()}
console.log(JSON.stringify({{ linhas: _nvCrTrabs, cal: campo.value }}));
"""
    r = subprocess.run(['node', '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    return out['linhas'], out['cal']


@pytest.mark.skipif(not shutil.which('node'), reason='node não está instalado')
def test_dez_chrompack_e_quatro_inlite_viram_catorze_linhas():
    chrom = [{'serie': f'00000011{n}'} for n in range(49, 55)] + [{'serie': s} for s in
             ('0000005064', '0000005065', '0000005309', '0000001085')]
    inlite = [{'serie': s} for s in ('2507050710AA', '2507050810AA', '2507050490AA', '2507050750AA')]
    itens = [
        {'tipo': 'ruido', 'qtd': 10, 'funcao': 'Motorista / Operação', 'dosim_ruido': chrom,
         'calibrador_ruido_serie': 'CAL0000002150'},
        {'tipo': 'quimico', 'qtd': 1},
        {'tipo': 'ruido', 'qtd': 4, 'funcao': 'Mecânico / Oficina', 'dosim_ruido': inlite,
         'calibrador_ruido_serie': '25035711'},
    ]
    linhas, cal = _rodar_no_node(itens)
    assert [l['sn'] for l in linhas] == [d['serie'] for d in chrom + inlite]
    assert {l['cargo'] for l in linhas[:10]} == {'Motorista'} and {l['cargo'] for l in linhas[10:]} == {'Mecânico'}
    assert linhas[10]['setor'] == 'Oficina'
    assert cal == 'CAL0000002150 / 25035711'


@pytest.mark.skipif(not shutil.which('node'), reason='node não está instalado')
def test_um_item_sem_dosimetro_continua_como_antes():
    linhas, cal = _rodar_no_node([{'tipo': 'ruido', 'qtd': 3, 'funcao': 'Soldador / Caldeiraria'}])
    assert len(linhas) == 3 and all(l['sn'] == '' and l['cargo'] == 'Soldador' for l in linhas)
    assert cal == ''


@pytest.mark.skipif(not shutil.which('node'), reason='node não está instalado')
def test_mesmo_calibrador_nos_dois_itens_nao_repete():
    itens = [{'tipo': 'ruido', 'dosim_ruido': [{'serie': 'A'}], 'calibrador_ruido_serie': 'CAL1'},
             {'tipo': 'ruido', 'dosim_ruido': [{'serie': 'B'}], 'calibrador_ruido_serie': 'CAL1'}]
    linhas, cal = _rodar_no_node(itens)
    assert [l['sn'] for l in linhas] == ['A', 'B'] and cal == 'CAL1'
