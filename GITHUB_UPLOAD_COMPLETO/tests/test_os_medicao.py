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
    with get_db() as conn:
        ultima_empresa = conn.execute('SELECT COALESCE(MAX(id), 0) AS m FROM empresas').fetchone()['m']
    yield
    with get_db() as conn:
        ids = [row_to_dict(r)['id'] for r in conn.execute(
            "SELECT id FROM demandas WHERE origem_ref LIKE 'crm:neg-osmed%'").fetchall()]
        for did in ids:
            conn.execute('DELETE FROM medicoes WHERE demanda_id=?', (did,))
            conn.execute('DELETE FROM coletas_ruido WHERE demanda_id=?', (did,))
            conn.execute('DELETE FROM demandas WHERE id=?', (did,))
        conn.execute("DELETE FROM empresas WHERE nome='OSMED Teste Ltda'")
        # as empresas que o teste (ou a OS dele) criou
        conn.execute('DELETE FROM empresas WHERE id > ?', (ultima_empresa,))


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


# ── casamento da empresa (fix 19/10/2026) ───────────────────────────────
# A OS MED-191026-001 ("MINERAL DO BRASIL LTDA", com CNPJ) criou uma empresa
# nova em vez de casar com "Mineral do Brasil", que veio do Planner sem CNPJ:
# a mesma cliente em duas linhas de Demandas / OS e o histórico partido.
MINERAL = {'nome': 'MINERAL DO BRASIL LTDA', 'cnpj': '17246638000290'}


def _empresa(nome, cnpj=None):
    with get_db() as conn:
        return conn.execute('INSERT INTO empresas (nome, cnpj) VALUES (?,?)', (nome, cnpj)).lastrowid


def _casamento(resp):
    assert resp.status_code == 201, resp.get_json()
    with get_db() as conn:
        return row_to_dict(conn.execute(
            'SELECT empresa_id, empresa_match_metodo, needs_review FROM demandas WHERE id=?',
            (resp.get_json()['demanda_id'],)).fetchone())


def _cnpj_da(empresa_id):
    with get_db() as conn:
        return conn.execute('SELECT cnpj FROM empresas WHERE id=?', (empresa_id,)).fetchone()['cnpj']


def test_razao_social_do_crm_casa_com_a_empresa_antiga_sem_cnpj():
    # a planilha seed traz "Mineral do Brasil" sem CNPJ, como em produção
    with get_db() as conn:
        r = conn.execute("SELECT id FROM empresas WHERE nome='Mineral do Brasil' AND cnpj IS NULL").fetchone()
    antiga = r['id'] if r else _empresa('Mineral do Brasil')
    try:
        d = _casamento(_post(_payload(origem='neg-osmed-mineral', numero_os='MED-191026-001',
                                      empresa=dict(MINERAL))))
        assert (d['empresa_id'], d['empresa_match_metodo']) == (antiga, 'nome_sem_cnpj')
        assert int(d['needs_review']) == 0
        with get_db() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM empresas "
                             "WHERE nome IN ('Mineral do Brasil', 'MINERAL DO BRASIL LTDA')").fetchone()['c']
        assert n == 1                                   # não nasceu a segunda Mineral
        assert _cnpj_da(antiga) == MINERAL['cnpj']      # a antiga ganhou o CNPJ...
        d2 = _casamento(_post(_payload(origem='neg-osmed-mineral-2', empresa=dict(MINERAL))))
        assert (d2['empresa_id'], d2['empresa_match_metodo']) == (antiga, 'cnpj')   # ...e a próxima casa por ele
    finally:
        with get_db() as conn:
            conn.execute('UPDATE empresas SET cnpj=NULL WHERE id=?', (antiga,))


def test_duas_gemeas_sem_cnpj_fica_na_mais_antiga_e_pede_revisao():
    mais_antiga = _empresa('Fundição Gêmea Teste')
    _empresa('Fundicao Gemea Teste Ltda')
    d = _casamento(_post(_payload(origem='neg-osmed-gemeas',
                                  empresa={'nome': 'FUNDIÇÃO GÊMEA TESTE LTDA', 'cnpj': '12345678000199'})))
    assert (d['empresa_id'], d['empresa_match_metodo']) == (mais_antiga, 'nome_sem_cnpj_ambiguo')
    assert int(d['needs_review']) == 1
    assert not _cnpj_da(mais_antiga)                # qual ficha fica com o CNPJ é de quem revisa


def test_outro_estabelecimento_nao_casa_por_nome():
    _empresa('Posto Bandeira 1')                               # o número distingue
    matriz = _empresa('Acme Filtros', '11111111000111')        # já tem outro CNPJ
    d = _casamento(_post(_payload(origem='neg-osmed-posto',
                                  empresa={'nome': 'POSTO BANDEIRA 2 LTDA', 'cnpj': '22222222000122'})))
    assert d['empresa_match_metodo'] == 'criada'
    d = _casamento(_post(_payload(origem='neg-osmed-acme',
                                  empresa={'nome': 'ACME FILTROS LTDA', 'cnpj': '11111111000292'})))
    assert d['empresa_match_metodo'] == 'criada' and d['empresa_id'] != matriz
    assert _cnpj_da(matriz) == '11111111000111'


def test_cnpj_pontuado_no_cadastro_casa_com_o_cnpj_so_digitos_do_crm():
    eid = _empresa('Arames Teste Ltda', '61.000.111/0001-22')
    d = _casamento(_post(_payload(origem='neg-osmed-arames',
                                  empresa={'nome': 'ARAMES TESTE LTDA', 'cnpj': '61000111000122'})))
    assert (d['empresa_id'], d['empresa_match_metodo']) == (eid, 'cnpj')
