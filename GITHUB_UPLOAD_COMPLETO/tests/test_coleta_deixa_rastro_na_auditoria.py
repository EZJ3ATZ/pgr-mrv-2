# -*- coding: utf-8 -*-
"""Finalizar uma planilha de campo tem de deixar evento na Auditoria.

Medido em produção em 10/09/2026: existe **um** evento `coleta_ruido_criada` e
**um** `coleta_quimico_criada`, os dois de maio — contra **96 coletas** gravadas
entre junho e setembro. A trilha de auditoria da coleta está morta desde
28/05/2026.

O motivo é mais direto do que parecia: `coleta_ruido_criada` e
`coleta_quimico_criada` **não são gravados por lugar nenhum do Python**. Eles só
existem em `templates/index.html`, onde a aba Auditoria oferece os dois como
opção de filtro, com ícone e rótulo — a tela tem um filtro para um evento que o
servidor nunca produz. Os dois registros de maio vieram de uma rota antiga que
já saiu do código.

O que se perde: não há como responder "quem finalizou esta planilha e quando".
`atividade_log` registra o clique na tela, mas só cobre agosto e setembro e não
liga o clique à coleta gravada. A coleta tem `tecnico_login` e `criado_em`, mas
quem procura no feed de eventos — que é onde o histórico é lido — não acha nada.

O evento carrega o id da coleta em `ref_id` para o feed poder linkar, e o tipo
casa exatamente o que o filtro da tela já espera.
"""
import pytest

from app import app
from controle.db import get_db, init_db, row_to_dict

OS = 'EVTCOL'


@pytest.fixture(autouse=True)
def _cenario():
    init_db()
    with get_db() as conn:
        eid = conn.execute('SELECT id FROM empresas ORDER BY id LIMIT 1').fetchone()['id']
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))
        conn.execute("INSERT INTO demandas (numero_os, empresa_id, status, criado_em) "
                     "VALUES (?, ?, 'aberta', '2026-09-01')", (OS, eid))
        did = conn.execute('SELECT id FROM demandas WHERE numero_os=?', (OS,)).fetchone()['id']
        conn.execute("DELETE FROM eventos WHERE tipo LIKE 'coleta_%_criada'")
    yield {'did': did, 'eid': eid}
    with get_db() as conn:
        for t in ('coletas_ruido', 'coletas_quimico', 'coletas_outros'):
            conn.execute(f'DELETE FROM {t} WHERE demanda_id=?', (did,))
        conn.execute('DELETE FROM demandas WHERE numero_os=?', (OS,))
        conn.execute("DELETE FROM eventos WHERE tipo LIKE 'coleta_%_criada'")


def _salvar(cen, tipo, bloco):
    with get_db() as conn:
        uid = conn.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()['id']
    payload = {'tipo': tipo, 'empresa_id': cen['eid'], 'empresa_nome': 'Empresa Teste',
               'demanda_id': cen['did'], 'data': '2026-09-10',
               'avaliador': 'Tecnico', 'os': OS}
    payload.update(bloco)
    with app.test_client() as cli:
        with cli.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        r = cli.post('/controle/medicoes', json=payload)
    return r.status_code, (r.get_json() or {})


def _eventos(tipo):
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM eventos WHERE tipo=? ORDER BY id DESC', (tipo,)).fetchall()]


def test_ruido_deixa_evento(_cenario):
    st, corpo = _salvar(_cenario, 'ruido', {'campo_ruido': {
        'hora_ini': '08:00', 'hora_fim': '16:00',
        'trabalhadores': [{'nome': 'Trabalhador'}]}})
    assert st == 200, corpo
    evs = _eventos('coleta_ruido_criada')
    assert len(evs) == 1, evs
    assert evs[0]['ref_id'] == corpo['id'], evs[0]
    assert 'Empresa Teste' in (evs[0]['descricao'] or ''), evs[0]


def test_quimico_deixa_evento(_cenario):
    st, corpo = _salvar(_cenario, 'quimico', {'campo_quimico': {
        'func_nome': 'Trabalhador', 'substancias': 'Poeira Total',
        'amostradores': [{'id_amostrador': 'EVTCOL1', 'vazao_inicial': 2.0,
                          'vazao_final': 1.98, 'tempo_min': 480}]}})
    assert st == 200, corpo
    evs = _eventos('coleta_quimico_criada')
    assert len(evs) == 1, evs
    assert evs[0]['ref_id'] == corpo['id'], evs[0]


@pytest.mark.parametrize('tipo,esperado', [
    ('calor', 'coleta_calor_criada'),
    ('vibracao_vbma', 'coleta_vibracao_criada'),
])
def test_calor_e_vibracao_deixam_evento(_cenario, tipo, esperado):
    st, corpo = _salvar(_cenario, tipo, {'campo_generico': {
        'hora_ini': '09:00', 'hora_fim': '11:00'}})
    assert st == 200, corpo
    evs = _eventos(esperado)
    assert len(evs) == 1, (esperado, evs)


def test_evento_diz_quem_finalizou(_cenario):
    st, corpo = _salvar(_cenario, 'ruido', {'campo_ruido': {
        'hora_ini': '08:00', 'hora_fim': '16:00',
        'trabalhadores': [{'nome': 'Trabalhador'}]}})
    assert st == 200, corpo
    ev = _eventos('coleta_ruido_criada')[0]
    assert (ev['usuario'] or '').strip(), 'o feed tem de dizer quem finalizou'


def test_planilha_duplicada_nao_gera_evento_novo(_cenario):
    bloco = {'campo_ruido': {'hora_ini': '08:00', 'hora_fim': '16:00',
                             'trabalhadores': [{'nome': 'Trabalhador'}]}}
    _salvar(_cenario, 'ruido', bloco)
    _salvar(_cenario, 'ruido', bloco)   # recusada pela trava de duplicidade
    assert len(_eventos('coleta_ruido_criada')) == 1
