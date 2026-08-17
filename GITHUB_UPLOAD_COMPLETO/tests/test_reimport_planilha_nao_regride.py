# -*- coding: utf-8 -*-
"""Re-import de planilha não pode REGREDIR estado que o sistema já gravou.

A planilha do Helbert/Wesley é SUGESTÃO de campo; o estado real da medição e do
amostrador vem do app (check-out do técnico, RA do laboratório, planner_sync).
O dedup de `importar_medicoes` (que existe para não duplicar medição a cada
re-import) começou sobrescrevendo tudo com o valor da planilha, e o mesmo
acontecia no UPDATE de `importar_amostradores`.

O que estes testes travam:
1. re-import da MESMA planilha crua não reverte medição já finalizada, não zera
   pontos feitos e não apaga observação escrita no sistema — caso real: na
   `seed/medicoes.xlsx` a Fundação Educacional Lucas tem duas linhas de Acetato
   de Etila, uma com ponto avaliado e outra vazia; a linha vazia zerava a cheia;
2. planilha MAIS AVANÇADA que o banco continua mandando (é assim que o campo
   reporta progresso);
3. célula vazia não encolhe `qtd_pontos_prevista` (o default silencioso é 1, e
   encolher fazia a medição virar 'realizado' cedo em routes.py);
4. status desconhecido na coluna Status do amostrador (nome de empresa, texto
   solto) NÃO vira 'laboratorio' sobre o estado real — isso ressuscitava
   amostrador 'concluido' na fila do laboratório;
5. valor conhecido ('UTILIZADO?') continua sendo aplicado.
"""
import io

import openpyxl
import pytest

from controle.db import get_db, init_db, upsert_empresa
from controle.import_xlsx import importar_amostradores, importar_medicoes

HDR_MED = ['UNIDADE', 'NUMERO DA O.S', 'RESPONSAVEL', 'AGENTE', 'AMOSTRADOR',
           'QUANTIDADE DE PONTOS', 'PONTOS AVALIADOS', 'LAUDAR', 'OBSERVACAO']
HDR_AMO = ['CODIGO', 'TIPO DE AMOSTRADOR', 'STATUS', 'DATA DE ENTRADA',
           'EMPRESA', 'AVALIADOR', 'DATA DA MEDICAO']

UNIDADE = 'REIMPORT TESTE LTDA'
OS_NUM = 'RI-9001'
CODIGO = 'RIMP-0001'


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _limpa():
    """Remove só o que este teste cria — o banco de teste nasce com seed."""
    init_db()
    with get_db() as conn:
        conn.execute(
            "DELETE FROM medicoes WHERE demanda_id IN "
            "(SELECT id FROM demandas WHERE numero_os=?)", (OS_NUM,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS_NUM,))
        conn.execute('DELETE FROM amostradores WHERE codigo=?', (CODIGO,))
    yield


def _medicao():
    with get_db() as conn:
        return dict(conn.execute(
            'SELECT m.* FROM medicoes m JOIN demandas d ON d.id = m.demanda_id '
            'WHERE d.numero_os=?', (OS_NUM,)).fetchone())


def _qtd_medicoes():
    with get_db() as conn:
        return conn.execute(
            'SELECT COUNT(*) c FROM medicoes m JOIN demandas d ON d.id = m.demanda_id '
            'WHERE d.numero_os=?', (OS_NUM,)).fetchone()['c']


def test_reimport_nao_reverte_medicao_finalizada():
    crua = _xlsx([HDR_MED,
                  [UNIDADE, OS_NUM, 'Fulano', 'Ruido', 'Dosimetro DOS', 3, None, 'S', None]])
    assert importar_medicoes(crua)['medicoes_inseridas'] == 1

    # o app finaliza (equivale a routes.py:2009 / lab_inbox.py:316)
    with get_db() as conn:
        conn.execute(
            "UPDATE medicoes SET status='realizado', qtd_pontos_feita=3, "
            "observacao='RA 4412 recebido' WHERE id=?", (_medicao()['id'],))

    res = importar_medicoes(crua)          # MESMA planilha, ainda sem avaliados
    assert res['medicoes_atualizadas'] == 1
    assert _qtd_medicoes() == 1            # dedup segue valendo

    m = _medicao()
    assert m['status'] == 'realizado'
    assert m['qtd_pontos_feita'] == 3
    assert m['observacao'] == 'RA 4412 recebido'


def test_planilha_mais_avancada_ainda_manda():
    parcial = _xlsx([HDR_MED,
                     [UNIDADE, OS_NUM, 'Fulano', 'Calor', '', 3, 2, 'N', 'parcial em campo']])
    importar_medicoes(parcial)
    assert _medicao()['status'] == 'parcial'

    completa = _xlsx([HDR_MED,
                      [UNIDADE, OS_NUM, 'Fulano', 'Calor', '', 3, 3, 'N', 'concluido em campo']])
    importar_medicoes(completa)

    m = _medicao()
    assert m['status'] == 'realizado'
    assert m['qtd_pontos_feita'] == 3
    assert m['observacao'] == 'concluido em campo'


def test_celula_vazia_nao_encolhe_pontos_previstos():
    cheia = _xlsx([HDR_MED,
                   [UNIDADE, OS_NUM, 'Fulano', 'Calor', '', 3, 3, 'N', 'ok']])
    importar_medicoes(cheia)

    vazia = _xlsx([HDR_MED,
                   [UNIDADE, OS_NUM, 'Fulano', 'Calor', '', None, None, '', '']])
    importar_medicoes(vazia)

    m = _medicao()
    assert m['qtd_pontos_prevista'] == 3   # o default silencioso e 1
    assert m['status'] == 'realizado'
    assert m['observacao'] == 'ok'


def _status_amostrador():
    with get_db() as conn:
        return conn.execute(
            'SELECT status FROM amostradores WHERE codigo=?', (CODIGO,)).fetchone()['status']


def test_status_desconhecido_nao_sobrescreve_amostrador_concluido():
    base = _xlsx([HDR_AMO, [CODIGO, 'Beckman', 'Estoque', '01/07/2026', '-', '', '']])
    assert importar_amostradores(base)['inserted'] == 1
    assert _status_amostrador() == 'disponivel'

    with get_db() as conn:
        conn.execute("UPDATE amostradores SET status='concluido' WHERE codigo=?", (CODIGO,))

    # coluna Status com NOME DE EMPRESA (caso real da planilha)
    sujo = _xlsx([HDR_AMO, [CODIGO, 'Beckman', 'MRV ENGENHARIA', '01/07/2026', '-', '', '']])
    importar_amostradores(sujo)
    assert _status_amostrador() == 'concluido'

    # valor CONHECIDO continua mandando
    conhecido = _xlsx([HDR_AMO, [CODIGO, 'Beckman', 'UTILIZADO?', '01/07/2026', '-', '', '']])
    importar_amostradores(conhecido)
    assert _status_amostrador() == 'laboratorio'


def test_celula_vazia_nao_apaga_empresa_nem_avaliador_do_amostrador():
    base = _xlsx([HDR_AMO, [CODIGO, 'Beckman', 'Estoque', '01/07/2026', '-', '', '']])
    importar_amostradores(base)

    empresa_id = upsert_empresa('', UNIDADE)
    with get_db() as conn:
        conn.execute("UPDATE amostradores SET empresa_id=?, avaliador='Helbert' "
                     'WHERE codigo=?', (empresa_id, CODIGO))

    # planilha com EMPRESA='-' e AVALIADOR vazio
    importar_amostradores(base)

    with get_db() as conn:
        row = dict(conn.execute('SELECT empresa_id, avaliador FROM amostradores '
                                'WHERE codigo=?', (CODIGO,)).fetchone())
    assert row['empresa_id'] == empresa_id
    assert row['avaliador'] == 'Helbert'


def test_arquivo_errado_segue_devolvendo_zero_abas():
    lixo = _xlsx([['a', 'b'], [1, 2]])
    assert importar_medicoes(lixo)['sheets_reconhecidas'] == 0
    assert importar_amostradores(lixo)['sheets_reconhecidas'] == 0
