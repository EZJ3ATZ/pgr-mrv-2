# -*- coding: utf-8 -*-
"""Tubo novo que o laboratório manda entra sozinho no estoque (06/10/2026).

Antes o código de remessa que não existia no cadastro ia para `fora` e
dependia de alguém digitar tubo por tubo: o estoque deixava de bater com a
prateleira, o planejamento barrava ("amostrador já reservado") e a cadeia de
custódia recusava o tubo — e o técnico voltava para a planilha.
"""
import pytest

import controle.lab_inbox as lab
from controle.db import get_db, init_db, row_to_dict

CODIGOS = ('TCP9901AV1', 'PVC99R01', 'NF12345', 'OS6718294', 'TCP9902AV1')


def _email(data_full, corpo):
    return {'id': 'm-remessa', 'subject': 'Envio de amostradores', 'from': 'solicitacao@uniscientificgroup.com.br',
            'data': data_full[:10], 'data_full': data_full, 'body': corpo, 'anexos': False, 'caixa': 'x@y'}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    init_db()
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
        antigo = conn.execute("SELECT valor FROM ms_sync_state WHERE chave='lab_watermark'").fetchone()
        conn.execute("DELETE FROM ms_sync_state WHERE chave='lab_watermark'")
        conn.execute("INSERT INTO ms_sync_state (chave, valor) VALUES ('lab_watermark', '2026-10-01T00:00:00Z')")
        for c in CODIGOS:
            conn.execute('DELETE FROM amostradores WHERE codigo=?', (c,))
    monkeypatch.setattr(lab, '_fetch_sent_to_lab', lambda *a, **k: [])
    yield
    with get_db() as conn:
        for c in CODIGOS:
            conn.execute('DELETE FROM amostradores WHERE codigo=?', (c,))
        conn.execute("DELETE FROM ms_sync_state WHERE chave='lab_watermark'")
        if antigo:
            conn.execute("INSERT INTO ms_sync_state (chave, valor) VALUES ('lab_watermark', ?)",
                         (row_to_dict(antigo)['valor'],))


def _rodar(monkeypatch, emails):
    monkeypatch.setattr(lab, '_fetch_lab_emails', lambda boxes, top: (emails, {}))
    return lab.sincronizar_lab(apply=True, parse_anexos=False)


def _tubos():
    with get_db() as conn:
        return {row_to_dict(r)['codigo']: row_to_dict(r) for r in conn.execute(
            "SELECT codigo, tipo, status, observacao FROM amostradores WHERE codigo IN (?,?,?,?,?)",
            CODIGOS).fetchall()}


def test_remessa_nova_cadastra_so_tubo_de_verdade(monkeypatch):
    r = _rodar(monkeypatch, [_email('2026-10-06T12:00:00Z',
                                    'Seguem TCP9901AV1 e PVC99R01. NF12345, OS6718294.')])
    t = _tubos()
    assert set(t) == {'TCP9901AV1', 'PVC99R01'}
    assert t['TCP9901AV1']['tipo'] == 'TCP' and t['TCP9901AV1']['status'] == 'disponivel'
    assert 'remessa' in t['PVC99R01']['observacao']
    assert sorted(r['tubos_cadastrados_remessa']) == ['PVC99R01', 'TCP9901AV1']


def test_rodar_de_novo_nao_duplica(monkeypatch):
    emails = [_email('2026-10-06T12:00:00Z', 'Seguem TCP9901AV1.')]
    _rodar(monkeypatch, emails)
    _rodar(monkeypatch, emails)
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM amostradores WHERE codigo='TCP9901AV1'").fetchone()['c'] == 1


def test_remessa_antiga_nao_cria(monkeypatch):
    _rodar(monkeypatch, [_email('2026-09-20T12:00:00Z', 'Seguem TCP9902AV1.')])
    assert 'TCP9902AV1' not in _tubos()


def test_codigo_arquivado_nao_e_recriado(monkeypatch):
    with get_db() as conn:
        conn.execute("INSERT INTO amostradores (codigo, tipo, status, arquivado) VALUES ('TCP9902AV1','TCP','concluido',1)")
    _rodar(monkeypatch, [_email('2026-10-06T12:00:00Z', 'Seguem TCP9902AV1.')])
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM amostradores WHERE codigo='TCP9902AV1'").fetchone()['c'] == 1
