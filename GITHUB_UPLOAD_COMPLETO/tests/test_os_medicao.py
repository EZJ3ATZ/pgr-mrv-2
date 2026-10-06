# -*- coding: utf-8 -*-
"""OS de medição que chega do CRM pronta (padrão de 06/10/2026).

Trava o que a decisão do Matheus pede: a OS de medição cai direto no portal,
no formato certo, sem leitura de texto — produto do catálogo vira agente pela
tabela fixa e a quantidade vem como veio.
"""
import json

import pytest

from app import app
from controle.catalogo_medicao import CATALOGO_MEDICAO, resolver_produto
from controle.db import get_db, init_db, row_to_dict
from controle.routes import _buscar_metodos_agente

SECRET = 'osmed-secret-teste'
H = {'x-crm-secret': SECRET}


def _payload(origem='neg-osmed-1', **mudar):
    p = {
        'origem': 'crm', 'origem_id': origem, 'numero_os': 'MED-061026-001',
        'empresa': {'nome': 'OSMED Teste Ltda', 'cnpj': '99.888.777/0001-66'},
        'local': {'unidade': 'Planta Contagem', 'endereco': 'Rua A, 10', 'cidade': 'Contagem', 'uf': 'MG'},
        'contato_local': {'nome': 'Ana', 'telefone': '(31) 99999-0000'},
        'itens': [
            {'produto': 'Ruído', 'quantidade': 10},
            {'produto': 'Álcool Metílico (Metanol)', 'quantidade': 3},
            {'produto': 'BTXE (Benzeno, Tolueno, Xileno e Etilbenzeno)', 'quantidade': 2},
            {'produto': 'Cobre', 'quantidade': 1},
            {'produto': 'Diária profissional para quantificação dos agentes ambientais', 'quantidade': 2},
        ],
    }
    p.update(mudar)
    return p


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv('CRM_PLANNER_SECRET', SECRET)
    init_db()
    yield
    with get_db() as conn:
        ids = [row_to_dict(r)['id'] for r in conn.execute(
            "SELECT id FROM demandas WHERE origem_ref LIKE 'crm:neg-osmed%'").fetchall()]
        for did in ids:
            conn.execute('DELETE FROM medicoes WHERE demanda_id=?', (did,))
            conn.execute('DELETE FROM coletas_ruido WHERE demanda_id=?', (did,))
            conn.execute('DELETE FROM demandas WHERE id=?', (did,))
        conn.execute("DELETE FROM empresas WHERE nome='OSMED Teste Ltda'")


def _post(p):
    with app.test_client() as c:
        return c.post('/controle/os/medicao', headers=H, json=p)


# ── tabela ──────────────────────────────────────────────────────────────
def test_todo_produto_quimico_resolve_metodo_no_guia():
    sem = [p for p, (ag, tipo, sit) in CATALOGO_MEDICAO.items()
           if sit in ('ok', 'confirmar') and tipo in ('quimico', 'particulado') and not _buscar_metodos_agente(ag)]
    assert not sem, f'sem método no guia: {sem}'


def test_os_erros_que_o_motor_de_texto_cometia_nao_existem_na_tabela():
    # o motor juntava os 5 álcoois em "Álcool" e trocava peróxido de MEK por MEK
    metanol = {m['metodoCod'] for m in _buscar_metodos_agente(resolver_produto('Álcool Metílico (Metanol)')['agente'])}
    etanol = {m['metodoCod'] for m in _buscar_metodos_agente(resolver_produto('Álcool Etílico (Etanol)')['agente'])}
    assert metanol and etanol and metanol != etanol
    assert resolver_produto('Peróxido de Metil Etil Cetona')['agente'] != resolver_produto('Metil Etil Cetona')['agente']
    assert 'Benzeno' in resolver_produto('BTXE (Benzeno, Tolueno, Xileno e Etilbenzeno)')['agente']
    assert resolver_produto('Medições Ambientais')['situacao'] == 'generico'
    assert resolver_produto('  ruido ')['agente'] == 'Ruído Ocupacional'


# ── porta de entrada ────────────────────────────────────────────────────
def test_sem_segredo_401():
    with app.test_client() as c:
        assert c.post('/controle/os/medicao', json=_payload()).status_code == 401


def test_teste_true_confere_sem_gravar():
    r = _post(_payload(teste=True))
    assert r.status_code == 200
    d = r.get_json()
    assert d['teste'] and d['dias_campo'] == 2
    assert [i['quantidade'] for i in d['itens']] == [10, 3, 2, 1]
    assert any('Cobre' in a for a in d['avisos'])
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM demandas WHERE origem_ref='crm:neg-osmed-1'").fetchone()['c'] == 0


def test_fora_do_padrao_volta_400_com_os_campos():
    p = _payload(contato_local={}, local={'cidade': 'Contagem'})
    p['itens'].append({'produto': 'Medições Ambientais', 'quantidade': 1})
    p['itens'].append({'produto': 'Produto Que Não Existe', 'quantidade': 1})
    p['itens'].append({'produto': 'Chumbo', 'quantidade': 0})
    r = _post(p)
    assert r.status_code == 400
    campos = {e['campo'] for e in r.get_json()['campos']}
    assert {'contato_local', 'local'} <= campos
    msgs = ' '.join(e['mensagem'] for e in r.get_json()['campos'])
    assert 'Medições Ambientais' in msgs and 'Produto Que Não Existe' in msgs and 'Chumbo' in msgs


def test_cria_demanda_com_agentes_e_quantidades_exatas():
    r = _post(_payload())
    assert r.status_code == 201, r.get_json()
    did = r.get_json()['demanda_id']
    with get_db() as conn:
        d = row_to_dict(conn.execute('SELECT * FROM demandas WHERE id=?', (did,)).fetchone())
        meds = [row_to_dict(x) for x in conn.execute(
            'SELECT agente, qtd_pontos_prevista FROM medicoes WHERE demanda_id=? ORDER BY id', (did,)).fetchall()]
    assert d['origem'] == 'crm_os' and d['numero_os'] == 'MED-061026-001'
    assert 'Contagem/MG' in d['descricao'] and 'Ana' in d['descricao']
    assert [m['qtd_pontos_prevista'] for m in meds] == [10, 3, 2, 1]
    ags = json.loads(d['agentes_manual'])
    assert [a['qtd'] for a in ags] == [10, 3, 2, 1] and ags[0]['tipo'] == 'ruido'
    # a tela de agentes da demanda lê exatamente isso (sem passar pelo motor de texto)
    from controle.routes import get_demanda_agentes
    with app.test_request_context(f'/controle/demandas/{did}/agentes'):
        tela = get_demanda_agentes(did).get_json()
    lista = tela.get('agentes', tela) if isinstance(tela, dict) else tela
    assert [(a['texto'], a['qtd']) for a in lista][:2] == [('Ruído Ocupacional', 10), (ags[1]['texto'], 3)]


def test_reenviar_atualiza_e_coleta_trava():
    did = _post(_payload()).get_json()['demanda_id']
    p = _payload()
    p['itens'] = [{'produto': 'Ruído', 'quantidade': 4}]
    r = _post(p)
    assert r.status_code == 200 and r.get_json()['criada'] is False and r.get_json()['demanda_id'] == did
    with get_db() as conn:
        assert [row_to_dict(x)['qtd_pontos_prevista'] for x in conn.execute(
            'SELECT qtd_pontos_prevista FROM medicoes WHERE demanda_id=?', (did,)).fetchall()] == [4]
        conn.execute("INSERT INTO coletas_ruido (demanda_id, empresa_nome, data_coleta, status) "
                     "VALUES (?, 'OSMED Teste Ltda', '2026-10-06', 'concluida')", (did,))
    r = _post(_payload())
    assert r.status_code == 409


def test_situacao_para_o_crm():
    did = _post(_payload()).get_json()['demanda_id']
    with app.test_client() as c:
        s = c.get('/controle/os/medicao/situacao?origem_id=neg-osmed-1', headers=H).get_json()
        assert s['encontrada'] and s['demanda_id'] == did and s['etapa'] == 'recebida'
        assert c.get('/controle/os/medicao/situacao?origem_id=nao-existe', headers=H).get_json() == {'encontrada': False}


def test_avisa_quando_falta_tubo_no_estoque():
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE tipo IN ('DNPH','FMD') AND status='disponivel'")
    p = _payload(teste=True)
    p['itens'] = [{'produto': 'Formaldeído', 'quantidade': 6}]
    d = _post(p).get_json()
    aviso = [a for a in d['avisos'] if a.startswith('Estoque:')]
    assert aviso and 'Formaldeído' in aviso[0] and 'DNPH' in aviso[0] and 'há 0' in aviso[0]


def test_mesma_os_ja_aberta_pelo_planner_vai_para_revisao():
    with get_db() as conn:
        conn.execute("DELETE FROM demandas WHERE numero_os='7654321'")
        eid = conn.execute("SELECT id FROM empresas ORDER BY id LIMIT 1").fetchone()['id']
        pid = conn.execute("INSERT INTO demandas (numero_os, empresa_id, status, origem) "
                           "VALUES ('7654321', ?, 'em_andamento', 'planner')", (eid,)).lastrowid
    try:
        r = _post(_payload(origem='neg-osmed-dup', numero_maestro='7654321'))
        d = r.get_json()
        assert r.status_code == 201, d
        assert any('7654321' in a and f'#{pid}' in a for a in d['avisos'])
        with get_db() as conn:
            nr = conn.execute('SELECT needs_review FROM demandas WHERE id=?', (d['demanda_id'],)).fetchone()['needs_review']
        assert int(nr) == 1
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM demandas WHERE numero_os='7654321'")
