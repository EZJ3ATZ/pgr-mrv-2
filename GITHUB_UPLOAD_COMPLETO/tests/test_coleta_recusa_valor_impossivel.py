# -*- coding: utf-8 -*-
"""Vazão e tempo fisicamente impossíveis não podem ser gravados.

Medido na bancada em 10/09/2026: mandei sete valores impossíveis pelo
`POST /controle/medicoes` e **os sete foram aceitos**, com HTTP 200 e
`ok:true` — vazão de 9.988 L/min, vazão negativa, vazão zero, tempo negativo,
cem horas de coleta.

E aconteceu de verdade. A coleta 20 de produção (Banco do Brasil, 16/07/2026)
tem `vazao_final = 19974,0` onde era 1,9974 — vírgula perdida na digitação —,
`vazao_media = 9988,006` e `volume_l = 0,0`. Está marcada `divergente` e mesmo
assim **concluída**, e a Cadeia de Custódia dela foi gerada: o xlsx seguiu para
o laboratório com o número impossível dentro.

A Cadeia já conferia a faixa do MÉTODO e reprovou 6 dos 9 tubos do teste. Mas
ela é a etapa seguinte e não bloqueia nada. Esta guarda é outra coisa: é a
física do equipamento, não o guia. Bomba de amostragem não faz 9.988 L/min em
nenhum método — a maior vazão real de produção é 2,506 L/min, e o teto aqui é
oito vezes isso, de propósito: pega o erro grosseiro de digitação e não opina
sobre método nenhum.

🔴 Guarda de 400 sem trava na TELA prende o item na fila offline para sempre
(o flush só remove em caso de sucesso). Por isso `nvValidarAmostradores` sobe no
mesmo commit, em `templates/index.html`, e barra antes do `ctrlFetch`.

Dois valores continuam passando de propósito: tempo/volume ausentes (o servidor
os recalcula das horas) e vazão fora da faixa do método mas dentro da física —
esta é decisão da Cadeia, com a faixa do guia na mão, não desta guarda.
"""
import pytest

from app import app
from controle.db import get_db, init_db

PREFIXO = 'GUARDAV'
BASE_OK = {'vazao_inicial': 2.0, 'vazao_final': 1.98, 'tempo_min': 480,
           'volume_l': 955.2, 'hora_inicio': '08:00', 'hora_final': '16:00'}


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (PREFIXO,))
        conn.execute(
            "INSERT INTO demandas (numero_os, empresa_id, status, criado_em) "
            "VALUES (?, ?, 'aberta', '2026-09-01')", (PREFIXO, eid))
        conn.execute('DELETE FROM amostradores WHERE codigo=?', (PREFIXO,))
        conn.execute("INSERT INTO amostradores (codigo, tipo, status, data_entrada) "
                     "VALUES (?, 'PVC', 'disponivel', '2026-09-01')", (PREFIXO,))
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM coletas_quimico_amostr WHERE id_amostrador=?", (PREFIXO,))
        conn.execute("DELETE FROM coletas_quimico WHERE demanda_id IN "
                     "(SELECT id FROM demandas WHERE numero_os=?)", (PREFIXO,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (PREFIXO,))
        conn.execute('DELETE FROM amostradores WHERE codigo=?', (PREFIXO,))


def _salvar(**mudancas):
    with get_db() as conn:
        d = conn.execute('SELECT id, empresa_id FROM demandas WHERE numero_os=?',
                         (PREFIXO,)).fetchone()
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    tubo = dict(BASE_OK, id_amostrador=PREFIXO, tipo_amostrador='PVC',
                substancia='Poeira Total')
    tubo.update(mudancas)
    payload = {
        'tipo': 'quimico', 'empresa_id': d['empresa_id'], 'empresa_nome': 'Teste',
        'demanda_id': d['id'], 'data': '2026-09-10', 'avaliador': 'Bancada',
        'campo_quimico': {'func_nome': 'Trabalhador', 'substancias': 'Poeira Total',
                          'amostradores': [tubo]},
    }
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=payload)
    return r.status_code, (r.get_json() or {})


def _gravadas():
    with get_db() as conn:
        return conn.execute(
            'SELECT COUNT(*) FROM coletas_quimico_amostr WHERE id_amostrador=?',
            (PREFIXO,)).fetchone()[0]


@pytest.mark.parametrize('nome,mudanca', [
    ('virgula perdida na vazao final', {'vazao_final': 19974.0}),
    ('virgula perdida na vazao inicial', {'vazao_inicial': 20111.0}),
    ('vazao negativa', {'vazao_inicial': -2.0, 'vazao_final': -1.9}),
    ('vazao zero nas duas pontas', {'vazao_inicial': 0.0, 'vazao_final': 0.0}),
    ('bomba a 500 L/min', {'vazao_inicial': 500.0, 'vazao_final': 500.0}),
    ('tempo negativo', {'tempo_min': -60}),
    ('cem horas de coleta', {'tempo_min': 6000}),
    ('volume negativo', {'volume_l': -100}),
])
def test_valor_impossivel_e_recusado_e_nada_e_gravado(nome, mudanca):
    status, corpo = _salvar(**mudanca)
    assert status == 400, (nome, status, corpo)
    assert corpo.get('ok') is False, (nome, corpo)
    assert 'erro' in corpo and corpo['erro'], (nome, corpo)
    assert PREFIXO in corpo['erro'], (
        'a mensagem tem de dizer QUAL tubo', nome, corpo['erro'])
    assert _gravadas() == 0, (nome, 'nada pode ficar no banco')


def test_coleta_normal_continua_passando():
    status, corpo = _salvar()
    assert status == 200, (status, corpo)
    assert corpo.get('ok') is True, corpo
    assert _gravadas() == 1


def test_tempo_e_volume_ausentes_continuam_passando():
    """O servidor recalcula dos horários — ausência não é valor impossível."""
    status, corpo = _salvar(tempo_min=None, volume_l=None)
    assert status == 200, (status, corpo)
    assert corpo.get('ok') is True, corpo


def test_vazao_fora_da_faixa_do_metodo_mas_possivel_continua_passando():
    """3 L/min não é físico-impossível: quem julga método é a Cadeia de Custódia."""
    status, corpo = _salvar(vazao_inicial=3.0, vazao_final=3.0)
    assert status == 200, (status, corpo)
    assert corpo.get('ok') is True, corpo
