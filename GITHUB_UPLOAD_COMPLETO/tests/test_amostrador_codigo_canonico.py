# -*- coding: utf-8 -*-
"""O codigo do amostrador e gravado como esta no cadastro (02/09/2026).

O campo e texto livre. O tecnico digita 'MTS3091', 'pvc 26t91' ou so 'FVPH'; o
cadastro guarda '3091' e o laboratorio devolve outra grafia ainda. Cada
divergencia dessas e uma amostra que nao casa com o laudo depois.

Caso real que motivou: a coleta da Hypofarma de 03/08 gravou 'MTS3091' e 'FVPH'
enquanto o laudo RA 81964681 trazia '3091' e 'FVPH2181'. Numa conferencia
contra o laboratorio a medicao parecia nao existir, e o tecnico levou a culpa
por um trabalho que ele tinha registrado.

Dos 39 codigos em producao em 02/09/2026, 6 nao existiam no cadastro.

Os codigos aqui sao proposital e visivelmente ficticios (comecam com Z): usar
os codigos reais fazia este arquivo brigar com test_cadeia_puxa_todas_as_medicoes,
que semeia '3091'/'MTS' com id fixo. Teste que apaga por codigo derruba o
vizinho quando a suite roda inteira.
"""
import pytest

from app import app
from controle.db import get_db, init_db
from controle.routes import _canonizar_amostradores


@pytest.fixture
def tubo():
    """Cadastra amostradores de teste e remove no fim, para nao vazar para os
    outros arquivos da suite."""
    criados = []

    def _criar(codigo, tipo):
        init_db()
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO amostradores (codigo, tipo, status, arquivado) "
                "VALUES (?, ?, 'disponivel', 0)", (codigo, tipo))
            criados.append(cur.lastrowid)
            return cur.lastrowid

    yield _criar
    if criados:
        with get_db() as conn:
            for i in criados:
                conn.execute("DELETE FROM amostradores WHERE id=?", (i,))


def test_prefixo_do_tipo_digitado_a_mais_resolve_para_o_cadastro(tubo):
    """'ZMTS9091' e o mesmo tubo que o cadastro chama de '9091' — igual ao
    'MTS3091' x '3091' da Hypofarma."""
    tubo('9091', 'ZMTS')
    saida, soltos = _canonizar_amostradores([
        {'id_amostrador': 'ZMTS9091', 'tipo_amostrador': 'ZMTS'}])
    assert saida[0]['id_amostrador'] == '9091', saida
    assert soltos == [], soltos


def test_minuscula_e_espaco_nao_criam_codigo_novo(tubo):
    tubo('Z6T91', 'ZPVC')
    saida, soltos = _canonizar_amostradores([
        {'id_amostrador': ' zpvc z6t91 ', 'tipo_amostrador': 'ZPVC'}])
    assert saida[0]['id_amostrador'] == 'Z6T91', saida
    assert soltos == [], soltos


def test_codigo_incompleto_nao_e_inventado_e_volta_como_solto(tubo):
    """'ZFVPH' e so o tipo, sem numero. Nenhum cadastro tem isso — o certo e
    avisar, nao adivinhar qual tubo o tecnico quis dizer."""
    tubo('ZFVPH9181', 'ZFVPH')
    saida, soltos = _canonizar_amostradores([
        {'id_amostrador': 'ZFVPH', 'tipo_amostrador': 'ZFVPH'}])
    assert saida[0]['id_amostrador'] == 'ZFVPH', saida   # nao foi reescrito
    assert soltos == ['ZFVPH'], soltos


def test_codigo_ja_canonico_passa_intacto(tubo):
    tubo('ZTCP955AV3', 'ZTCP')
    saida, soltos = _canonizar_amostradores([
        {'id_amostrador': 'ZTCP955AV3', 'tipo_amostrador': 'ZTCP'}])
    assert saida[0]['id_amostrador'] == 'ZTCP955AV3', saida
    assert soltos == [], soltos


def test_campo_vazio_nao_vira_solto():
    """Linha de amostrador em branco e linha nao preenchida, nao erro."""
    saida, soltos = _canonizar_amostradores([{'id_amostrador': '', 'tipo_amostrador': 'PVC'}])
    assert soltos == [], soltos
    assert saida[0]['id_amostrador'] == ''


def test_lista_vazia_nao_quebra():
    assert _canonizar_amostradores([]) == ([], [])
    assert _canonizar_amostradores(None) == ([], [])


def test_codigo_desconhecido_nao_bloqueia_e_e_devolvido_avisado(tubo):
    """Tubo fora do cadastro nao pode travar o tecnico em campo, mas tem de
    voltar avisado: sem cadastro ele tambem nao recebe baixa e fica
    'disponivel' no estoque estando no laboratorio."""
    saida, soltos = _canonizar_amostradores([
        {'id_amostrador': 'ZNAOEXISTE99', 'tipo_amostrador': 'ZPVC'}])
    assert saida[0]['id_amostrador'] == 'ZNAOEXISTE99', saida
    assert soltos == ['ZNAOEXISTE99'], soltos


def test_salvar_coleta_grava_o_codigo_do_cadastro(tubo):
    """Ponta a ponta: o que entra como 'zmts9091' fica '9091' no banco, que e
    a chave que casa com o laudo."""
    init_db()
    tubo('9091', 'ZMTS')
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA CANON TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) "
            "VALUES (?, 'OS-CN-1', 'pendente')", (eid,))
        did = cur.lastrowid
        uid = conn.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()['id']

    payload = {
        'tipo': 'quimico', 'empresa_id': eid, 'demanda_id': did,
        'empresa_nome': 'EMPRESA CANON TESTE', 'data': '2026-09-02',
        'avaliador': 'Helbert', 'os': 'OS-CN-1',
        'campo_quimico': {
            'func_nome': 'Fulano', 'substancias': 'Ácido peracético',
            'amostradores': [
                {'id_amostrador': 'zmts9091', 'tipo_amostrador': 'ZMTS'},
                {'id_amostrador': 'ZNAOEXISTE99', 'tipo_amostrador': 'ZPVC'},
            ],
        },
    }
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=payload)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:400])
    body = r.get_json()
    assert body.get('ok') is True, body
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id_amostrador FROM coletas_quimico_amostr WHERE coleta_id=? ORDER BY seq",
            (body['id'],)).fetchall()
    cods = [(x['id_amostrador'] if hasattr(x, 'keys') else x[0]) for x in rows]
    assert '9091' in cods, cods
    assert 'ZNAOEXISTE99' in (body.get('amostradores_fora_do_cadastro') or []), body


def test_canoniza_tambem_o_tipo(tubo):
    """`codigo` nao tem UNIQUE no banco. Levar so o codigo e deixar o tipo como
    o tecnico digitou parte a identidade do tubo no meio."""
    tubo('9440', 'ZIOL')
    saida, soltos = _canonizar_amostradores([
        {'id_amostrador': 'ZIOL9440', 'tipo_amostrador': 'ZPVC'}])   # tipo errado
    assert saida[0]['id_amostrador'] == '9440', saida
    assert saida[0]['tipo_amostrador'] == 'ZIOL', saida
    assert soltos == [], soltos


def test_a_canonizacao_nao_cega_a_trava_de_duplicidade(tubo):
    """A canonizacao roda DEPOIS de _coleta_duplicada de proposito: as linhas
    ja gravadas em producao estao cruas, e comparar canonico contra cru
    deixaria passar planilha repetida justamente na transicao."""
    init_db()
    tubo('9091', 'ZMTS')
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('EMPRESA DEDUP TESTE')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (empresa_id, numero_os, status) "
            "VALUES (?, 'OS-DD-1', 'pendente')", (eid,))
        did = cur.lastrowid
        uid = conn.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()['id']

    def _enviar():
        p = {
            'tipo': 'quimico', 'empresa_id': eid, 'demanda_id': did,
            'empresa_nome': 'EMPRESA DEDUP TESTE', 'data': '2026-09-02',
            'avaliador': 'Helbert', 'os': 'OS-DD-1',
            'campo_quimico': {
                'func_nome': 'Fulano', 'substancias': 'Ácido peracético',
                'amostradores': [{'id_amostrador': 'ZMTS9091', 'tipo_amostrador': 'ZMTS'}],
            },
        }
        with app.test_client() as cli:
            with cli.session_transaction() as s:
                s['_user_id'] = str(uid)
                s['_fresh'] = True
            return cli.post('/controle/medicoes', json=p).get_json()

    primeira = _enviar()
    assert primeira.get('ok') is True, primeira
    segunda = _enviar()
    assert segunda.get('duplicada') is True, \
        'a mesma planilha entrou duas vezes — a canonizacao cegou o dedup'
