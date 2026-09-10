# -*- coding: utf-8 -*-
"""`/controle/demandas?limit=N` tem de devolver no máximo N demandas.

`list_demandas` recebia os filtros da query string e terminava a consulta com
`LIMIT 2000` fixo, ignorando qualquer `limit` que viesse na URL. Medido na
bancada em 10/09/2026: `?limit=5` devolveu **85** registros.

Quem pede limite e não recebe:

  - a aba **Analytics BI** chama `/controle/demandas?limit=1000` no `biLoad()`
    e baixa a tabela inteira;
  - a **Home** pede as demandas recentes com limite pequeno para montar o card.

Hoje são 288 demandas em produção, então o teto de 2000 ainda não estourou e
ninguém viu erro — o custo é banda e memória à toa em toda abertura de aba, e um
contrato que mente. Quando a tabela passar de 2000, a lista passa a truncar
silenciosamente e o BI começa a somar errado sem avisar.

O teto de 2000 continua existindo como guarda-chuva: `limit` só o reduz, nunca o
aumenta. Pedido inválido (zero, negativo, texto) cai no teto, que é o
comportamento de antes.
"""
import pytest

from app import app
from controle.db import get_db, init_db, list_demandas

PREFIXO = 'LIMDEM'


@pytest.fixture(autouse=True)
def _semeia():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        for i in range(12):
            conn.execute(
                "INSERT INTO demandas (numero_os, empresa_id, status, prazo, criado_em) "
                "VALUES (?, ?, 'aberta', '2026-09-30', '2026-09-01')",
                (f'{PREFIXO}{i:03d}', eid))
    yield
    with get_db() as conn:
        conn.execute('DELETE FROM demandas WHERE numero_os LIKE ?', (PREFIXO + '%',))


def test_limit_corta_a_lista():
    assert len(list_demandas({'limit': 5})) == 5


def test_limit_vale_em_todas_as_ordens():
    for ordem in ('prazo', 'empresa', 'data_criacao'):
        assert len(list_demandas({'limit': 3, 'ordem': ordem})) == 3, ordem


def test_sem_limit_devolve_tudo_como_antes():
    todas = list_demandas({})
    assert len(todas) >= 12, 'o seed do teste tem de aparecer'


def test_limit_invalido_cai_no_teto_e_nao_quebra():
    for ruim in ('abc', '', '0', '-3', None):
        n = len(list_demandas({'limit': ruim}))
        assert n >= 12, (ruim, n)


def test_limit_nao_ultrapassa_o_teto_de_2000():
    assert len(list_demandas({'limit': 999999})) <= 2000


def test_rota_repassa_o_limit_da_url():
    init_db()
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.get('/controle/demandas?limit=4')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(r.get_json()) == 4, r.get_json()
