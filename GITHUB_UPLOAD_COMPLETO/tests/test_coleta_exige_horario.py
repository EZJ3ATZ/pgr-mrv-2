# -*- coding: utf-8 -*-
"""Finalizar coleta sem horario de campo passa a ser recusado (02/09/2026).

O horario de inicio/termino e o que da o TEMPO DE EXPOSICAO do laudo. O
checklist da tela avisava ("horario de inicio/termino nao informado") mas nao
impedia nada, e duas coletas de producao foram finalizadas sem ele: uma de
ruido em 17/06 e a OS 6482868 de 16/07, esta com calor E vibracao em branco.

O rascunho (nvDraftSave, localStorage) nao passa por esta rota, entao a trava
nao faz ninguem perder trabalho no meio da visita.
"""
import json

import pytest

from app import app
from controle.db import get_db, init_db


def _seed():
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA HORARIO TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) "
            "VALUES (?, 'OS-HR-1', 'pendente')", (eid,))
        return eid, cur.lastrowid


def _post(payload):
    init_db()
    with get_db() as conn:
        uid = conn.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()['id']
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=payload)
        try:
            return r.status_code, r.get_json()
        except Exception:
            return r.status_code, {}


def _base():
    eid, did = _seed()
    return {'empresa_id': eid, 'demanda_id': did, 'empresa_nome': 'EMPRESA HORARIO TESTE',
            'data': '2026-09-02', 'avaliador': 'Helbert', 'os': 'OS-HR-1'}


@pytest.mark.parametrize('tipo,campo', [
    ('ruido', 'campo_ruido'),
    ('calor', 'campo_generico'),
    ('vibracao', 'campo_generico'),
])
def test_sem_horario_e_recusado(tipo, campo):
    p = _base()
    p['tipo'] = tipo
    p[campo] = {'acomp': 'Fulano', 'hora_ini': '', 'hora_fim': ''}
    status, body = _post(p)
    assert status == 400, (status, body)
    assert 'hora' in (body.get('erro') or '').lower(), body


@pytest.mark.parametrize('tipo,campo', [
    ('ruido', 'campo_ruido'),
    ('calor', 'campo_generico'),
])
def test_so_a_hora_de_termino_faltando_tambem_e_recusado(tipo, campo):
    p = _base()
    p['tipo'] = tipo
    p[campo] = {'hora_ini': '08:00', 'hora_fim': '  '}
    status, body = _post(p)
    assert status == 400, (status, body)


def test_nada_e_gravado_quando_recusa():
    """A trava roda ANTES de qualquer escrita — nem coleta, nem baixa."""
    p = _base()
    p['tipo'] = 'calor'
    p['campo_generico'] = {'hora_ini': '', 'hora_fim': ''}
    did = p['demanda_id']
    with get_db() as conn:
        antes = conn.execute(
            "SELECT COUNT(*) c FROM coletas_outros WHERE demanda_id=?", (did,)).fetchone()
        antes = antes['c'] if hasattr(antes, 'keys') else antes[0]
    _post(p)
    with get_db() as conn:
        depois = conn.execute(
            "SELECT COUNT(*) c FROM coletas_outros WHERE demanda_id=?", (did,)).fetchone()
        depois = depois['c'] if hasattr(depois, 'keys') else depois[0]
    assert depois == antes


def test_com_horario_continua_salvando():
    """O outro lado: a trava nao pode impedir a coleta boa."""
    p = _base()
    p['tipo'] = 'calor'
    p['campo_generico'] = {'hora_ini': '08:00', 'hora_fim': '11:30', 'ibutg_setores': []}
    status, body = _post(p)
    assert status == 200, (status, body)
    assert body.get('ok') is True, body
