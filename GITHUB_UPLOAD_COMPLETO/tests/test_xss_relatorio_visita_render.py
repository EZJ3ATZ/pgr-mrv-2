# -*- coding: utf-8 -*-
"""Prova o XSS na TELA, não no helper — renderiza a rota real com dado envenenado.

Unidade de `_e()` passando não diz nada sobre a página: o que interessa é se algum
campo escapou do escape. Este teste envenena TODOS os campos livres da visita de uma
vez e exige que nenhum `<script>` chegue ao HTML servido.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import app as flask_app
from controle.db import get_db, init_db

XSS = '<script>alert(1)</script>'
ATTR = 'x" onerror="alert(2)'


def _usuario_tecnico():
    """A rota fica atras do gate de login (`before_request` + flask_login).
    Cria um usuario de verdade e assume a sessao dele -- e assim que um tecnico
    abre o relatorio, que e exatamente o cenario do ataque."""
    init_db()
    with get_db() as conn:
        row = conn.execute("SELECT id FROM usuarios WHERE email=?",
                           ('xsstest@ocupacional.com.br',)).fetchone()
        if row:
            return str(dict(row)['id'])
        cur = conn.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
            "VALUES (?,?,?,?,1)",
            ('Teste XSS', 'xsstest@ocupacional.com.br', 'x', 'tecnico'))
        conn.commit()
        return str(cur.lastrowid)


@pytest.fixture()
def cliente():
    flask_app.config['TESTING'] = True
    uid = _usuario_tecnico()
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess['_user_id'] = uid
            sess['_fresh'] = True
        yield c


_seq = itertools.count(1)


def _criar_visita_envenenada():
    init_db()
    with get_db() as conn:
        cnpj = '00.000.000/%04d-00' % next(_seq)   # cnpj e UNIQUE na tabela
        cur = conn.execute(
            "INSERT INTO empresas (nome, cnpj) VALUES (?, ?)", (XSS, cnpj))
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO visitas_tecnicas "
            "(empresa_id, data_visita, tecnico, tipo_visita, resultado, "
            " observacao_geral, justificativa, imprevisto_tipo, ciencia_texto, "
            " assinante_nome, assinante_cargo, acompanhante, sem_assinatura_motivo, "
            " hora_inicio, hora_termino) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, '2026-08-13', XSS, XSS, 'concluido',
             XSS, XSS, XSS, XSS, XSS, XSS, XSS, XSS, XSS, XSS))
        vid = cur.lastrowid
        conn.commit()
    return vid


def test_nenhum_campo_livre_vira_script_na_pagina(cliente):
    vid = _criar_visita_envenenada()
    r = cliente.get('/controle/relatorio_visita/%d' % vid)
    assert r.status_code == 200
    corpo = r.get_data(as_text=True)
    assert '<script>alert(1)</script>' not in corpo, 'XSS armazenado chegou ao HTML'
    assert '&lt;script&gt;' in corpo, 'o dado sumiu — devia aparecer escapado, não some'


def test_assinatura_forjada_nao_sai_do_atributo_src(cliente):
    vid = _criar_visita_envenenada()
    with get_db() as conn:
        conn.execute("UPDATE visitas_tecnicas SET assinatura=?, assinatura_empresa=? "
                     "WHERE id=?",
                     ('data:image/png;base64,' + ATTR, 'data:image/png;base64,' + ATTR, vid))
        conn.commit()
    r = cliente.get('/controle/relatorio_visita/%d' % vid)
    assert r.status_code == 200, 'sem 200 o teste nao prova nada'
    corpo = r.get_data(as_text=True)
    assert 'onerror=' not in corpo, 'a data-URI forjada saiu do src'


def test_a_query_da_rota_so_pede_colunas_que_existem():
    """A rota selecionava `d.agentes`, que NUNCA existiu no schema (127 commits do
    db.py, e conferido no Postgres de produção: só existe `agentes_manual`). O alias
    não era usado em lugar nenhum — era SELECT morto que derrubava a tela inteira
    com 500 desde 28/05/2026. Como `visitas_tecnicas` está vazia em produção,
    ninguém tinha esbarrado.
    """
    import re
    from controle import routes as _r
    fonte = open(_r.__file__.replace('.pyc', '.py'), encoding='utf-8').read()
    assert 'd.agentes' not in fonte, 'coluna inexistente voltou para a query'
