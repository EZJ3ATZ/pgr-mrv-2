# -*- coding: utf-8 -*-
"""A fila de RAs do laboratório tem de sair da tela por AÇÃO do técnico.

Queixa de 05/08/2026 (WhatsApp): "atualizei todos os amostradores e esses aqui eu
coloquei como concluídos mas não sei como fazer isso aqui sumir".

Causa: a fila (`ms_sync_state['lab_resultados']`) é um retrato gravado no sync, e
`acao` era lido desse retrato. Cadastrar o amostrador que o laudo cita, ou vincular
o RA à mão, não mexia no retrato — a linha só sumia no sync seguinte (até 3h), e
o caso "manual" não sumia NUNCA, porque a vinculação manual não deixava rastro.

Aqui provamos as duas pontas:
  1. `_revalidar_nao_cadastrados` reconfere os códigos contra o inventário de agora;
  2. `registrar_ra_vinculado` grava o vínculo RA↔amostrador que faz `casou` encher.

As funções sob teste abrem a própria conexão (SQLite trava com conexão de fora
aberta), então os helpers daqui abrem e FECHAM a delas antes de chamar.
"""
import pytest

from controle.db import get_db, init_db
from controle.lab_inbox import (_classificar_acao_resultados, _completar_casou_por_ra_laudos,
                                _ensure_ra_laudos, _revalidar_nao_cadastrados,
                                registrar_ra_vinculado)


def _limpar():
    with get_db() as c:
        _ensure_ra_laudos(c)
        c.execute("DELETE FROM ra_laudos WHERE amostrador_id < 0")
        c.execute("DELETE FROM amostradores WHERE id < 0")


@pytest.fixture(autouse=True)
def banco():
    init_db()
    _limpar()
    yield
    _limpar()


def _inserir(id_, codigo, status='laboratorio', data_resultado=None):
    with get_db() as c:
        c.execute(
            "INSERT INTO amostradores (id, codigo, tipo, status, data_resultado, arquivado) "
            "VALUES (?,?,?,?,?,0)", (id_, codigo, 'TCP', status, data_resultado))


def _laudos_de(aid):
    with get_db() as c:
        _ensure_ra_laudos(c)
        return [dict(r) for r in c.execute(
            "SELECT ra_num, amostrador_cod, funcionario FROM ra_laudos WHERE amostrador_id=?",
            (aid,)).fetchall()]


# ── 1. cadastrou o tubo que faltava ────────────────────────────────────

def test_cadastrado_sem_resultado_deixa_de_pedir_cadastro():
    """Sai de 'cadastrar' para 'manual': a próxima ação agora é VINCULAR."""
    _inserir(-1, 'TCP4806AV3')
    fila = [{'ra_num': '81962594', 'casou': [], 'nao_cadastrados': ['TCP4806AV3']}]
    _revalidar_nao_cadastrados(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['nao_cadastrados'] == []
    assert fila[0]['acao'] == 'manual'
    # e a tela ganha o alvo exato para vincular, sem seletor livre
    assert [s['codigo'] for s in fila[0]['sugeridos']] == ['TCP4806AV3']


def test_tubo_concluido_a_mao_sem_data_entra_como_sugerido():
    """O caso real de 05/08/2026: 16 códigos concluídos à mão, sem data_resultado.

    Nenhum aparecia no seletor de vinculação (que só lista 'No laboratório'), então
    a fila não tinha saída. Vira `sugeridos` para a tela vincular direto.
    """
    _inserir(-7, 'TCP4908AV3', status='concluido')     # sem data_resultado
    _inserir(-8, 'PVC14V69', status='concluido')
    fila = [{'ra_num': '81963184', 'casou': [],
             'nao_cadastrados': ['TCP4908AV3', 'PVC14V69', 'FANTASMA9']}]
    _revalidar_nao_cadastrados(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['nao_cadastrados'] == ['FANTASMA9']   # esse segue fora mesmo
    assert sorted(s['codigo'] for s in fila[0]['sugeridos']) == ['PVC14V69', 'TCP4908AV3']
    assert fila[0]['acao'] == 'cadastrar'               # ainda falta 1 → cadastrar antes


def test_cadastrado_com_resultado_sai_da_fila():
    _inserir(-2, 'TCP4908AV3', status='concluido', data_resultado='2026-08-04')
    fila = [{'ra_num': '81962596', 'casou': [], 'nao_cadastrados': ['TCP4908AV3']}]
    _revalidar_nao_cadastrados(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['acao'] == 'resolvido'
    assert 'sugeridos' not in fila[0]      # nada a fazer, nada a sugerir


def test_codigo_que_segue_fora_do_inventario_continua_cobrando():
    """Sem o tubo cadastrado, a guarda contra gravar no amostrador errado fica."""
    fila = [{'ra_num': '81963406', 'casou': [], 'nao_cadastrados': ['PVC04V50']}]
    _revalidar_nao_cadastrados(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['nao_cadastrados'] == ['PVC04V50']
    assert fila[0]['acao'] == 'cadastrar'


def test_laudo_de_dois_tubos_espera_os_dois():
    """Caso real 75A1+75B1: cadastrar só um não libera a linha."""
    _inserir(-3, '75B1', status='concluido', data_resultado='2026-08-04')
    fila = [{'ra_num': '81955409', 'casou': ['75A1'], 'nao_cadastrados': ['75B1', 'X7P75C9']}]
    _revalidar_nao_cadastrados(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['nao_cadastrados'] == ['X7P75C9']
    assert fila[0]['acao'] == 'cadastrar'


def test_revalidar_nao_ressuscita_quem_ja_estava_resolvido():
    fila = [{'ra_num': '81961870', 'casou': ['40U15'], 'nao_cadastrados': [], 'acao': 'resolvido'}]
    _revalidar_nao_cadastrados(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['acao'] == 'resolvido'


# ── 2. vinculou o RA à mão ─────────────────────────────────────────────

def test_vinculacao_manual_tira_o_ra_da_fila():
    """Era o 'trem que não sumia': concluir o amostrador não bastava."""
    _inserir(-4, 'PVC07U97', status='concluido', data_resultado='2026-08-05')
    assert registrar_ra_vinculado(-4, '81963406', 'RA 81963406 - X', '2026-08-05')
    fila = [{'ra_num': '81963406', 'casou': [], 'nao_cadastrados': []}]
    _completar_casou_por_ra_laudos(fila)
    _classificar_acao_resultados(fila)
    assert fila[0]['casou'] == ['PVC07U97']
    assert fila[0]['acao'] == 'resolvido'


def test_vinculacao_manual_nao_sobrescreve_laudo_lido_do_pdf():
    """O laudo extraído tem funcionário/método/resultados — o vínculo à mão não apaga."""
    _inserir(-5, 'TCP4794AV3')
    with get_db() as c:
        _ensure_ra_laudos(c)
        c.execute("INSERT INTO ra_laudos (amostrador_id, amostrador_cod, ra_num, funcionario) "
                  "VALUES (?,?,?,?)", (-5, 'TCP4794AV3', '81962593', 'JOSE DA SILVA'))
    registrar_ra_vinculado(-5, '81962593')
    laudos = _laudos_de(-5)
    assert len(laudos) == 1 and laudos[0]['funcionario'] == 'JOSE DA SILVA'


def test_ra_vazio_nao_grava_nada():
    _inserir(-6, 'SEMRA01')
    assert registrar_ra_vinculado(-6, '') is False
    assert _laudos_de(-6) == []
