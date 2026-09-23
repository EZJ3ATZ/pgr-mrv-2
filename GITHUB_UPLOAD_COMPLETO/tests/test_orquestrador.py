# -*- coding: utf-8 -*-
"""Orquestrador da OS (substitui o MAESTRO) — v1.

Cobre os requisitos aprovados pelo Bernardo (22/07): nº de OS lógico com data,
fan-out por raia com SLA, medição direto na tabela demandas (sem Planner),
onboarding só p/ cliente novo >300 vidas, credenciamento = e-mail, aprovação
(Valéria/Luiz) cria a task no Planner e marca opcional a linha do BI.
Graph mockado — nada toca o Planner real; e-mails ficam pendente_envio
(ORQ_ENVIAR_EMAILS desligado nos testes)."""
import re
import json

import pytest

import controle.graph as graph_mod
import controle.orquestrador as orq
from controle.db import get_db, init_db, row_to_dict
from app import app

SECRET = 'orq-secret-teste'

PAYLOAD = {
    'empresa': 'ORQ Teste Ltda', 'cnpj': '11.222.333/0001-44',
    'vidas': 450, 'cliente_novo': True, 'consultor': 'Jéssica',
    'contato_nome': 'Ana', 'contato_email': 'ana@cliente.com', 'contato_tel': '(31) 99999-0000',
    'negocio_crm_id': 'neg-1', 'vencimento': '05/08/2026', 'parcelamento': '2X',
    'envolve_credenciamento': True,
    'servicos': [
        {'nome': 'PGR', 'categoria': 'engenharia', 'valor': 844, 'quantidade': 1},
        {'nome': 'Ruído', 'categoria': 'medicao', 'valor': 300, 'quantidade': 2},
        {'nome': 'AET', 'valor': 500},
        {'nome': 'Treinamento NR-35', 'valor': 200},
    ],
}


@pytest.fixture(autouse=True)
def _orq_ativo_por_padrao(monkeypatch):
    # A maioria dos testes valida o comportamento ATIVO do orquestrador.
    # O interruptor mestre nasce DESLIGADO em produção; aqui ligamos por padrão
    # e os testes de modo dormente sobrescrevem com ORQ_ATIVO=0.
    monkeypatch.setenv('ORQ_ATIVO', '1')


def _limpar():
    init_db()
    with get_db() as conn:
        orq._ensure_schema(conn)
        conn.execute("DELETE FROM os_raias")
        conn.execute("DELETE FROM os_ordens")
        conn.execute("DELETE FROM demandas WHERE origem='crm_os'")


def test_classificacao_por_keyword():
    assert orq.classificar_servico({'nome': 'AET'}) == 'ergonomia'
    assert orq.classificar_servico({'nome': 'Treinamento NR-35'}) == 'treinamento'
    assert orq.classificar_servico({'nome': 'LTCAT'}) == 'engenharia'
    assert orq.classificar_servico({'nome': 'Dosimetria de ruído'}) == 'medicao'


def test_dry_run_fanout_completo():
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=True)
    assert r['ok'] and r['dry_run']
    assert re.fullmatch(r'\d{4}\.\d{4}-\d{3}', r['numero'])   # 2026.0722-001
    raias = {x['raia'] for x in r['raias']}
    assert raias == {'cobranca', 'medicao', 'engenharia', 'ergonomia',
                     'treinamento', 'credenciamento', 'onboarding'}
    cob = next(x for x in r['raias'] if x['raia'] == 'cobranca')
    corpo = cob['detalhe']['email']['corpo']
    assert 'SERVIÇOS | VALOR | VENCIMENTO | O.S | PARCELAMENTO' in corpo
    assert r['numero'] in corpo and '2X' in corpo


def test_onboarding_so_acima_de_300_vidas():
    _limpar()
    p = dict(PAYLOAD); p['vidas'] = 120
    raias = {x['raia'] for x in orq.abrir_os(p, dry_run=True)['raias']}
    assert 'onboarding' not in raias
    p = dict(PAYLOAD); p['cliente_novo'] = False
    raias = {x['raia'] for x in orq.abrir_os(p, dry_run=True)['raias']}
    assert 'onboarding' not in raias


def test_abrir_os_real_cria_demanda_de_medicao():
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    assert r['ok'] and not r['dry_run']
    med = next(x for x in r['raias'] if x['raia'] == 'medicao')
    assert med['demanda_id']
    with get_db() as conn:
        d = row_to_dict(conn.execute(
            "SELECT * FROM demandas WHERE id=?", (med['demanda_id'],)).fetchone())
        assert d['numero_os'] == r['numero'] and d['origem'] == 'crm_os'
        assert 'Ruído' in (d['descricao'] or '')
        cob = row_to_dict(conn.execute(
            "SELECT status FROM os_raias WHERE raia='cobranca'").fetchone())
        assert cob['status'] == 'pendente_envio'   # envio desligado nos testes


def _mock_graph(monkeypatch):
    monkeypatch.setattr(graph_mod, 'graph_ok', lambda: True)
    monkeypatch.setattr(graph_mod, 'criar_planner_task',
                        lambda *a, **k: {'id': 'TASK-MOCK'})
    monkeypatch.setattr(graph_mod, 'set_task_description', lambda *a, **k: True)
    monkeypatch.setattr(graph_mod, 'get_category_ids_by_names',
                        lambda pid, nomes: {'category6': True})
    monkeypatch.setattr(graph_mod, 'get_bucket_id_by_name',
                        lambda *a, **k: 'BK-ENG')
    monkeypatch.setattr(graph_mod, 'get_plan_id_by_title', lambda *a, **k: 'PL-ERG')


def test_aprovacao_cria_task_e_marca_bi(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    with get_db() as conn:
        eng = row_to_dict(conn.execute(
            "SELECT id FROM os_raias WHERE raia='engenharia'").fetchone())
    resp, code = orq.aprovar_raia(r['numero'], eng['id'], 'Evelyn Duarte',
                                  'Luiz Fernando', criar_linha_bi=True)
    assert code == 200 and resp['ok']
    assert resp['planner_task_id'] == 'TASK-MOCK' and resp['bi_linha_pendente']
    with get_db() as conn:
        ra = row_to_dict(conn.execute(
            "SELECT * FROM os_raias WHERE id=?", (eng['id'],)).fetchone())
        assert ra['status'] == 'em_andamento'
        assert ra['tecnico_definido'] == 'Evelyn Duarte'
        assert json.loads(ra['detalhe_json'])['criar_linha_bi'] is True
    # aprovar de novo → 409 (não está mais aguardando)
    resp2, code2 = orq.aprovar_raia(r['numero'], eng['id'], 'X', 'Y')
    assert code2 == 409


def test_concluir_todas_fecha_a_os(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    p = {'empresa': 'Mini Ltda', 'servicos': [{'nome': 'PGR'}]}
    r = orq.abrir_os(p, dry_run=False)
    with get_db() as conn:
        ids = [row_to_dict(x)['id'] for x in conn.execute(
            "SELECT id FROM os_raias ORDER BY id").fetchall()]
    for i, rid in enumerate(ids):
        resp, code = orq.concluir_raia(r['numero'], rid)
        assert code == 200
    assert resp['os_concluida'] is True
    with get_db() as conn:
        o = row_to_dict(conn.execute(
            "SELECT status FROM os_ordens WHERE numero=?", (r['numero'],)).fetchone())
        assert o['status'] == 'concluida'


def test_rota_abrir_exige_secret(monkeypatch):
    _limpar()
    monkeypatch.setenv('CRM_PLANNER_SECRET', SECRET)
    c = app.test_client()
    r = c.post('/controle/os/abrir', json={'empresa': 'X', 'dry_run': True})
    assert r.status_code == 401
    r = c.post('/controle/os/abrir', headers={'x-crm-secret': SECRET},
               json={'empresa': 'X', 'servicos': [], 'dry_run': True})
    assert r.status_code == 200 and r.get_json()['ok']


def test_painel_sla(monkeypatch):
    _limpar()
    orq.abrir_os({'empresa': 'SLA Ltda', 'servicos': [{'nome': 'PGR'}]}, dry_run=False)
    p = orq.painel()
    assert p['ok'] and p['ordens']
    raia = p['ordens'][0]['raias'][0]
    assert raia['horas_decorridas'] is not None and raia['horas_decorridas'] >= 0


def test_painel_filtro_por_negocio():
    # Rastreio do vendedor (pedido da M. Fernanda): o CRM consulta o painel
    # por negocio_crm_id e recebe SÓ as OS daquele negócio, sem LIMIT.
    _limpar()
    orq.abrir_os({'empresa': 'A Ltda', 'negocio_crm_id': '123',
                  'servicos': [{'nome': 'PGR'}]}, dry_run=False)
    orq.abrir_os({'empresa': 'B Ltda', 'negocio_crm_id': '456',
                  'servicos': [{'nome': 'Ruído'}]}, dry_run=False)
    p = orq.painel(negocio='123')
    assert p['ok'] and len(p['ordens']) == 1
    assert p['ordens'][0]['empresa'] == 'A Ltda'
    assert p['ordens'][0]['raias']          # raias vêm junto p/ montar o status
    assert orq.painel(negocio='999')['ordens'] == []
    # int também vale (o CRM manda number)
    assert len(orq.painel(negocio=456)['ordens']) == 1


# ── Interruptor mestre ORQ_ATIVO (CRM não está em uso: dormente por padrão) ──
def test_dormente_nao_grava_nada(monkeypatch):
    """ORQ_ATIVO=0 ⇒ abrir_os vira preview mesmo com dry_run=False:
    nada entra em os_ordens nem em demandas."""
    _limpar()
    monkeypatch.setenv('ORQ_ATIVO', '0')
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    assert r['ok'] and r['dry_run'] and r['dormente'] is True
    assert re.fullmatch(r'\d{4}\.\d{4}-\d{3}', r['numero'])
    with get_db() as conn:
        n_os = row_to_dict(conn.execute(
            "SELECT COUNT(*) AS c FROM os_ordens").fetchone())['c']
        n_dem = row_to_dict(conn.execute(
            "SELECT COUNT(*) AS c FROM demandas WHERE origem='crm_os'").fetchone())['c']
    assert n_os == 0 and n_dem == 0


def test_dormente_bloqueia_aprovacao(monkeypatch):
    """Abre OS real (ativo), depois adormece: aprovar não toca o Planner
    nem muda o status da raia."""
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)          # ativo (fixture)
    with get_db() as conn:
        eng = row_to_dict(conn.execute(
            "SELECT id FROM os_raias WHERE raia='engenharia'").fetchone())
    chamadas = []
    def _spy(*a, **k):
        chamadas.append((a, k))
        return {'id': 'NAO-DEVIA'}
    monkeypatch.setattr(graph_mod, 'criar_planner_task', _spy)
    monkeypatch.setenv('ORQ_ATIVO', '0')
    resp, code = orq.aprovar_raia(r['numero'], eng['id'], 'Evelyn', 'Luiz')
    assert code == 200 and resp.get('dormente') is True
    assert not chamadas                                     # Planner não foi chamado
    with get_db() as conn:
        ra = row_to_dict(conn.execute(
            "SELECT status, planner_task_id FROM os_raias WHERE id=?",
            (eng['id'],)).fetchone())
    assert ra['status'] == 'aguardando_aprovacao' and not ra['planner_task_id']


def test_dormente_bloqueia_conclusao(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os({'empresa': 'Dorm Ltda', 'servicos': [{'nome': 'PGR'}]},
                     dry_run=False)
    with get_db() as conn:
        rid = row_to_dict(conn.execute(
            "SELECT id FROM os_raias LIMIT 1").fetchone())['id']
    monkeypatch.setenv('ORQ_ATIVO', '0')
    resp, code = orq.concluir_raia(r['numero'], rid)
    assert code == 200 and resp.get('dormente') is True
    with get_db() as conn:
        ra = row_to_dict(conn.execute(
            "SELECT status FROM os_raias WHERE id=?", (rid,)).fetchone())
    assert ra['status'] != 'concluida'


# ── Fila de aprovação (backend consumido pela UI do Assinador) ──────────────
def test_fila_aprovacao_lista_raias_pendentes():
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)          # ativo (fixture)
    fila = orq.fila_aprovacao()
    assert fila['ok'] and fila['total'] >= 3
    tipos = {x['raia'] for x in fila['fila']}
    assert {'engenharia', 'ergonomia', 'treinamento'} <= tipos
    eng = next(x for x in fila['fila'] if x['raia'] == 'engenharia')
    assert eng['numero'] == r['numero'] and eng['raia_id']
    assert eng['empresa'] == 'ORQ Teste Ltda'
    assert any('PGR' in (i.get('nome', '')) for i in eng['itens'])
    ergo = next(x for x in fila['fila'] if x['raia'] == 'ergonomia')
    assert ergo.get('modalidade')                            # interna/externa
    # filtro por tipo
    so_eng = orq.fila_aprovacao(raias=['engenharia'])
    assert {x['raia'] for x in so_eng['fila']} == {'engenharia'}


def test_fila_some_apos_aprovar(monkeypatch):
    _limpar()
    _mock_graph(monkeypatch)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    eng = next(x for x in orq.fila_aprovacao(raias=['engenharia'])['fila'])
    orq.aprovar_raia(r['numero'], eng['raia_id'], 'Evelyn', 'Luiz')
    assert orq.fila_aprovacao(raias=['engenharia'])['total'] == 0


def test_fila_vazia_quando_dormente(monkeypatch):
    _limpar()
    monkeypatch.setenv('ORQ_ATIVO', '0')
    orq.abrir_os(dict(PAYLOAD), dry_run=False)               # preview, não grava
    assert orq.fila_aprovacao()['total'] == 0


# ── Procedência do match de empresa (fix 01/09) ─────────────────────────
# Antes, `_criar_demanda_medicao` casava a empresa SÓ pelo CNPJ e não marcava
# nada: uma OS com o CNPJ trocado entrava calada na ficha de outro cliente.

def _empresa_fixa(cnpj, nome):
    with get_db() as conn:
        conn.execute("DELETE FROM empresas WHERE cnpj=?", (cnpj,))
        conn.execute("INSERT INTO empresas (cnpj, nome) VALUES (?,?)", (cnpj, nome))
        return row_to_dict(conn.execute(
            "SELECT id FROM empresas WHERE cnpj=?", (cnpj,)).fetchone())['id']


def _demanda_da_os(resp):
    med = next(x for x in resp['raias'] if x['raia'] == 'medicao')
    with get_db() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM demandas WHERE id=?", (med['demanda_id'],)).fetchone())


def test_cnpj_casa_com_nome_diferente_marca_revisao():
    _limpar()
    eid = _empresa_fixa(PAYLOAD['cnpj'], 'BANCO DO BRASIL SA')
    d = _demanda_da_os(orq.abrir_os(dict(PAYLOAD), dry_run=False))
    assert d['empresa_id'] == eid                      # o CNPJ ainda manda
    assert d['needs_review'] == 1                      # ...mas não entra calado
    assert d['empresa_match_metodo'] == 'cnpj_nome_divergente'
    assert 0 <= d['empresa_match_score'] < orq.MATCH_NOME_MIN


def test_cnpj_com_o_mesmo_nome_nao_pede_revisao():
    _limpar()
    _empresa_fixa(PAYLOAD['cnpj'], PAYLOAD['empresa'])
    d = _demanda_da_os(orq.abrir_os(dict(PAYLOAD), dry_run=False))
    assert d['needs_review'] == 0
    assert d['empresa_match_metodo'] == 'cnpj'
    assert d['empresa_match_score'] == 1.0


def test_empresa_nova_registra_metodo_criada():
    _limpar()
    p = dict(PAYLOAD)
    p['cnpj'] = '99.888.777/0001-66'
    p['empresa'] = 'Empresa Inexistente Para Teste Ltda'
    with get_db() as conn:
        conn.execute("DELETE FROM empresas WHERE cnpj=?", (p['cnpj'],))
    d = _demanda_da_os(orq.abrir_os(p, dry_run=False))
    assert d['needs_review'] == 0
    assert d['empresa_match_metodo'] == 'criada'


def test_sufixo_societario_diferente_nao_vira_revisao():
    """"ORQ Teste Ltda" x "ORQ TESTE LTDA - ME" é a MESMA empresa: normalizar_nome
    derruba sufixo e acento, então não pode encher o banner de falso positivo."""
    _limpar()
    _empresa_fixa(PAYLOAD['cnpj'], 'ORQ TESTE LTDA - ME')
    d = _demanda_da_os(orq.abrir_os(dict(PAYLOAD), dry_run=False))
    assert d['needs_review'] == 0
    assert d['empresa_match_metodo'] == 'cnpj'


# ── Técnico sugerido e prazo (fix 02/09) ────────────────────────────────
# Antes: sugerir_tecnico lia carga de os_raias (tabela que nasce vazia) e
# devolvia None para sempre; a demanda nascia sem prazo nenhum.

def _tecnicos_teste(nomes):
    with get_db() as conn:
        conn.execute("DELETE FROM usuarios WHERE email LIKE 'tec-teste-%'")
        for i, n in enumerate(nomes):
            conn.execute(
                "INSERT INTO usuarios (nome, email, senha_hash, role, ativo)"
                " VALUES (?,?,?, 'tecnico', 1)",
                (n, f'tec-teste-{i}@x.com', 'x'))


def test_sugere_tecnico_de_medicao_pelo_cadastro_do_portal():
    _limpar()
    _tecnicos_teste(['Zulmira Teste', 'Abel Teste'])
    eid = _empresa_fixa('55.444.333/0001-22', 'Empresa da Carga')
    with get_db() as conn:
        # Abel já tem uma demanda aberta; Zulmira não tem nenhuma
        conn.execute("INSERT INTO demandas (titulo, empresa_id, status, origem,"
                     " responsavel) VALUES ('carga', ?, 'pendente', 'crm_os',"
                     " 'Abel Teste')", (eid,))
        assert orq.sugerir_tecnico(conn, 'medicao') == 'Zulmira Teste'


def test_sugere_engenharia_pelo_roster_do_assinador(monkeypatch):
    _limpar()
    monkeypatch.setattr(orq, '_roster_engenharia',
                        lambda: ['Bianca Roster', 'Ariel Roster'])
    with get_db() as conn:
        r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
        eng = next(x for x in r['raias'] if x['raia'] == 'engenharia')
        # empate de carga (ninguém tem raia aberta) → desempata por nome
        assert eng['tecnico_sugerido'] == 'Ariel Roster'


def test_roster_fora_do_ar_nao_derruba_a_abertura(monkeypatch):
    """Se o Assinador não responder, a raia fica sem sugestão — mas a OS abre."""
    _limpar()
    monkeypatch.setattr(orq, '_roster_engenharia', lambda: [])
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    assert r['ok']
    eng = next(x for x in r['raias'] if x['raia'] == 'engenharia')
    assert eng['tecnico_sugerido'] is None


def test_prazo_do_crm_desce_para_a_demanda_em_iso():
    """A consultora digita data BR; a coluna é TEXT e consultas fazem ::date,
    então tem que ser gravada em ISO (ver o 500 do dashboard de 26/08)."""
    _limpar()
    p = dict(PAYLOAD)
    p['prazo'] = '17/07/2026'
    d = _demanda_da_os(orq.abrir_os(p, dry_run=False))
    assert d['prazo'] == '2026-07-17'


def test_sem_prazo_a_demanda_nasce_sem_data():
    _limpar()
    d = _demanda_da_os(orq.abrir_os(dict(PAYLOAD), dry_run=False))
    assert d['prazo'] in (None, '')


def test_prazo_invalido_nao_grava_lixo():
    _limpar()
    p = dict(PAYLOAD)
    p['prazo'] = 'quando der'
    d = _demanda_da_os(orq.abrir_os(p, dry_run=False))
    assert d['prazo'] in (None, '')


def test_roster_descarta_cargo_de_gestao(monkeypatch):
    """Coordenadora/Supervisor/Diretor não executam demanda — mesma régua que o
    Painel de Entregas Técnicas já usa para tirar gestor da disputa."""
    import io as _io
    import json as _j
    import urllib.request as _u

    payload = {'tecnicos': [
        {'nome': 'Fabiana Ferreira', 'cargo': 'Coordenadora de Segurança do Trabalho'},
        {'nome': 'Valeria de Jesus', 'cargo': 'Supervisora de Segurança do Trabalho'},
        {'nome': 'Bernardo Junqueira', 'cargo': 'Diretor de Operações'},
        {'nome': 'Tainara Gomes', 'cargo': 'Técnico (a) em Segurança do Trabalho  IV'},
        {'nome': 'Mauro Malta', 'cargo': 'Engenheiro em Segurança do Trabalho'},
        {'nome': '   ', 'cargo': 'Técnico (a) em Segurança do Trabalho'},
    ]}

    class _Resp(_io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setenv('CRM_PLANNER_SECRET', 'x')
    monkeypatch.setattr(_u, 'urlopen',
                        lambda *a, **k: _Resp(_j.dumps(payload).encode('utf-8')))
    orq._roster_cache['ate'] = 0          # ignora cache de chamadas anteriores
    assert orq._roster_engenharia() == ['Tainara Gomes', 'Mauro Malta']
    orq._roster_cache['ate'] = 0


def test_roster_sem_segredo_nao_chama_o_assinador(monkeypatch):
    monkeypatch.delenv('CRM_PLANNER_SECRET', raising=False)
    orq._roster_cache['ate'] = 0
    assert orq._roster_engenharia() == []
    orq._roster_cache['ate'] = 0


def test_coordenacao_nao_entra_no_rodizio_de_medicao():
    """Quem coordena aprova, não executa: role='coordenacao' fica fora da
    sugestão mesmo com carga zero (o Luiz Fernando estava como técnico)."""
    _limpar()
    _tecnicos_teste(['Wanda Teste'])
    with get_db() as conn:
        conn.execute("DELETE FROM usuarios WHERE email='coord-teste@x.com'")
        conn.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, role, ativo)"
            " VALUES ('Ana Coordenadora', 'coord-teste@x.com', 'x', 'coordenacao', 1)")
        assert orq.sugerir_tecnico(conn, 'medicao') == 'Wanda Teste'


def test_email_cobranca_vencimento_em_dd_mm_aaaa():
    """O modal do CRM manda o vencimento em ISO (input type=date); o template do
    financeiro, capturado das caixas reais, usa dd/mm/aaaa. Medido em 09/09/2026:
    o corpo saía com '2026-10-10'."""
    _limpar()
    p = dict(PAYLOAD); p['vencimento'] = '2026-10-10'
    r = orq.abrir_os(p, dry_run=True)
    corpo = next(x for x in r['raias'] if x['raia'] == 'cobranca')['detalhe']['email']['corpo']
    assert '| 10/10/2026 |' in corpo
    assert '2026-10-10' not in corpo
    # quem já mandava em BR continua igual
    r2 = orq.abrir_os(dict(PAYLOAD), dry_run=True)
    corpo2 = next(x for x in r2['raias'] if x['raia'] == 'cobranca')['detalhe']['email']['corpo']
    assert '| 05/08/2026 |' in corpo2
    # vazio não vira lixo
    p3 = dict(PAYLOAD); p3['vencimento'] = ''
    r3 = orq.abrir_os(p3, dry_run=True)
    corpo3 = next(x for x in r3['raias'] if x['raia'] == 'cobranca')['detalhe']['email']['corpo']
    assert '| - |' in corpo3


def test_vencimento_gravado_em_iso_na_os():
    """A coluna é TEXT e outras consultas castam com ::date: BR cru gravado já
    derrubou dashboard (ver data_iso). Entra BR ou ISO, grava ISO."""
    _limpar()
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)          # PAYLOAD manda '05/08/2026'
    with get_db() as conn:
        v = row_to_dict(conn.execute(
            "SELECT vencimento FROM os_ordens WHERE numero=?", (r['numero'],)).fetchone())['vencimento']
    assert v == '2026-08-05'


# ── Prazo e responsável decididos por quem aprova na fila 🚦 ─────────────
# Medido em 23/09/2026 nas 200 tasks mais recentes do bucket da engenharia:
# 100% têm responsável, 96% têm prazo, 98% têm a etiqueta "Demanda nova".
# Sem esses campos a task entra no painel deles sem classificação, sem dono e
# fora da conta de atraso/OTD — por isso os três viraram parte da aprovação.

def _mock_graph_capturando(monkeypatch, recebido, email_resolve=True):
    """Igual ao _mock_graph, mas guarda o que foi mandado para o Planner."""
    def _criar(plan_id, title, **kw):
        recebido.update({'plan_id': plan_id, 'title': title, **kw})
        return {'id': 'TASK-MOCK'}
    monkeypatch.setattr(graph_mod, 'graph_ok', lambda: True)
    monkeypatch.setattr(graph_mod, 'criar_planner_task', _criar)
    monkeypatch.setattr(graph_mod, 'set_task_description', lambda *a, **k: True)
    monkeypatch.setattr(graph_mod, 'get_category_ids_by_names',
                        lambda pid, nomes: {'_nomes': list(nomes)})
    monkeypatch.setattr(graph_mod, 'get_bucket_id_by_name', lambda *a, **k: 'BK-ENG')
    monkeypatch.setattr(graph_mod, 'get_plan_id_by_title', lambda *a, **k: 'PL-ERG')
    monkeypatch.setattr(
        graph_mod, 'assignments_para',
        (lambda email: {'AAD-ID': {'@odata.type': '#microsoft.graph.plannerAssignment',
                                   'orderHint': ' !'}}) if email_resolve else (lambda email: None))


def _raia(nome):
    with get_db() as conn:
        return row_to_dict(conn.execute(
            "SELECT id FROM os_raias WHERE raia=?", (nome,)).fetchone())['id']


def test_aprovar_manda_prazo_responsavel_e_demanda_nova(monkeypatch):
    _limpar()
    got = {}
    _mock_graph_capturando(monkeypatch, got)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    resp, code = orq.aprovar_raia(r['numero'], _raia('engenharia'), 'Evelyn Duarte',
                                  'Luiz Fernando', prazo='2026-10-15',
                                  tecnico_email='engenharia13@ocupacional.com.br')
    assert code == 200 and resp['ok']
    # meio-dia UTC: 00:00Z apareceria como o dia anterior à tarde no Brasil
    assert got['due_date_time'] == '2026-10-15T12:00:00Z'
    assert list(got['assignments'].keys()) == ['AAD-ID']
    assert orq.LABEL_DEMANDA_NOVA in got['applied_categories']['_nomes']
    assert resp['prazo'] == '2026-10-15' and resp['sem_responsavel'] is False


def test_aprovar_aceita_prazo_em_formato_br(monkeypatch):
    _limpar()
    got = {}
    _mock_graph_capturando(monkeypatch, got)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    orq.aprovar_raia(r['numero'], _raia('treinamento'), 'Evelyn Duarte', 'Luiz',
                     prazo='15/10/2026', tecnico_email='x@ocupacional.com.br')
    assert got['due_date_time'] == '2026-10-15T12:00:00Z'
    assert orq.LABEL_DEMANDA_NOVA in got['applied_categories']['_nomes']


def test_aprovar_sem_prazo_e_sem_email_continua_valendo(monkeypatch):
    """Chamada antiga (sem os campos novos) não pode quebrar: a task nasce sem
    prazo e sem responsável, exatamente como era antes."""
    _limpar()
    got = {}
    _mock_graph_capturando(monkeypatch, got)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    resp, code = orq.aprovar_raia(r['numero'], _raia('engenharia'), 'Evelyn', 'Luiz')
    assert code == 200 and resp['ok']
    assert got.get('due_date_time') is None
    assert got.get('assignments') is None
    assert resp['prazo'] is None and resp['sem_responsavel'] is False


def test_tecnico_sem_conta_no_azure_avisa_quem_aprovou(monkeypatch):
    """Kellen Ferreira está no roster da BI sem e-mail e não existe no tenant
    (conferido 23/09/2026). Escolher alguém assim não pode travar a fila: a
    task nasce sem responsável e a resposta diz isso."""
    _limpar()
    got = {}
    _mock_graph_capturando(monkeypatch, got, email_resolve=False)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    resp, code = orq.aprovar_raia(r['numero'], _raia('engenharia'), 'Kellen Ferreira',
                                  'Luiz', prazo='2026-10-15',
                                  tecnico_email='nao-existe@ocupacional.com.br')
    assert code == 200 and resp['ok']
    assert got.get('assignments') is None
    assert resp['sem_responsavel'] is True
    assert got['due_date_time'] == '2026-10-15T12:00:00Z'


def test_prazo_invalido_nao_derruba_a_aprovacao(monkeypatch):
    _limpar()
    got = {}
    _mock_graph_capturando(monkeypatch, got)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    resp, code = orq.aprovar_raia(r['numero'], _raia('engenharia'), 'Evelyn', 'Luiz',
                                  prazo='data errada', tecnico_email='x@ocupacional.com.br')
    assert code == 200 and resp['ok']
    assert got.get('due_date_time') is None and resp['prazo'] is None


def test_prazo_da_aprovacao_atualiza_a_demanda_de_medicao(monkeypatch):
    """Medição não vai para o Planner — o prazo tem que cair na demanda."""
    _limpar()
    got = {}
    _mock_graph_capturando(monkeypatch, got)
    r = orq.abrir_os(dict(PAYLOAD), dry_run=False)
    orq.aprovar_raia(r['numero'], _raia('medicao'), 'Geferson', 'Luiz',
                     prazo='20/11/2026')
    with get_db() as conn:
        d = row_to_dict(conn.execute(
            "SELECT responsavel, prazo FROM demandas WHERE origem='crm_os'").fetchone())
    assert d['responsavel'] == 'Geferson'
    assert d['prazo'] == '2026-11-20'
