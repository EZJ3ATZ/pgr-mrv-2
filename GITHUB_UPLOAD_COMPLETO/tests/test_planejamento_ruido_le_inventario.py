# -*- coding: utf-8 -*-
"""Planejamento de ruído: dosímetro e calibrador com a calibração do INVENTÁRIO.

17/11/2026, Wesley no grupo Os Medidores: "Os equipamentos da Inlite não aparecem para ser
reservados no sistema mesmo já estando cadastrados. Eu atualizei o sistema com o certificado de
calibração mas ainda aparece vencido". As listas do planejamento (/controle/dosimetros-ruido e
/controle/calibradores-ruido) liam só o mapa fixo do app.py, com a calibração de 2025, e
mostravam VENCIDO (e o certificado antigo) o que o inventário já tinha recalibrado.

O que estes testes travam:
  - pelo nº de série, data de calibração e certificado do inventário valem por cima do mapa fixo,
    e o aparelho deixa de sair vencido;
  - aparelho que só existe no inventário entra na lista, com a marca dele (o filtro da tela é
    pela marca do dosímetro);
  - aparelho sem linha no inventário segue como estava no mapa fixo.
"""
import sys

import pytest

from app import app
from controle.db import get_db, init_db

MARCA = 'Inlite'
ORIGEM = 'teste_planinv'
EMAIL = 'tecnico.planinv@ocupacional.com.br'


def _apaga():
    with get_db() as conn:
        conn.execute('DELETE FROM equipamentos_inventario WHERE origem=?', (ORIGEM,))
        conn.execute('DELETE FROM usuarios WHERE email=?', (EMAIL,))


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    init_db()
    _apaga()
    mod = sys.modules['app']
    # Mapa fixo como o de produção: aparelhos calibrados em 2025 (vencidos pela regra de 1 ano)
    monkeypatch.setitem(mod._DOSIM_RUIDO, 'inlite', dict(mod._DOSIM_RUIDO.get('inlite', {}), **{
        'TPI-DOS-1': {'serie': 'TPI-DOS-1', 'cert': '42.000-2025', 'data_calib': '2025-08-28'},
        'TPI-DOS-3': {'serie': 'TPI-DOS-3', 'cert': '42.003-2025', 'data_calib': '2025-08-28'},
    }))
    monkeypatch.setattr(mod, '_CALIB_RUIDO', dict(mod._CALIB_RUIDO, **{
        'TPI-CAL-1': {'serie': 'TPI-CAL-1', 'cert': '42.100-2025', 'data_calib': '2025-08-28',
                      'marca': 'INLITE', 'modelo': 'CalPro'},
    }))
    with get_db() as conn:
        conn.execute("INSERT INTO usuarios (nome, email, senha_hash, role, ativo) VALUES (?,?,?,?,1)",
                     ('Teste planejamento', EMAIL, 'x', 'tecnico'))
        for tipo, serie, cert, dcal in (('dosimetro', 'tpi-dos-1 ', '189.700', '2026-11-04'),
                                        ('dosimetro', 'TPI-DOS-2', '189.701', '2026-11-05'),
                                        ('calibrador_ruido', 'TPI-CAL-1', '189.723', '2026-11-04')):
            conn.execute(
                "INSERT INTO equipamentos_inventario (tipo, marca, modelo, numero_serie, compatibilidade, "
                "status, cert_numero, observacao, data_calibracao, origem) "
                "VALUES (?,?,?,?,'','disponivel',?,?,?,?)",
                (tipo, MARCA, 'DoseMax V2' if tipo == 'dosimetro' else 'CalPro', serie, cert,
                 'teste do planejamento', dcal, ORIGEM))
    yield
    _apaga()


def _cli():
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios WHERE email=?', (EMAIL,)).fetchone()['id']
    cli = app.test_client()
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


def _por_serie(lista):
    return {x['serie'].strip().upper(): x for x in lista}


def test_dosimetro_recalibrado_no_inventario_nao_sai_vencido():
    r = _cli().get('/controle/dosimetros-ruido')
    assert r.status_code == 200
    d = _por_serie(r.get_json()['dosimetros'])
    recal = d['TPI-DOS-1']
    assert recal['data_calib'] == '2026-11-04'
    assert recal['cert'] == '189.700'
    assert recal['validade'] == '2027-11-04'
    assert recal['vencido'] is False
    assert recal['marca'] == 'INLITE'


def test_dosimetro_so_do_inventario_entra_na_lista_com_a_marca():
    d = _por_serie(_cli().get('/controle/dosimetros-ruido').get_json()['dosimetros'])
    novo = d['TPI-DOS-2']
    assert novo['marca'] == 'INLITE' and novo['cert'] == '189.701'
    assert novo['vencido'] is False


def test_sem_linha_no_inventario_fica_o_mapa_fixo():
    d = _por_serie(_cli().get('/controle/dosimetros-ruido').get_json()['dosimetros'])
    velho = d['TPI-DOS-3']
    assert velho['data_calib'] == '2025-08-28' and velho['cert'] == '42.003-2025'
    assert velho['vencido'] is True


def test_calibrador_recalibrado_no_inventario_nao_sai_vencido():
    r = _cli().get('/controle/calibradores-ruido')
    assert r.status_code == 200
    c = _por_serie(r.get_json()['calibradores'])
    cal = c['TPI-CAL-1']
    assert cal['data_calib'] == '2026-11-04' and cal['cert'] == '189.723'
    assert cal['vencido'] is False and cal['marca'] == 'INLITE'
