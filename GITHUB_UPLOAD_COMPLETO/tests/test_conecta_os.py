# -*- coding: utf-8 -*-
"""OS de medição anda no OCP Conecta conforme anda no portal (16/10/2026).

Planejamento feito = marco "planejada" (L1 com o técnico); demanda concluída =
marco "concluida" (L2 e L3). Cada marco vai uma vez só.
"""
import pytest

from app import app
from controle import conecta_os
from controle.db import get_db, init_db, row_to_dict

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
            conn.execute("DELETE FROM eventos WHERE ref_id=? AND tipo='conecta_os_marco'", (did,))
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


def test_sem_chave_nao_faz_nada(monkeypatch):
    monkeypatch.delenv('CONECTA_OS_KEY')
    assert conecta_os.sincronizar() == {'ativo': False}
