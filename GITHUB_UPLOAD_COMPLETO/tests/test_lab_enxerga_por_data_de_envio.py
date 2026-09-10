# -*- coding: utf-8 -*-
"""Estar no laboratório é ter SIDO ENVIADO, não estar com o status certo.

Medido em produção em 10/09/2026: **179 amostradores** com `data_envio_lab`
preenchida e nenhum `data_resultado` — e só **16** com `status='laboratorio'`.
Os outros 163 estavam invisíveis para todo o motor do laboratório, porque cada
peça dele filtrava pelo status:

  - `_alertar_resultados_atrasados` — nunca alertava sobre eles;
  - a reconciliação `status='laboratorio' AND data_resultado<>'' -> concluido` —
    nunca os fechava;
  - o backfill de RAs — classificava como `fora_do_lab` e **descartava o
    resultado do laboratório**, mesmo tendo o PDF do laudo na mão.

O último é o mais caro: o laboratório devolvia o resultado e o sistema jogava
fora porque a anotação de estado estava errada.

A causa raiz (a rota de envio que não movia o status) foi corrigida à parte.
Este arquivo trava a outra metade: **o motor passa a acreditar na data, não na
anotação**. `data_envio_lab` preenchida e `data_resultado` vazia é a definição
de "está no laboratório" — a data é o fato, o status é o rótulo.

Histórico e prateleira continuam de fora: `concluido`, `devolvido` e
`descartado` não voltam à fila por terem uma data de envio antiga.
"""
import pytest

from controle.db import get_db, init_db, row_to_dict
from controle.lab_inbox import _alertar_resultados_atrasados

PREFIXO = 'LABVER'


def _cria(codigo, status, envio, resultado=''):
    with get_db() as conn:
        conn.execute('DELETE FROM amostradores WHERE codigo=?', (codigo,))
        cur = conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, data_entrada, "
            "data_envio_lab, data_resultado) VALUES (?, 'PVC', ?, '2026-01-01', ?, ?)",
            (codigo, status, envio, resultado))
        return cur.lastrowid


def _eventos(aid):
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM eventos WHERE tipo='lab_resultado_atrasado' "
            "AND ref_id=? AND ref_tipo='amostrador'", (aid,)).fetchone()[0]


@pytest.fixture(autouse=True)
def _limpa():
    init_db()
    yield
    with get_db() as conn:
        ids = [row_to_dict(r)['id'] for r in conn.execute(
            'SELECT id FROM amostradores WHERE codigo LIKE ?', (PREFIXO + '%',)).fetchall()]
        for aid in ids:
            conn.execute("DELETE FROM eventos WHERE ref_id=? AND ref_tipo='amostrador'", (aid,))
        conn.execute('DELETE FROM amostradores WHERE codigo LIKE ?', (PREFIXO + '%',))


@pytest.mark.parametrize('status', ['laboratorio', 'disponivel', 'reservado'])
def test_alerta_de_atraso_ve_quem_foi_enviado_e_nao_voltou(status):
    """O status errado não pode esconder uma amostra parada há 200 dias."""
    aid = _cria(PREFIXO + '1', status, '2026-01-28')
    with get_db() as conn:
        _alertar_resultados_atrasados(conn, dias=15)
    assert _eventos(aid) == 1, (
        f'amostra enviada em 28/01 e sem resultado tem de alertar mesmo com '
        f'status {status!r}')


@pytest.mark.parametrize('status', ['concluido', 'devolvido', 'descartado'])
def test_alerta_ignora_o_que_ja_fechou(status):
    """Histórico não volta à fila por ter data de envio antiga."""
    aid = _cria(PREFIXO + '2', status, '2026-01-28')
    with get_db() as conn:
        _alertar_resultados_atrasados(conn, dias=15)
    assert _eventos(aid) == 0, f'{status} nao pode alertar'


def test_alerta_ignora_quem_ja_tem_resultado():
    aid = _cria(PREFIXO + '3', 'disponivel', '2026-01-28', resultado='2026-02-10')
    with get_db() as conn:
        _alertar_resultados_atrasados(conn, dias=15)
    assert _eventos(aid) == 0


def test_alerta_ignora_quem_esta_dentro_do_prazo():
    from datetime import date, timedelta
    recente = (date.today() - timedelta(days=3)).isoformat()
    aid = _cria(PREFIXO + '4', 'disponivel', recente)
    with get_db() as conn:
        _alertar_resultados_atrasados(conn, dias=15)
    assert _eventos(aid) == 0


def test_alerta_nao_repete_para_o_mesmo_amostrador():
    aid = _cria(PREFIXO + '5', 'disponivel', '2026-01-28')
    with get_db() as conn:
        _alertar_resultados_atrasados(conn, dias=15)
        _alertar_resultados_atrasados(conn, dias=15)
    assert _eventos(aid) == 1
