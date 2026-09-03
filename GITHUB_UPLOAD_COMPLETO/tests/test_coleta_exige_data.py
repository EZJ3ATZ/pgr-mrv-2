# -*- coding: utf-8 -*-
"""Finalizar coleta sem a data passa a ser recusado (02/09/2026).

Sem data a coleta some de tudo que filtra por periodo: Planilhas Feitas, o
painel, e a conferencia contra o laudo do laboratorio. Ela so reaparece em
auditoria manual. Aconteceu 4 vezes em producao, todas em calor: Casa Espirita
17/06, Banco do Brasil 08/07 e as duas da Multi Formato em 27/08 — esta ultima
fez a medicao parecer inexistente num levantamento de campo.

A trava vale para TODOS os tipos, inclusive quimico (que nao tem a guarda de
horario). O rascunho e a coleta 'planejada' criada pelo planejamento nao passam
por esta rota, entao ninguem perde trabalho no meio da visita.
"""
import pytest

from app import app
from controle.db import get_db, init_db


def _seed():
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA DATA TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) "
            "VALUES (?, 'OS-DT-1', 'pendente')", (eid,))
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


def _base(**extra):
    eid, did = _seed()
    p = {'empresa_id': eid, 'demanda_id': did, 'empresa_nome': 'EMPRESA DATA TESTE',
         'avaliador': 'Helbert', 'os': 'OS-DT-1'}
    p.update(extra)
    return p


def _com_horario(p, tipo):
    """Preenche o horario para a guarda ANTERIOR nao roubar o teste — o 400
    tem de vir da data, nao de outra coisa."""
    p['tipo'] = tipo
    if tipo == 'ruido':
        p['campo_ruido'] = {'hora_ini': '08:00', 'hora_fim': '11:30'}
    elif tipo in ('calor', 'vibracao'):
        p['campo_generico'] = {'hora_ini': '08:00', 'hora_fim': '11:30'}
    elif tipo == 'quimico':
        p['campo_quimico'] = {'func_nome': 'Fulano', 'substancias': 'Sílica'}
    return p


@pytest.mark.parametrize('tipo', ['ruido', 'calor', 'vibracao', 'quimico'])
def test_sem_data_e_recusado(tipo):
    p = _com_horario(_base(data=''), tipo)
    status, body = _post(p)
    assert status == 400, (status, body)
    assert 'data' in (body.get('erro') or '').lower(), body


@pytest.mark.parametrize('valor', [None, '   ', '02/09/2026', '2026-9-2', 'hoje'])
def test_data_em_formato_invalido_tambem_e_recusada(valor):
    """O <input type='date'> so manda YYYY-MM-DD. Data em formato brasileiro
    chegando aqui e payload de outra origem, e gravada como texto ela quebra
    toda comparacao por periodo."""
    p = _com_horario(_base(data=valor), 'calor')
    status, body = _post(p)
    assert status == 400, (status, body)


def test_nada_e_gravado_quando_recusa():
    """A trava roda ANTES de qualquer escrita — nem coleta, nem baixa."""
    p = _com_horario(_base(data=''), 'calor')
    did = p['demanda_id']

    def _conta():
        with get_db() as conn:
            r = conn.execute("SELECT COUNT(*) c FROM coletas_outros WHERE demanda_id=?",
                             (did,)).fetchone()
            return r['c'] if hasattr(r, 'keys') else r[0]

    antes = _conta()
    _post(p)
    assert _conta() == antes


def test_com_data_continua_salvando():
    """O outro lado: a trava nao pode impedir a coleta boa."""
    p = _com_horario(_base(data='2026-09-02'), 'calor')
    p['campo_generico']['ibutg_setores'] = []
    status, body = _post(p)
    assert status == 200, (status, body)
    assert body.get('ok') is True, body
    with get_db() as conn:
        r = conn.execute("SELECT data_coleta FROM coletas_outros WHERE id=?",
                         (body['id'],)).fetchone()
    assert (r['data_coleta'] if hasattr(r, 'keys') else r[0]) == '2026-09-02'


def test_recusa_nao_deixa_foto_nem_assinatura_orfa():
    """A gravacao de fotos/assinaturas rodava no TOPO da rota, antes das
    guardas: finalizacao recusada deixava anexo no banco e a mensagem dizia
    'Nada foi salvo'. Pior, o anexo e chaveado por (demanda, empresa, data) —
    com a data em branco nascia com a chave errada e sumia da planilha
    remontada. O bloco passou a rodar depois das guardas."""
    p = _com_horario(_base(data=''), 'calor')
    p['fotos'] = [{'categoria': 'ambiente', 'data': 'data:image/png;base64,iVBORw0KGgo='}]
    p['sig_avaliado'] = 'data:image/png;base64,iVBORw0KGgo='

    def _conta():
        with get_db() as conn:
            r = conn.execute(
                "SELECT COUNT(*) c FROM visita_anexos WHERE empresa_nome=?",
                ('EMPRESA DATA TESTE',)).fetchone()
            return r['c'] if hasattr(r, 'keys') else r[0]

    antes = _conta()
    status, _ = _post(p)
    assert status == 400
    assert _conta() == antes, 'anexo gravado numa finalizacao recusada'
