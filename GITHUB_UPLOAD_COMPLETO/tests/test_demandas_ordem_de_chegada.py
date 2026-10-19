# -*- coding: utf-8 -*-
"""Demandas / OS em ordem de chegada (Helbert, grupo Os Medidores, 19/10/2026).

"tem como deixar as O.S por ordem de chegada? mesmo eu classificando lá, elas
não ficaram em ordem." A lista só ordenava por empresa, prazo e atrasadas, e as
OS que o CRM manda direto (Pacto 16/10, Mineral 19/10) ficavam no meio, pelo prazo.

Chegada = criação no Planner (`criado_em_ms`, AAAA-MM-DD) ou, na OS do CRM, a
entrada no portal (`criado_em`, com hora). Empresa com OS pendente vem antes da
que só tem OS concluída, nas duas direções.
"""
import pytest

from controle.db import get_db, init_db, list_operational_por_empresa

PREFIXO = 'ZZORDCHEG'


@pytest.fixture(autouse=True)
def _semeia():
    init_db()
    with get_db() as conn:
        ids = {}
        for nome in ('A antiga', 'B crm nova', 'C so concluida', 'D duas'):
            conn.execute('INSERT INTO empresas (nome) VALUES (?)', (f'{PREFIXO} {nome}',))
            ids[nome] = conn.execute('SELECT id FROM empresas WHERE nome=?',
                                     (f'{PREFIXO} {nome}',)).fetchone()['id']

        def dem(emp, origem, status, criado_em_ms, criado_em, n):
            conn.execute(
                "INSERT INTO demandas (numero_os, empresa_id, titulo, status, origem, tipo_demanda, "
                "criado_em_ms, criado_em) VALUES (?, ?, ?, ?, ?, 'operacional', ?, ?)",
                (f'{PREFIXO}{n}', ids[emp], f'{PREFIXO}{n} - {emp}', status, origem,
                 criado_em_ms, criado_em))

        dem('A antiga', 'planner', 'aberta', '2026-09-01', '2026-09-01 10:00:00', 1)
        dem('B crm nova', 'crm_os', 'pendente', None, '2026-10-19 11:58:35', 2)
        dem('C so concluida', 'planner', 'concluida', '2026-10-18', '2026-10-18 10:00:00', 3)
        dem('D duas', 'planner', 'em_andamento', '2026-08-01', '2026-08-01 10:00:00', 4)
        dem('D duas', 'planner', 'aberta', '2026-10-10', '2026-10-10 10:00:00', 5)
    yield
    with get_db() as conn:
        conn.execute('DELETE FROM demandas WHERE numero_os LIKE ?', (PREFIXO + '%',))
        conn.execute('DELETE FROM empresas WHERE nome LIKE ?', (PREFIXO + '%',))


def _lista(ordem):
    return list_operational_por_empresa({'ordem': ordem, 'empresa': PREFIXO})


def _nomes(ordem):
    return [r['empresa_nome'].replace(PREFIXO + ' ', '') for r in _lista(ordem)]


def test_mais_nova_primeiro_poe_a_os_do_crm_de_hoje_no_topo():
    assert _nomes('chegada') == ['B crm nova', 'D duas', 'A antiga', 'C so concluida']


def test_mais_antiga_primeiro_e_a_fila_pela_os_pendente_mais_velha():
    assert _nomes('chegada_antiga') == ['D duas', 'A antiga', 'B crm nova', 'C so concluida']


def test_devolve_a_data_que_decidiu_a_posicao():
    r = {x['empresa_nome'].replace(PREFIXO + ' ', ''): x for x in _lista('chegada')}
    assert r['B crm nova']['chegada_pendente_nova'] == '2026-10-19 11:58:35'
    assert r['D duas']['chegada_pendente_nova'] == '2026-10-10'
    assert r['D duas']['chegada_pendente_antiga'] == '2026-08-01'
    assert r['C so concluida']['chegada_pendente_nova'] is None
    assert r['C so concluida']['chegada_ultima'] == '2026-10-18'


def test_ordens_antigas_continuam_iguais():
    # 'empresa' é o padrão da rota (quem chama sem ordem) e 'recente' alimenta o card da Home.
    assert _nomes('empresa') == ['A antiga', 'B crm nova', 'C so concluida', 'D duas']
    assert _nomes('recente') == ['B crm nova', 'C so concluida', 'D duas', 'A antiga']
