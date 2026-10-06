# -*- coding: utf-8 -*-
"""Cada planilha de campo soma os pontos que mediu; a OS só fecha com tudo feito.

06/10/2026, preparando a OS que chega do CRM direto no portal: a 1ª planilha
completava a medição inteira (os 10 pontos de ruído de uma vez) e a do 2º dia
ou do 2º trabalhador era RECUSADA como "planilha duplicada" — numa OS com
Sílica x6 só a 1ª entrava. E a demanda local concluía na 2ª planilha qualquer,
com medição por fazer e resultado de laboratório por chegar.
"""
import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

OS = 'SOMAPONTOS'


@pytest.fixture
def cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))
        did = conn.execute("INSERT INTO demandas (numero_os, empresa_id, status, origem, criado_em) "
                           "VALUES (?, ?, 'pendente', 'crm_os', '2026-10-06')", (OS, eid)).lastrowid
        ids = {}
        for ag, qtd in (('Poeira Respirável + Sílica Livre Cristalina', 3), ('Ruído Ocupacional', 4)):
            ids[ag] = conn.execute("INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, "
                                   "qtd_pontos_feita, status) VALUES (?, ?, ?, 0, 'pendente')",
                                   (did, ag, qtd)).lastrowid
    yield {'did': did, 'eid': eid, 'sil': ids['Poeira Respirável + Sílica Livre Cristalina'],
           'ruido': ids['Ruído Ocupacional']}
    with get_db() as conn:
        for t in ('coletas_ruido', 'coletas_quimico', 'coletas_outros', 'medicoes'):
            conn.execute(f'DELETE FROM {t} WHERE demanda_id=?', (did,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))


def _salvar(c, tipo, data, bloco):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    payload = {'tipo': tipo, 'empresa_id': c['eid'], 'empresa_nome': 'Teste', 'demanda_id': c['did'],
               'data': data, 'avaliador': 'Tecnico', 'os': OS}
    payload.update(bloco)
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=payload)
    return r.status_code, (r.get_json() or {})


def _quimico(c, trab, tubo):
    return _salvar(c, 'quimico', '2026-10-06', {'campo_quimico': {
        'func_nome': trab, 'substancias': 'Poeira Respirável + Sílica Livre Cristalina',
        'amostradores': [{'id_amostrador': tubo, 'vazao_inicial': 2.0, 'vazao_final': 1.98, 'tempo_min': 480}]}})


def _ruido(c, data, n):
    return _salvar(c, 'ruido', data, {'campo_ruido': {
        'hora_ini': '08:00', 'hora_fim': '16:00',
        'trabalhadores': [{'nome': f'T{i}'} for i in range(n)]}})


def _med(mid):
    with get_db() as conn:
        return row_to_dict(conn.execute('SELECT qtd_pontos_feita, status FROM medicoes WHERE id=?',
                                        (mid,)).fetchone())


def _dem(did):
    with get_db() as conn:
        return row_to_dict(conn.execute('SELECT status, data_conclusao FROM demandas WHERE id=?',
                                        (did,)).fetchone())


def test_cada_trabalhador_do_quimico_entra_e_soma(cenario):
    for i, tubo in enumerate(['SOMA01', 'SOMA02', 'SOMA03']):
        st, corpo = _quimico(cenario, f'Trabalhador {i}', tubo)
        assert st == 200 and corpo.get('ok'), corpo
    assert _med(cenario['sil']) == {'qtd_pontos_feita': 3, 'status': 'aguardando_lab'}
    assert _dem(cenario['did'])['status'] == 'em_andamento'


def test_ruido_em_dois_dias_soma_e_nao_e_recusado(cenario):
    st, corpo = _ruido(cenario, '2026-10-06', 2)
    assert st == 200 and corpo.get('ok'), corpo
    assert _med(cenario['ruido']) == {'qtd_pontos_feita': 2, 'status': 'pendente'}
    st, corpo = _ruido(cenario, '2026-10-07', 2)
    assert st == 200 and corpo.get('ok'), corpo
    assert _med(cenario['ruido']) == {'qtd_pontos_feita': 4, 'status': 'realizado'}
    # ponto além do previsto entra, não é recusado
    st, corpo = _ruido(cenario, '2026-10-08', 1)
    assert st == 200 and corpo.get('ok'), corpo


def test_os_nao_fecha_com_medicao_em_aberto_e_fecha_quando_completa(cenario):
    _ruido(cenario, '2026-10-06', 4)
    _quimico(cenario, 'A', 'SOMA11')
    _quimico(cenario, 'B', 'SOMA12')
    # ruído completo, sílica 2 de 3: antes concluía aqui (2ª planilha)
    assert _dem(cenario['did'])['status'] == 'em_andamento'
    _quimico(cenario, 'C', 'SOMA13')
    # sílica completa no campo, mas esperando o laboratório: ainda aberta
    assert _dem(cenario['did'])['status'] == 'em_andamento'
    with get_db() as conn:   # o resultado do lab chega (o lab_inbox faz isso)
        conn.execute("UPDATE medicoes SET status='realizado' WHERE id=?", (cenario['sil'],))
    from controle.routes import _atualizar_demanda_por_coleta
    _atualizar_demanda_por_coleta(cenario['did'], 'concluida')
    d = _dem(cenario['did'])
    assert d['status'] == 'concluida' and d['data_conclusao']
