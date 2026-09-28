# -*- coding: utf-8 -*-
"""Inventário: o técnico cadastra equipamento e acessório pela tela.

Pedido do Matheus (28/09/2026): dar para adicionar os acessórios que o campo usa
além dos equipamentos, com os tipos do inventário do Wesley
(Engenharia Interno\\Wesley Rodrigues\\Outros\\Inventartio Preliminar.docx,
31/03/2026). Acessório só registra o que está guardado: não entra na planilha
de campo.

O que estes testes travam:
  - o catálogo tem os 11 acessórios do inventário do Wesley, como acessório;
  - acessório é contado por QUANTIDADE (total, danificados, precisam de limpeza),
    e danificados/limpeza são parte do total, nunca maiores que ele;
  - acessório não tem calibração e não entra no alerta de calibração;
  - equipamento novo (ex.: calibrador de vazão) entra no alerta como os outros;
  - cadastro repetido é recusado (contaria em dobro);
  - técnico cadastra e edita; remover é só do admin;
  - o "Reconstruir dos certificados" (admin) não apaga o que foi cadastrado à mão.
"""
from datetime import date, timedelta

import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

MARCA = 'TESTINV'
USUARIOS = {'tecnico': 'tecnico.inventario@ocupacional.com.br',
            'admin': 'admin.inventario@ocupacional.com.br'}

# Os 11 itens do "Inventartio Preliminar.docx" do Wesley
ACESSORIOS_DO_WESLEY = {
    'suporte_cassete', 'mangueira', 'ciclone_aluminio', 'ciclone_nylon',
    'calibrador_ciclone', 'suporte_iom', 'calibrador_iom', 'redutor_vazao',
    'chave_calibracao', 'chave_corte', 'quebrador_tcp',
}


def _apaga_testes():
    with get_db() as conn:
        conn.execute('DELETE FROM equipamentos_inventario WHERE marca=?', (MARCA,))


@pytest.fixture(autouse=True)
def _ambiente():
    init_db()
    _apaga_testes()
    with get_db() as conn:
        for role, email in USUARIOS.items():
            conn.execute('DELETE FROM usuarios WHERE email=?', (email,))
            conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                         "VALUES (?,?,?,?,1)", (f'Teste {role}', email, 'x', role))
    yield
    _apaga_testes()
    with get_db() as conn:
        for email in USUARIOS.values():
            conn.execute('DELETE FROM usuarios WHERE email=?', (email,))


def _cli(role):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios WHERE email=?',
                           (USUARIOS[role],)).fetchone()['id']
    cli = app.test_client()
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


def _cria(cli, **campos):
    campos.setdefault('marca', MARCA)
    return cli.post('/controle/equipamentos', json=campos)


def _linha(eid):
    with get_db() as conn:
        r = conn.execute('SELECT * FROM equipamentos_inventario WHERE id=?', (eid,)).fetchone()
    return row_to_dict(r) if r else None


# ── catálogo ──────────────────────────────────────────────────────────

def test_catalogo_tem_os_acessorios_do_inventario_do_wesley():
    r = _cli('tecnico').get('/controle/equipamentos/tipos')
    assert r.status_code == 200
    cat = {t['tipo']: t['categoria'] for t in r.get_json()}
    faltando = ACESSORIOS_DO_WESLEY - set(cat)
    assert not faltando, f'acessórios do inventário fora do catálogo: {faltando}'
    assert all(cat[t] == 'acessorio' for t in ACESSORIOS_DO_WESLEY)
    # a frota que já existia continua como equipamento
    for t in ('bomba', 'dosimetro', 'calibrador_ruido', 'vibrador', 'termometro'):
        assert cat[t] == 'equipamento'


# ── acessório: contado por quantidade ─────────────────────────────────

def test_tecnico_cadastra_acessorio_por_quantidade():
    cli = _cli('tecnico')
    r = _cria(cli, tipo='mangueira', quantidade=18, qtd_danificada=3, qtd_limpeza=18)
    assert r.status_code == 201, r.get_data(as_text=True)
    eid = r.get_json()['id']

    itens = cli.get('/controle/equipamentos').get_json()
    e = next(i for i in itens if i['id'] == eid)
    assert e['categoria'] == 'acessorio'
    assert (e['quantidade'], e['qtd_danificada'], e['qtd_limpeza']) == (18, 3, 18)
    assert e['origem'] == 'manual'


def test_acessorio_nao_entra_no_alerta_de_calibracao():
    cli = _cli('tecnico')
    eid = _cria(cli, tipo='ciclone_aluminio', quantidade=11).get_json()['id']
    calib = cli.get('/controle/equipamentos/calibracao').get_json()
    assert eid not in [i['id'] for i in calib['itens']], (
        'acessório não calibra: no alerta ele sairia como "sem calibração" para sempre')


def test_danificados_e_limpeza_nao_passam_do_total():
    cli = _cli('tecnico')
    r = _cria(cli, tipo='suporte_cassete', quantidade=3, qtd_danificada=5)
    assert r.status_code == 400 and 'Danificados' in r.get_json()['erro']

    eid = _cria(cli, tipo='suporte_cassete', quantidade=9, qtd_danificada=1,
                qtd_limpeza=9).get_json()['id']
    r = cli.put(f'/controle/equipamentos/{eid}', json={'qtd_limpeza': 10})
    assert r.status_code == 400
    # baixar o total abaixo dos danificados também não pode
    r = cli.put(f'/controle/equipamentos/{eid}', json={'quantidade': 0})
    assert r.status_code == 400
    e = _linha(eid)
    assert (e['quantidade'], e['qtd_danificada'], e['qtd_limpeza']) == (9, 1, 9), \
        'PUT recusado não pode ter gravado nada'

    r = cli.put(f'/controle/equipamentos/{eid}', json={'quantidade': 12, 'qtd_limpeza': 4})
    assert r.status_code == 200
    e = _linha(eid)
    assert (e['quantidade'], e['qtd_danificada'], e['qtd_limpeza']) == (12, 1, 4)


@pytest.mark.parametrize('valor', [-1, 'abc', '2,5'])
def test_quantidade_invalida_e_recusada(valor):
    cli = _cli('tecnico')
    assert _cria(cli, tipo='chave_corte', quantidade=valor).status_code == 400
    eid = _cria(cli, tipo='chave_corte', quantidade=2).get_json()['id']
    assert cli.put(f'/controle/equipamentos/{eid}', json={'quantidade': valor}).status_code == 400
    assert _linha(eid)['quantidade'] == 2


# ── validação do cadastro ─────────────────────────────────────────────

def test_tipo_fora_do_catalogo_e_recusado():
    cli = _cli('tecnico')
    assert _cria(cli, tipo='foguete').status_code == 400
    assert _cria(cli, tipo='').status_code == 400
    # "Outro" sem descrição viraria uma linha sem nome
    assert _cria(cli, tipo='outro_acessorio', quantidade=1).status_code == 400
    assert _cria(cli, tipo='outro_acessorio', observacao='Tripé',
                 quantidade=1).status_code == 201


def test_cadastro_repetido_e_recusado():
    cli = _cli('tecnico')
    assert _cria(cli, tipo='redutor_vazao', observacao='4 seções', quantidade=1).status_code == 201
    r = _cria(cli, tipo='redutor_vazao', observacao='4 SEÇÕES ', quantidade=1)
    assert r.status_code == 409, 'mesmo acessório duas vezes conta em dobro'
    # variante diferente do mesmo tipo é outro item
    assert _cria(cli, tipo='redutor_vazao', observacao='2 seções', quantidade=1).status_code == 201

    assert _cria(cli, tipo='calibrador_vazao', numero_serie='TI-126958').status_code == 201
    assert _cria(cli, tipo='calibrador_vazao', numero_serie='ti-126958').status_code == 409


# ── equipamento novo ──────────────────────────────────────────────────

def test_equipamento_novo_entra_no_alerta_de_calibracao():
    cli = _cli('tecnico')
    vencida = (date.today() - timedelta(days=760)).isoformat()   # 2 anos + 1 mês
    r = _cria(cli, tipo='calibrador_vazao', observacao='Defender 510-M',
              numero_serie='TI-VENC', cert_numero='172.712', data_calibracao=vencida)
    assert r.status_code == 201, r.get_data(as_text=True)
    eid = r.get_json()['id']
    calib = cli.get('/controle/equipamentos/calibracao').get_json()
    item = next(i for i in calib['itens'] if i['id'] == eid)
    assert item['status'] == 'vencido'

    r = _cria(cli, tipo='luximetro', numero_serie='TI-LUX', data_calibracao='31/02/2026')
    assert r.status_code == 400, 'data que não existe não pode ser gravada'


# ── permissão ─────────────────────────────────────────────────────────

def test_remover_e_so_do_admin():
    eid = _cria(_cli('tecnico'), tipo='suporte_iom', quantidade=4).get_json()['id']

    r = _cli('tecnico').delete(f'/controle/equipamentos/{eid}')
    assert r.status_code == 403
    assert _linha(eid) is not None

    r = _cli('admin').delete(f'/controle/equipamentos/{eid}')
    assert r.status_code == 200
    assert _linha(eid) is None
    assert _cli('admin').delete(f'/controle/equipamentos/{eid}').status_code == 404


def test_put_em_item_que_nao_existe_da_404():
    assert _cli('tecnico').put('/controle/equipamentos/987654',
                               json={'quantidade': 1}).status_code == 404


def test_reconstruir_dos_certificados_nao_apaga_o_cadastro_manual():
    tec = _cli('tecnico')
    bomba = _cria(tec, tipo='bomba', observacao='Bomba nova', numero_serie='TI-B1').get_json()['id']
    acess = _cria(tec, tipo='ciclone_nylon', quantidade=3).get_json()['id']

    r = _cli('admin').post('/controle/equipamentos/rebuild-frota')
    assert r.status_code == 200, r.get_data(as_text=True)

    assert _linha(bomba) is not None, 'bomba cadastrada à mão sumiu na reconstrução'
    assert _linha(acess) is not None
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM equipamentos_inventario "
                         "WHERE tipo='bomba' AND numero_serie='A060502'").fetchone()['c']
    assert n == 1, 'a frota dos certificados continua sendo recriada, sem duplicar'
