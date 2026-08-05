# -*- coding: utf-8 -*-
"""Arquivamento do estoque para recadastro manual do inventário físico.

Pedido do Wesley (05/08/2026): "apaga os amostradores do sistema q vou inserir
manualmente os q temos aki" — o estoque do sistema deixou de refletir a
prateleira. Virou ARQUIVAR, não DELETE, porque `baixas`/RA apontam para o id
do amostrador: deletar deixaria a baixa órfã e derrubaria o histórico da
medição.

O que estes testes travam:
1. o arquivamento pega SÓ 'disponivel' sem reserva — ciclo em andamento
   (reservado/laboratorio) e histórico (concluido) ficam intactos;
2. recadastrar um código arquivado REATIVA a linha original em vez de abrir
   uma segunda — senão o mesmo dispositivo físico ganharia dois ids e todo
   relatório agrupado por código contaria em dobro (antes desta mudança o
   cadastro em lote simplesmente IGNORAVA o código e o Wesley não conseguiria
   reinserir nada);
3. a reativação zera o ciclo de uso anterior mas preserva o que é do
   dispositivo (lote e certificado de calibração).
"""
import pytest

from app import app
from controle.db import (get_db, init_db, row_to_dict,
                         arquivar_amostradores_estoque,
                         contar_amostradores_estoque)
from controle.routes import cria_amostrador, cria_amostradores_lote

PREFIXO = 'ARQEST'


@pytest.fixture(autouse=True)
def _estoque_vazio():
    """Cada teste começa com o estoque DRENADO.

    O banco de teste nasce com o seed de amostradores do `init_db`, e
    `arquivar_amostradores_estoque()` é global por natureza (é a operação de
    inventário inteiro) — então esvaziamos o estoque antes de semear, senão a
    contagem do teste mediria o seed em vez do caso.
    """
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo LIKE 'ARQEST%'")
    arquivar_amostradores_estoque()
    yield


def _seed(sufixo, status='disponivel', plano=None, **extra):
    codigo = f'{PREFIXO}{sufixo}'
    cols = ['codigo', 'tipo', 'status', 'reservado_por_plano']
    vals = [codigo, 'TCP', status, plano]
    for k, v in extra.items():
        cols.append(k)
        vals.append(v)
    ph = ','.join(['?'] * len(cols))
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO amostradores ({','.join(cols)}) VALUES ({ph})", vals)
        return row_to_dict(conn.execute(
            'SELECT id FROM amostradores WHERE codigo=?', (codigo,)).fetchone())['id']


def _estado(aid):
    with get_db() as conn:
        return row_to_dict(conn.execute(
            'SELECT * FROM amostradores WHERE id=?', (aid,)).fetchone())


# ── 1. escopo do arquivamento ─────────────────────────────────────────

def test_arquiva_so_o_estoque_livre():
    livre       = _seed('01')
    reservado   = _seed('02', status='reservado', plano=77)
    no_lab      = _seed('03', status='laboratorio')
    concluido   = _seed('04', status='concluido')
    manutencao  = _seed('05', status='manutencao')
    # disponível mas com plano dono: inconsistência conhecida, não arquivar
    disp_c_plano = _seed('06', status='disponivel', plano=88)

    assert contar_amostradores_estoque() == 1
    n, codigos = arquivar_amostradores_estoque()

    assert n == 1 and codigos == [f'{PREFIXO}01']
    assert int(_estado(livre)['arquivado']) == 1
    assert _estado(livre)['arquivado_em']
    for aid in (reservado, no_lab, concluido, manutencao, disp_c_plano):
        assert int(_estado(aid)['arquivado'] or 0) == 0


def test_arquivamento_e_idempotente():
    _seed('10')
    assert arquivar_amostradores_estoque()[0] == 1
    assert contar_amostradores_estoque() == 0
    assert arquivar_amostradores_estoque()[0] == 0


def test_arquivar_nao_deleta_a_linha():
    """A garantia central: `baixas` aponta para o id, então a linha tem de ficar."""
    aid = _seed('11')
    arquivar_amostradores_estoque()
    with get_db() as conn:
        assert conn.execute(
            'SELECT COUNT(*) c FROM amostradores WHERE id=?', (aid,)
        ).fetchone()['c'] == 1


# ── 2. recadastro reativa em vez de duplicar ──────────────────────────

def _post_lote(codigos, **body):
    payload = {'tipo': 'TCP', 'codigos': codigos}
    payload.update(body)
    with app.test_request_context('/controle/amostradores/lote', json=payload):
        return cria_amostradores_lote().get_json()


def test_lote_reativa_codigo_arquivado_em_vez_de_ignorar():
    aid = _seed('20')
    arquivar_amostradores_estoque()

    r = _post_lote([f'{PREFIXO}20'])

    assert r['reativados'] == 1
    assert r['criados'] == 0 and r['ignorados'] == 0
    st = _estado(aid)
    assert int(st['arquivado']) == 0 and st['arquivado_em'] is None
    assert st['status'] == 'disponivel'
    # mesma linha, mesmo id — nada duplicado
    with get_db() as conn:
        assert conn.execute(
            'SELECT COUNT(*) c FROM amostradores WHERE codigo=?',
            (f'{PREFIXO}20',)).fetchone()['c'] == 1


def test_lote_ainda_ignora_codigo_ativo_e_cria_o_novo():
    _seed('21')                       # ativo
    arquivado = _seed('22')
    arquivar_amostradores_estoque()   # arquiva 21 e 22
    _seed('21')                       # 21 volta a existir ativo (recadastro anterior)

    r = _post_lote([f'{PREFIXO}21', f'{PREFIXO}22', f'{PREFIXO}23'])

    assert r['ignorados'] == 1        # 21 já ativo
    assert r['reativados'] == 1       # 22 estava arquivado
    assert r['criados'] == 1          # 23 é novo
    assert int(_estado(arquivado)['arquivado']) == 0


def test_lote_casa_codigo_gravado_em_minusculo():
    """`limpos` vem em UPPER; comparar cru deixava passar e tentava duplicar."""
    with get_db() as conn:
        conn.execute("INSERT INTO amostradores (codigo, tipo, status) "
                     "VALUES ('arqest30', 'TCP', 'disponivel')")
    r = _post_lote([f'{PREFIXO}30'])
    assert r['criados'] == 0 and r['ignorados'] == 1


def test_cadastro_unitario_reativa_arquivado():
    aid = _seed('40')
    arquivar_amostradores_estoque()
    with app.test_request_context(
            '/controle/amostradores',
            json={'codigo': f'{PREFIXO}40', 'tipo': 'EC'}):
        r = cria_amostrador().get_json()
    assert r['ok'] and r['reativado'] and r['id'] == aid
    st = _estado(aid)
    assert int(st['arquivado']) == 0 and st['tipo'] == 'EC'


def test_cadastro_unitario_continua_barrando_duplicata_ativa():
    _seed('41')
    with app.test_request_context(
            '/controle/amostradores',
            json={'codigo': f'{PREFIXO}41', 'tipo': 'TCP'}):
        resp, code = cria_amostrador()
    assert code == 409 and resp.get_json()['duplicado'] is True


# ── 3. o que a reativação zera e o que preserva ───────────────────────

def test_reativacao_zera_ciclo_anterior_e_preserva_o_dispositivo():
    aid = _seed('50', status='disponivel',
                empresa_id=None, avaliador='Kelly',
                data_medicao='2024-02-20', data_envio_lab='2024-02-25',
                data_resultado='2024-03-10', data_conclusao='2024-03-11',
                lote='L-998', cert_numero='CERT-123',
                cert_laboratorio='SGS', cert_validade='2027-01-01')
    arquivar_amostradores_estoque()
    _post_lote([f'{PREFIXO}50'])

    st = _estado(aid)
    # ciclo de uso anterior: zerado (senão o amostrador "novo" nasce com a
    # coleta antiga colada nele)
    for campo in ('empresa_id', 'avaliador', 'data_medicao', 'data_envio_lab',
                  'data_resultado', 'data_conclusao', 'reservado_por_plano'):
        assert not st[campo], f'{campo} deveria ter sido zerado'
    # atributos do dispositivo físico: preservados
    assert st['lote'] == 'L-998'
    assert st['cert_numero'] == 'CERT-123'
    assert st['cert_laboratorio'] == 'SGS'
    assert st['cert_validade'] == '2027-01-01'


def test_reativado_volta_a_aparecer_na_listagem():
    from controle.db import list_amostradores
    _seed('60')
    arquivar_amostradores_estoque()
    visiveis = [a['codigo'] for a in list_amostradores({})]
    assert f'{PREFIXO}60' not in visiveis
    arquivados = [a['codigo'] for a in list_amostradores({'arquivados': '1'})]
    assert f'{PREFIXO}60' in arquivados

    _post_lote([f'{PREFIXO}60'])
    assert f'{PREFIXO}60' in [a['codigo'] for a in list_amostradores({})]
