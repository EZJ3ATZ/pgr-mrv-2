# -*- coding: utf-8 -*-
"""A conferência lê o tipo do amostrador do CADASTRO do tubo, não da coleta.

Medido no banco de produção em 08/09/2026: das 31 linhas de
`coletas_quimico_amostr` cujo tubo existe no cadastro, 15 tinham o tipo
divergente. Dois padrões:

  - 10 linhas gravaram 'TCP' (tubo de carvão ativado) em tubo PVC, IOL ou EC.
    Vinha do `nvCqAddAmostr`, que criava a linha com 'TCP' fixo.
  - 5 linhas gravaram o texto inteiro do guia ('SKC 226-01 (TCP*****)') no
    lugar da sigla.

Efeito: a aba "Gerar Cadeia de Custódia" imprimia "amostrador X não é o do
método (Y)" em cima de coleta certa. Rodando a conferência nas 39 coletas
químicas de produção, a reprovação por tipo caía de **13 para 2** quando o tipo
vem do cadastro, e 6 linhas viravam de "fora" para "ok".

A gravação já foi resolvida em 02/09 por `_canonizar_amostradores`, que troca
código E tipo pelo que está no cadastro. O que faltava era a LEITURA: as linhas
gravadas antes disso continuam no banco com o tipo errado, e migrar dado de
produção é decisão do Matheus. Lendo do cadastro, o histórico se corrige sem
migração nenhuma.
"""
import pytest

from app import app
from controle.db import get_db, init_db


@pytest.fixture
def cenario():
    """Semeia tubo + coleta com o tipo ERRADO gravado e limpa no fim.

    O CÓDIGO começa com Z de propósito: código real faz este arquivo brigar
    com test_cadeia_puxa_todas_as_medicoes, que semeia os mesmos ids. O TIPO
    tem de ser o de verdade (PVC, EC), senão o teste compara com a sigla do
    método e reprova por um motivo que não é o que está sendo medido.
    """
    criados = {'amostr': [], 'coleta': [], 'empresa': [], 'demanda': []}

    def _montar(codigo, tipo_cadastro, tipo_na_coleta, substancia='Sílica Cristalina'):
        init_db()
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO amostradores (codigo, tipo, status, arquivado) "
                "VALUES (?, ?, 'disponivel', 0)", (codigo, tipo_cadastro))
            criados['amostr'].append(cur.lastrowid)
            cur = conn.execute("INSERT INTO empresas (nome) VALUES ('ZTIPO TUBO')")
            eid = cur.lastrowid
            criados['empresa'].append(eid)
            cur = conn.execute(
                "INSERT INTO demandas (empresa_id, numero_os, status) "
                "VALUES (?, 'ZOS-TIPO-1', 'em_andamento')", (eid,))
            did = cur.lastrowid
            criados['demanda'].append(did)
            cur = conn.execute(
                "INSERT INTO coletas_quimico "
                "(empresa_id, empresa_nome, demanda_id, data_coleta, substancias, "
                " nome_funcionario, status) "
                "VALUES (?, 'ZTIPO TUBO', ?, '2026-09-08', ?, 'Fulano', 'concluida')",
                (eid, did, substancia))
            cid = cur.lastrowid
            criados['coleta'].append(cid)
            conn.execute(
                "INSERT INTO coletas_quimico_amostr "
                "(coleta_id, seq, id_amostrador, tipo_amostrador, substancia, "
                " vazao_media, hora_inicio, hora_final) "
                "VALUES (?, 1, ?, ?, ?, 2.5, '09:10', '11:50')",
                (cid, codigo, tipo_na_coleta, substancia))
            uid = conn.execute(
                'SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
        return codigo, uid

    yield _montar

    with get_db() as conn:
        for cid in criados['coleta']:
            conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
            conn.execute('DELETE FROM coletas_quimico WHERE id=?', (cid,))
        for did in criados['demanda']:
            conn.execute('DELETE FROM demandas WHERE id=?', (did,))
        for eid in criados['empresa']:
            conn.execute('DELETE FROM empresas WHERE id=?', (eid,))
        for aid in criados['amostr']:
            conn.execute('DELETE FROM amostradores WHERE id=?', (aid,))


def _conferir(uid, codigo):
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.get('/controle/cadeia-custodia/medicoes')
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json()
    achado = [m for m in (body.get('medicoes') or [])
              if str(m.get('codigo') or '').upper() == codigo.upper()]
    assert achado, f'{codigo} não apareceu na conferência'
    return achado[0]


def _problemas_de_tipo(item):
    return [p for p in (item.get('problemas') or []) if p.startswith('amostrador ')]


def test_tipo_errado_na_coleta_nao_reprova_coleta_certa(cenario):
    """O caso das 10 linhas: tubo PVC gravado como TCP."""
    cod, uid = cenario('ZPVC12V96', 'PVC', 'TCP')
    item = _conferir(uid, cod)
    assert item['tipo'] == 'PVC', item['tipo']
    assert _problemas_de_tipo(item) == [], item['problemas']


def test_texto_do_guia_gravado_no_lugar_da_sigla(cenario):
    """O caso das 5 linhas: 'SKC 226-01 (TCP*****)' no campo do tipo."""
    cod, uid = cenario('ZEC98029A', 'EC', 'SKC 225-5 (EC*****)')
    item = _conferir(uid, cod)
    assert item['tipo'] == 'EC', item['tipo']


def test_tubo_fora_do_cadastro_mantem_o_que_a_coleta_gravou(cenario):
    """Sem cadastro não há fonte melhor: usa o que veio, não inventa.

    8 das 39 linhas de produção têm tubo que não está no cadastro.
    """
    init_db()
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('ZTIPO SOLTO')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO coletas_quimico "
            "(empresa_id, empresa_nome, data_coleta, substancias, nome_funcionario, status) "
            "VALUES (?, 'ZTIPO SOLTO', '2026-09-08', 'Sílica Cristalina', 'Fulano', 'concluida')",
            (eid,))
        cid = cur.lastrowid
        conn.execute(
            "INSERT INTO coletas_quimico_amostr "
            "(coleta_id, seq, id_amostrador, tipo_amostrador, substancia, "
            " vazao_media, hora_inicio, hora_final) "
            "VALUES (?, 1, 'ZNAOEXISTE77', 'IOL', 'Sílica Cristalina', 2.5, '09:00', '11:40')",
            (cid,))
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    try:
        item = _conferir(uid, 'ZNAOEXISTE77')
        assert item['tipo'] == 'IOL', item['tipo']
    finally:
        with get_db() as conn:
            conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
            conn.execute('DELETE FROM coletas_quimico WHERE id=?', (cid,))
            conn.execute('DELETE FROM empresas WHERE id=?', (eid,))
