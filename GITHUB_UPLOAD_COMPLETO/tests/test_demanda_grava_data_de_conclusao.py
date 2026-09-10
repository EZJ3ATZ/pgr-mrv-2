# -*- coding: utf-8 -*-
"""Concluir a OS tem de carimbar QUANDO — senão o ciclo não é medível.

Medido em produção em 10/09/2026: **220 demandas com status `concluida` e
apenas 5 com `data_conclusao`**. Nenhuma das pendentes, em andamento ou abertas
tem a coluna preenchida, e é assim porque nenhum dos caminhos que marca a
demanda como concluída grava a data junto — são cinco `UPDATE demandas SET
status='concluida'` no código e nenhum toca em `data_conclusao`.

O que isso custa: **não existe tempo de ciclo da ordem de serviço**. Quanto
tempo uma OS leva do Planner até o laudo é a primeira pergunta de qualquer
relatório de entrega, e hoje ela não tem resposta no banco. O tempo de
laboratório é medível (18,7 dias de média em 146 amostras) justamente porque as
duas pontas dele são gravadas; o da OS não.

`concluido_em_ms` não resolve: vem do Planner, está preenchido em 116 das 220 e
vazio em todas as concluídas recentes. É o carimbo de lá, não o daqui.

A data é gravada em ISO com hora (`agora_brt`), no mesmo UPDATE que muda o
status — nunca em dois passos, senão uma falha no meio deixa a demanda
concluída e sem data, que é exatamente o estado de hoje. E nunca sobrescreve
data já existente: reconcluir não pode mexer no carimbo original.
"""
import re

import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

OS = 'DTCONCL'
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}')


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os LIKE ?', (OS + '%',))
        conn.execute(
            "INSERT INTO demandas (numero_os, empresa_id, status, origem, criado_em) "
            "VALUES (?, ?, 'aberta', 'local', '2026-09-01')", (OS, eid))
        did = conn.execute('SELECT id FROM demandas WHERE numero_os=?', (OS,)).fetchone()['id']
    yield {'did': did, 'eid': eid}
    with get_db() as conn:
        for t in ('coletas_ruido', 'coletas_quimico', 'coletas_outros'):
            conn.execute(f'DELETE FROM {t} WHERE demanda_id=?', (did,))
        conn.execute('DELETE FROM demandas WHERE numero_os LIKE ?', (OS + '%',))


def _demanda(did):
    with get_db() as conn:
        return row_to_dict(
            conn.execute('SELECT * FROM demandas WHERE id=?', (did,)).fetchone())


def _concluir_por_coleta(cen):
    """Fecha a demanda pelo caminho da coleta finalizada.

    A regra tem dois degraus e o teste tem de respeitar os dois: demanda
    'aberta' vira 'em_andamento' na primeira coleta, e só a partir daí uma
    coleta concluída a fecha (e apenas quando `origem != 'planner'`, porque
    para demanda do Planner a fonte de verdade é de lá).
    """
    from controle.routes import _atualizar_demanda_por_coleta
    _atualizar_demanda_por_coleta(cen['did'], 'concluida', None)   # aberta -> em_andamento
    _atualizar_demanda_por_coleta(cen['did'], 'concluida', None)   # -> concluida


def test_concluir_carimba_a_data(_cenario):
    _concluir_por_coleta(_cenario)
    d = _demanda(_cenario['did'])
    assert d['status'] == 'concluida', d
    assert d['data_conclusao'], 'sem isso nao existe tempo de ciclo da OS'
    assert ISO.match(str(d['data_conclusao'])), d['data_conclusao']


def test_reconcluir_nao_reescreve_o_carimbo(_cenario):
    _concluir_por_coleta(_cenario)
    primeira = _demanda(_cenario['did'])['data_conclusao']
    with get_db() as conn:
        conn.execute("UPDATE demandas SET status='em_andamento' WHERE id=?",
                     (_cenario['did'],))
    _concluir_por_coleta(_cenario)
    assert _demanda(_cenario['did'])['data_conclusao'] == primeira


def test_demanda_que_nao_conclui_nao_ganha_data(_cenario):
    """Coleta salva sem concluir deixa a demanda em andamento e sem carimbo."""
    from controle.routes import _atualizar_demanda_por_coleta
    _atualizar_demanda_por_coleta(_cenario['did'], None, None)
    d = _demanda(_cenario['did'])
    assert d['status'] == 'em_andamento', d
    assert not d['data_conclusao'], d


def test_rota_de_concluir_tambem_carimba(_cenario):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post(f'/controle/demandas/{_cenario["did"]}/concluir', json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = _demanda(_cenario['did'])
    assert d['status'] == 'concluida', d
    assert d['data_conclusao'] and ISO.match(str(d['data_conclusao'])), d
