# -*- coding: utf-8 -*-
"""OS de medição anda no OCP Conecta conforme anda no portal (16/10/2026).

Planejamento feito = marco "planejada" (L1 com o técnico); campo finalizado com o
laboratório pendente = marco "campo" (L2 e a tarefa de atualizar o PGR, com a
previsão de entrega da planilha); demanda concluída = marco "concluida" (L3).
Cada marco vai uma vez só.
"""
import pytest

from app import app
from controle import conecta_os, routes
from controle.db import get_db, init_db, row_to_dict
from controle.os_medicao import situacao

SECRET = 'conecta-secret-teste'


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv('CRM_PLANNER_SECRET', SECRET)
    monkeypatch.setenv('AVISO_MEDICOES_ATIVO', '0')
    monkeypatch.setenv('CONECTA_OS_KEY', 'chave-teste')
    init_db()
    yield
    with get_db() as conn:
        for r in conn.execute("SELECT id FROM demandas WHERE origem_ref='crm:neg-conecta-1'").fetchall():
            did = row_to_dict(r)['id']
            conn.execute('DELETE FROM medicoes WHERE demanda_id=?', (did,))
            conn.execute('DELETE FROM planejamentos WHERE demanda_id=?', (did,))
            conn.execute('DELETE FROM coletas_ruido WHERE demanda_id=?', (did,))
            conn.execute("DELETE FROM eventos WHERE ref_id=? AND tipo IN ('conecta_os_marco', 'previsao_entrega')",
                         (did,))
            conn.execute('DELETE FROM demandas WHERE id=?', (did,))
        conn.execute("DELETE FROM empresas WHERE nome='CONECTA Teste Ltda'")
        conn.execute("DELETE FROM usuarios WHERE email='tec.conecta@ocupacional.com.br'")


def _criar():
    p = {'origem': 'crm', 'origem_id': 'neg-conecta-1', 'numero_os': 'MED-CONECTA-001',
         'empresa': {'nome': 'CONECTA Teste Ltda'},
         'local': {'cidade': 'Contagem', 'uf': 'MG'}, 'contato_local': {'nome': 'Ana', 'telefone': '1'},
         'itens': [{'produto': 'Ruído', 'quantidade': 2}]}
    with app.test_client() as c:
        r = c.post('/controle/os/medicao', headers={'x-crm-secret': SECRET}, json=p)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['demanda_id']


def test_planejada_e_concluida_vao_uma_vez_cada(monkeypatch):
    did = _criar()
    mandados = []

    def falso_post(url, chave, corpo):
        mandados.append(corpo)
        return {'ok': True, 'encontrada': True, 'numero': 'MED-CONECTA-001', 'concluidas': ['L1']}
    monkeypatch.setattr(conecta_os, '_post', falso_post)

    assert conecta_os.sincronizar()['feitos'] == []          # recebida: nada a mandar
    with get_db() as conn:
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                     "VALUES ('Tecnico Conecta','tec.conecta@ocupacional.com.br','x','tecnico',1)")
        conn.execute("INSERT INTO planejamentos (demanda_id, empresa_id, numero_os, tecnico, data_prevista, status) "
                     "SELECT id, empresa_id, 'MED-CONECTA-001', 'Tecnico Conecta', '2026-10-20', 'confirmado' "
                     "FROM demandas WHERE id=?", (did,))
    conecta_os.sincronizar()
    conecta_os.sincronizar()
    assert [m['marco'] for m in mandados] == ['planejada']
    assert mandados[0]['executor_email'] == 'tec.conecta@ocupacional.com.br'
    assert mandados[0]['origem_id'] == 'neg-conecta-1'

    with get_db() as conn:
        conn.execute("UPDATE demandas SET status='concluida' WHERE id=?", (did,))
    conecta_os.sincronizar()
    conecta_os.sincronizar()
    assert [m['marco'] for m in mandados] == ['planejada', 'concluida']


def test_os_que_ainda_nao_esta_no_conecta_tenta_de_novo(monkeypatch):
    did = _criar()
    with get_db() as conn:
        conn.execute("INSERT INTO planejamentos (demanda_id, empresa_id, numero_os, tecnico, status) "
                     "SELECT id, empresa_id, 'MED-CONECTA-001', 'X', 'confirmado' FROM demandas WHERE id=?", (did,))
    respostas = [{'ok': True, 'encontrada': False, 'concluidas': []},
                 {'ok': True, 'encontrada': True, 'concluidas': ['L1']}]
    monkeypatch.setattr(conecta_os, '_post', lambda *a: respostas.pop(0))
    assert conecta_os.sincronizar()['feitos'] == []
    assert len(conecta_os.sincronizar()['feitos']) == 1


def test_planilha_de_campo_grava_a_previsao_de_entrega():
    did = _criar()
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    planilha = {'tipo': 'ruido', 'empresa_nome': 'CONECTA Teste Ltda', 'demanda_id': did, 'data': '2026-10-30',
                'avaliador': 'Tecnico', 'os': 'MED-CONECTA-001', 'prazo': '2026-11-20',
                'campo_ruido': {'hora_ini': '08:00', 'hora_fim': '16:00', 'acomp': 'Ana',
                                'trabalhadores': [{'nome': 'Trabalhador'}]}}
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=planilha)
    assert r.status_code == 200, r.get_json()
    assert situacao('neg-conecta-1')['previsao_entrega'] == '2026-11-20'


def test_previsao_vale_a_ultima_e_nao_repete():
    did = _criar()
    with app.test_request_context():
        for prazo in ('2026-11-20', '2026-11-20', '', '20/11/2026', '2026-12-01'):
            routes._guardar_previsao_entrega({'demanda_id': did, 'prazo': prazo}, 'Tecnico')
    with get_db() as conn:
        n = row_to_dict(conn.execute("SELECT COUNT(*) AS c FROM eventos WHERE tipo='previsao_entrega' AND ref_id=?",
                                     (did,)).fetchone())['c']
    assert n == 2
    assert situacao('neg-conecta-1')['previsao_entrega'] == '2026-12-01'


def test_campo_finalizado_manda_campo_com_a_previsao(monkeypatch):
    """Ideia do Helbert (28/10/2026): campo finalizado com o laboratório pendente já abre
    no Conecta a tarefa de atualizar o PGR, com a previsão de entrega da planilha."""
    did = _criar()
    mandados = []

    def falso_post(url, chave, corpo):
        mandados.append(corpo)
        return {'ok': True, 'encontrada': True, 'numero': 'MED-CONECTA-001', 'concluidas': ['L2']}
    monkeypatch.setattr(conecta_os, '_post', falso_post)

    with get_db() as conn:
        conn.execute("INSERT INTO coletas_ruido (demanda_id, empresa_nome, data_coleta, status) "
                     "VALUES (?, 'CONECTA Teste Ltda', '2026-10-30', 'concluida')", (did,))
        conn.execute("UPDATE medicoes SET status='parcial' WHERE demanda_id=?", (did,))
    conecta_os.sincronizar()
    assert [m['marco'] for m in mandados] == ['planejada']      # campo ainda com medição por fazer

    with get_db() as conn:
        conn.execute("UPDATE medicoes SET status='aguardando_lab' WHERE demanda_id=?", (did,))
    with app.test_request_context():
        routes._guardar_previsao_entrega({'demanda_id': did, 'prazo': '2026-11-20'}, 'Tecnico')
    conecta_os.sincronizar()
    conecta_os.sincronizar()
    assert [m['marco'] for m in mandados] == ['planejada', 'campo']
    assert mandados[1]['prazo_entrega'] == '2026-11-20' and '20/11/2026' in mandados[1]['detalhe']
    assert 'prazo_entrega' not in mandados[0]

    with get_db() as conn:
        conn.execute("UPDATE demandas SET status='concluida' WHERE id=?", (did,))
    conecta_os.sincronizar()
    assert [m['marco'] for m in mandados] == ['planejada', 'campo', 'concluida']
    assert mandados[2]['prazo_entrega'] == '2026-11-20'


def test_sem_chave_nao_faz_nada(monkeypatch):
    monkeypatch.delenv('CONECTA_OS_KEY')
    assert conecta_os.sincronizar() == {'ativo': False}
