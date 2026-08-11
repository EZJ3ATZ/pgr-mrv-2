# -*- coding: utf-8 -*-
"""Horário da medição de vibração tem de sair no relatório da VISITA.

Wesley, 11/08/2026: "Medição de Vibração (VMB/VCI) não aparece o horário da
medição no relatório da visita mesmo sendo preenchido na planilha."

O dado estava no banco. Em produção, `coletas_outros#23` (LPC Construções,
OS 6549851, visita de 10/08) guardou `vibr_pontos[0].hora_inicio = '13:25'` e
`hora_final = '13:29'` — exatamente o que ele digitou. Quem perdia era o PDF:
a planilha INDIVIDUAL de vibração já tinha a coluna Horário, mas o relatório
CONSOLIDADO (visita com ruído/químico junto, que é o caso da LPC) montava a
tabela sem ela — e o cabeçalho "Horário:" saía "___ – ___" porque o técnico
preenche hora por trabalhador, não no bloco de campo da visita.
"""
from controle.routes import _campo_completo_pdf
from tests.laudos_casos import pdf_texto


BASE = {
    'empresa_nome': 'LPC Construções e Empreendimentos',
    'os': '6549851', 'data_coleta': '2026-08-10',
    'tecnico': 'Wesley Vieira Rodrigues', 'cidade': 'Belo Horizonte',
    # já resolvido: sem isto o gerador vai procurar o MTE no banco/usuário
    # logado, que fora de request não existe (o PDF não é o assunto do teste)
    'tecnico_mte': '00000/MG',
}

PONTO_VMB = {
    'nome': 'Wilian José Alves dos Santos', 'tipo': 'vmb',
    'funcao': 'Pedreiro I', 'setor': 'Obras',
    'hora_inicio': '13:25', 'hora_final': '13:29',
    'tempo': '3', 'tempo_nexp': '5',
    'equip': 'Serra Circular', 'marca': 'Makitta - 125mm', 'ano': '',
}

PONTO_VCI = {
    'nome': 'Rogerio Fernandes de Melo', 'tipo': 'vci',
    'funcao': 'Operador', 'setor': 'Pátio',
    'hora_inicio': '09:40', 'hora_final': '10:10',
    'tempo': '0.5', 'tempo_nexp': '2',
    'equip': 'Empilhadeira', 'marca': 'YALE - 70VX', 'ano': '2019',
}


def _pdf(pontos, hora_visita=None, subtipo=''):
    vib = {**BASE, 'subtipo': subtipo, 'pontos': pontos,
           'acomp': 'Angela Gomes', 'obs': ''}
    if hora_visita:
        vib['hora_ini'], vib['hora_fim'] = hora_visita
    buf, _nome = _campo_completo_pdf({
        'tipos': ['vibracao'], 'base': BASE, **BASE, 'vibracao': vib})
    return pdf_texto(buf.getvalue())


# ── o bug do Wesley ───────────────────────────────────────────────────

def test_horario_por_trabalhador_sai_na_tabela_vmb():
    txt = _pdf([PONTO_VMB])
    assert 'Horário' in txt, 'a tabela de vibração perdeu a coluna Horário'
    assert '13:25' in txt and '13:29' in txt, \
        'o horário digitado por trabalhador não chegou ao relatório da visita'


def test_horario_por_trabalhador_sai_na_tabela_vci():
    txt = _pdf([PONTO_VCI])
    assert '09:40' in txt and '10:10' in txt


def test_cabecalho_cai_para_o_horario_das_medicoes_quando_o_bloco_esta_vazio():
    """Sem hora no bloco da visita (caso da LPC), o cabeçalho usa a 1ª e a
    última medição em vez de imprimir '___ – ___'."""
    txt = _pdf([PONTO_VCI, PONTO_VMB])
    assert 'Horário: 09:40' in txt.replace('\n', ' '), \
        'o cabeçalho deveria começar na 1ª medição (09:40)'
    assert '13:29' in txt          # e terminar na última


def test_hora_do_bloco_da_visita_continua_vencendo():
    txt = _pdf([PONTO_VMB], hora_visita=('08:00', '17:30'))
    assert '08:00' in txt and '17:30' in txt
    assert '13:25' in txt        # e a da medição segue na tabela


def test_visita_sem_hora_nenhuma_nao_quebra():
    p = {k: v for k, v in PONTO_VMB.items() if k not in ('hora_inicio', 'hora_final')}
    txt = _pdf([p])
    assert 'VIBRAÇÃO' in txt.upper()
    # nome longo quebra em 2 linhas na coluna estreita — checa o começo
    assert 'Wilian' in txt and 'Serra Circular' in txt


def test_as_duas_tabelas_convivem_com_a_coluna_nova():
    """VCI e VMB na mesma visita: cada tabela tem seu conjunto de colunas."""
    txt = _pdf([PONTO_VCI, PONTO_VMB], subtipo='ambos')
    assert 'Trajeto' in txt              # tabela VCI
    assert 'Equipamento' in txt          # tabela VMB
    assert '09:40' in txt and '13:25' in txt
