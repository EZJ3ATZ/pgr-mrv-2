# -*- coding: utf-8 -*-
"""Tempo de entrega do laboratório: envio da amostra → resultado (RA).

Levantado pelo Matheus em 06/08/2026 ("já podemos medir o tempo de entrega do
laboratório?"). Existiam duas medidas e nenhuma era a do lab:

- `tempo_coleta_lab` (campo → despacho) é NOSSO tempo;
- `tempo_lab_concluido` (envio → conclusão) carrega a nossa demora em FECHAR o
  amostrador — e era esse o número que a tela do BI mostrava sob o rótulo
  "Lab → Resultado", inflando o laboratório em ~14 dias (31d no lugar de ~16d).

`tempo_lab_resultado` é a régua para cobrar o laboratório: mediana e p90, porque a
distribuição tem cauda longa (um laudo real levou 92 dias).
"""
import pytest

from controle.db import get_db, init_db, resumo_dias, stats_amostradores_fluxo


# ── o cálculo, sem banco ───────────────────────────────────────────────

def test_mediana_nao_e_arrastada_pela_cauda():
    """O laudo de 92 dias não pode virar a régua: média 32, mediana 14."""
    r = resumo_dias([10, 12, 14, 92])
    assert r['mediana_dias'] == 14
    assert r['media_dias'] == 32.0
    assert r['p90_dias'] == 92 and r['max_dias'] == 92
    assert r['amostra'] == 4


def test_resumo_ignora_none_e_ordena():
    r = resumo_dias([30, None, 5, 10])
    assert r['amostra'] == 3 and r['mediana_dias'] == 10 and r['max_dias'] == 30


def test_resumo_sem_amostra_e_none():
    """Devolver zero fingiria medição onde não há — a tela mostra '—'."""
    assert resumo_dias([]) is None
    assert resumo_dias([None]) is None


def test_resumo_de_um_unico_valor():
    r = resumo_dias([16])
    assert r == {'media_dias': 16.0, 'mediana_dias': 16, 'p90_dias': 16,
                 'max_dias': 16, 'amostra': 1}


# ── o que entra na conta (banco) ───────────────────────────────────────

def _limpar():
    with get_db() as c:
        c.execute('DELETE FROM amostradores WHERE id < 0')


@pytest.fixture(autouse=True)
def banco():
    """O banco de teste vem semeado com amostradores reais, então cada teste mede o
    DELTA da amostra — nunca apaga a tabela (outros testes dependem do seed).

    A conexão NÃO fica aberta: `stats_amostradores_fluxo` abre a sua, e no SQLite
    ela não veria um INSERT ainda não commitado.
    """
    init_db()
    _limpar()
    yield
    _limpar()


def _inserir(id_, envio, resultado, arquivado=0, conclusao=None):
    with get_db() as c:
        c.execute(
            'INSERT INTO amostradores (id, codigo, tipo, status, data_envio_lab, '
            'data_resultado, data_conclusao, arquivado) VALUES (?,?,?,?,?,?,?,?)',
            (id_, f'ZZ{abs(id_)}', 'TCP', 'concluido', envio, resultado, conclusao,
             arquivado))


def _amostra():
    return (stats_amostradores_fluxo().get('tempo_lab_resultado') or {}).get('amostra') or 0


def test_conta_envio_ate_resultado():
    n0 = _amostra()
    _inserir(-1, '2026-07-01', '2026-07-15')
    assert _amostra() == n0 + 1


def test_arquivado_tambem_conta():
    """Concluído há mais de 30 dias é arquivado; ignorá-lo jogaria fora justamente
    o histórico de quem já fechou o ciclo."""
    n0 = _amostra()
    _inserir(-2, '2026-01-10', '2026-01-25', arquivado=1)
    assert _amostra() == n0 + 1


def test_sem_uma_das_datas_nao_entra():
    n0 = _amostra()
    _inserir(-3, '2026-07-01', None)
    _inserir(-4, None, '2026-07-15')
    _inserir(-5, '', '')
    assert _amostra() == n0


def test_resultado_antes_do_envio_nao_entra():
    """Data invertida é erro de lançamento — entraria como dia negativo e baixaria
    a média do laboratório de graça."""
    n0 = _amostra()
    _inserir(-6, '2026-07-20', '2026-07-01')
    assert _amostra() == n0


def test_a_metrica_do_lab_nao_e_a_de_conclusao():
    """Mede coisas diferentes: fechar o amostrador é trabalho nosso, não do lab."""
    _inserir(-7, '2026-06-01', '2026-06-15', conclusao='2026-07-15')
    fx = stats_amostradores_fluxo()
    assert fx['tempo_lab_resultado']['amostra'] >= 1
    # o par (14d de lab, 44d até fechar) existe no banco — as duas medidas divergem
    assert fx['tempo_lab_concluido']['media_dias'] > fx['tempo_lab_resultado']['media_dias']
