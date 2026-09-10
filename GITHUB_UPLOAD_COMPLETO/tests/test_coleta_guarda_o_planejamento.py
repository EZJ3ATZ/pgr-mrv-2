# -*- coding: utf-8 -*-
"""A coleta tem de guardar de qual PLANEJAMENTO ela saiu.

Medido em produção em 10/09/2026: das **96 coletas** gravadas (31 de ruído, 41
químicas, 24 de calor/vibração), **nenhuma** tem `planejamento_id`. E os **37**
planejamentos existentes não têm uma coleta sequer apontando para eles — a
tabela liga, mas o lado da coleta nasce nulo, sempre.

Não é o wizard que esquece de mandar: `nvSalvarMedicao` (templates/index.html)
põe `planejamento_id` no corpo, e a própria rota usa esse valor para
`_atualizar_demanda_por_coleta`. O que descarta é a gravação — a lista `campos`
de `save_coleta_ruido`, `save_coleta_quimico` e `save_coleta_outros` (controle/db.py)
não tem a coluna, então `vals` nunca a inclui no INSERT.

O que se perde com o vínculo nulo:
  - "o que foi planejado x o que foi medido" na tela de Planejamento;
  - a lista de agentes previstos e não executados da visita;
  - qualquer contagem de retrabalho ou de visita que precisou voltar.

A coluna já existe nas três tabelas desde a migração — só nunca foi preenchida.
"""
import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

OS = 'PLANVINC'


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))
        conn.execute("INSERT INTO demandas (numero_os, empresa_id, status, criado_em) "
                     "VALUES (?, ?, 'aberta', '2026-09-01')", (OS, eid))
        did = conn.execute('SELECT id FROM demandas WHERE numero_os=?', (OS,)).fetchone()['id']
        conn.execute("DELETE FROM planejamentos WHERE numero_os=?", (OS,))
        cur = conn.execute(
            "INSERT INTO planejamentos (demanda_id, empresa_id, numero_os, tecnico, "
            "data_prevista, status, criado_em) "
            "VALUES (?, ?, ?, 'Tecnico', '2026-09-10', 'confirmado', '2026-09-01')",
            (did, eid, OS))
        pid = cur.lastrowid
    yield {'did': did, 'eid': eid, 'pid': pid}
    with get_db() as conn:
        for t in ('coletas_ruido', 'coletas_quimico', 'coletas_outros'):
            conn.execute(f'DELETE FROM {t} WHERE demanda_id=?', (did,))
        conn.execute('DELETE FROM planejamentos WHERE numero_os=?', (OS,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))


def _salvar(cenario, tipo, bloco):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    payload = {
        'tipo': tipo, 'empresa_id': cenario['eid'], 'empresa_nome': 'Teste',
        'demanda_id': cenario['did'], 'planejamento_id': cenario['pid'],
        'data': '2026-09-10', 'avaliador': 'Tecnico', 'os': OS,
    }
    payload.update(bloco)
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=payload)
    return r.status_code, (r.get_json() or {})


def _coleta(tabela, did):
    with get_db() as conn:
        row = conn.execute(
            f'SELECT * FROM {tabela} WHERE demanda_id=? ORDER BY id DESC LIMIT 1',
            (did,)).fetchone()
    return row_to_dict(row) if row else None


def test_ruido_guarda_o_planejamento(_cenario):
    st, corpo = _salvar(_cenario, 'ruido', {'campo_ruido': {
        'hora_ini': '08:00', 'hora_fim': '16:00', 'acomp': 'Fulano',
        'trabalhadores': [{'nome': 'Trabalhador'}]}})
    assert st == 200, corpo
    c = _coleta('coletas_ruido', _cenario['did'])
    assert c and c['planejamento_id'] == _cenario['pid'], c


def test_quimico_guarda_o_planejamento(_cenario):
    st, corpo = _salvar(_cenario, 'quimico', {'campo_quimico': {
        'func_nome': 'Trabalhador', 'substancias': 'Poeira Total',
        'amostradores': [{'id_amostrador': 'PLANVINC1', 'vazao_inicial': 2.0,
                          'vazao_final': 1.98, 'tempo_min': 480}]}})
    assert st == 200, corpo
    c = _coleta('coletas_quimico', _cenario['did'])
    assert c and c['planejamento_id'] == _cenario['pid'], c


@pytest.mark.parametrize('tipo', ['calor', 'vibracao_vbma'])
def test_outros_guarda_o_planejamento(_cenario, tipo):
    st, corpo = _salvar(_cenario, tipo, {'campo_generico': {
        'hora_ini': '09:00', 'hora_fim': '11:00', 'acomp': 'Fulano'}})
    assert st == 200, corpo
    c = _coleta('coletas_outros', _cenario['did'])
    assert c and c['planejamento_id'] == _cenario['pid'], c


def test_visita_sem_planejamento_continua_valida(_cenario):
    """Visita avulsa (sem planejamento) não pode quebrar — grava nulo, como antes."""
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json={
            'tipo': 'ruido', 'empresa_id': _cenario['eid'], 'empresa_nome': 'Teste',
            'demanda_id': _cenario['did'], 'data': '2026-09-10', 'avaliador': 'Tecnico',
            'campo_ruido': {'hora_ini': '08:00', 'hora_fim': '16:00',
                            'trabalhadores': [{'nome': 'X'}]}})
    assert r.status_code == 200, r.get_data(as_text=True)
    c = _coleta('coletas_ruido', _cenario['did'])
    assert c and c['planejamento_id'] is None, c
