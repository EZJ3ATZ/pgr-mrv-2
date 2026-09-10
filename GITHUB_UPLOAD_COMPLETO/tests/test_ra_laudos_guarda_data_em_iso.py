# -*- coding: utf-8 -*-
"""As datas de `ra_laudos` têm de ficar em ISO, como as das outras tabelas.

Medido em produção em 10/09/2026: dos **95 laudos**, **94 guardam a data no
formato brasileiro** (`DD/MM/AAAA`) e **zero** em ISO — nas duas colunas,
`data_amostragem` e `data_recebimento`. As colunas são `TEXT`.

Data brasileira em coluna de texto ordena alfabeticamente: **`05/08` vem antes
de `29/06`**. Qualquer `ORDER BY`, `MIN`, `MAX` ou corte por período nessa
tabela devolve a ordem errada sem dar erro nenhum — e foi exatamente assim que
o dashboard caiu em 01/09, quando duas datas BR chegaram numa coluna que o
Postgres tentou converter (`date/time field value out of range`).

O parser do PDF lê `DD/MM/AAAA` porque é o que o laudo do laboratório traz —
isso continua igual. O que muda é a hora de GRAVAR: converte para ISO, como
`_upsert_ra_laudo` já fazia para `amostradores.data_medicao` com `_iso_br`. Data
que não casa o formato (vazia, ou texto solto do PDF) é gravada como veio, para
não perder o que o laudo dizia.

`normalizar_datas_ra_laudos` conserta o que já está gravado, na mesma rodada do
sync — mesmo padrão de `normalizar_datas_vazias`. É idempotente: rodar duas
vezes não muda nada na segunda.
"""
import pytest

from controle.db import get_db, init_db, row_to_dict
from controle.lab_inbox import (_ensure_ra_laudos, _iso_br,
                                normalizar_datas_ra_laudos)

COD = 'RAISO'


def _grava_direto(cod, amostragem, recebimento):
    """Insere como o banco de produção está hoje: data BR crua."""
    with get_db() as conn:
        _ensure_ra_laudos(conn)
        conn.execute('DELETE FROM ra_laudos WHERE amostrador_cod=?', (cod,))
        conn.execute(
            "INSERT INTO ra_laudos (amostrador_id, amostrador_cod, ra_num, "
            "data_amostragem, data_recebimento, criado_em) "
            "VALUES (NULL, ?, 'RA1', ?, ?, CURRENT_TIMESTAMP)",
            (cod, amostragem, recebimento))


def _le(cod):
    with get_db() as conn:
        return row_to_dict(conn.execute(
            'SELECT * FROM ra_laudos WHERE amostrador_cod=?', (cod,)).fetchone())


@pytest.fixture(autouse=True)
def _limpa():
    init_db()
    yield
    with get_db() as conn:
        try:
            conn.execute('DELETE FROM ra_laudos WHERE amostrador_cod LIKE ?', (COD + '%',))
        except Exception:
            pass


def test_iso_br_converte_o_formato_do_laudo():
    assert _iso_br('30/06/2026') == '2026-06-30'
    assert _iso_br('05/08/2026') == '2026-08-05'
    assert _iso_br('31/02/2026') == '', 'data impossivel nao vira ISO'
    assert _iso_br('') == ''
    assert _iso_br(None) == ''


def test_normaliza_o_que_ja_esta_gravado_em_br():
    _grava_direto(COD + '1', '29/06/2026', '05/08/2026')
    with get_db() as conn:
        normalizar_datas_ra_laudos(conn)
    r = _le(COD + '1')
    assert r['data_amostragem'] == '2026-06-29', r
    assert r['data_recebimento'] == '2026-08-05', r


def test_a_ordem_passa_a_ser_cronologica():
    """O caso que motiva tudo: em texto BR, 05/08 vinha antes de 29/06."""
    _grava_direto(COD + 'A', '29/06/2026', '29/06/2026')
    _grava_direto(COD + 'B', '05/08/2026', '05/08/2026')
    with get_db() as conn:
        normalizar_datas_ra_laudos(conn)
        ordem = [row_to_dict(r)['amostrador_cod'] for r in conn.execute(
            'SELECT amostrador_cod FROM ra_laudos WHERE amostrador_cod LIKE ? '
            'ORDER BY data_amostragem', (COD + '%',)).fetchall()]
    assert ordem == [COD + 'A', COD + 'B'], ordem


def test_nao_mexe_no_que_ja_esta_em_iso():
    _grava_direto(COD + '2', '2026-06-30', '2026-07-01')
    with get_db() as conn:
        normalizar_datas_ra_laudos(conn)
    r = _le(COD + '2')
    assert r['data_amostragem'] == '2026-06-30', r
    assert r['data_recebimento'] == '2026-07-01', r


def test_preserva_o_que_nao_e_data():
    """Texto solto que o parser não entendeu continua ali — não se apaga evidência."""
    _grava_direto(COD + '3', '', 'nao informado')
    with get_db() as conn:
        normalizar_datas_ra_laudos(conn)
    r = _le(COD + '3')
    assert r['data_recebimento'] == 'nao informado', r


def test_e_idempotente():
    _grava_direto(COD + '4', '29/06/2026', '05/08/2026')
    with get_db() as conn:
        primeira = normalizar_datas_ra_laudos(conn)
        segunda = normalizar_datas_ra_laudos(conn)
    assert primeira >= 1, primeira
    assert segunda == 0, 'a segunda rodada nao tem o que fazer'
    assert _le(COD + '4')['data_amostragem'] == '2026-06-29'
