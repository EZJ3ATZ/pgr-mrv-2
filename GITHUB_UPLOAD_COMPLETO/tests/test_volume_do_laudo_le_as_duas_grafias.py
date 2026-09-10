# -*- coding: utf-8 -*-
"""O volume amostrado tem de chegar ao laudo químico nos DOIS bancos.

A coluna nasce `volume_L` no DDL (`db.py`, CREATE TABLE coletas_quimico_amostr).
O **Postgres normaliza identificador sem aspas para minúsculo** e grava
`volume_l`; o **SQLite preserva** e grava `volume_L`. Mesma migração, dois nomes
de coluna — conferido em 10/09/2026 comparando os dois esquemas: em 42 tabelas,
essa é a única divergência.

`get_coleta_quimico` faz `SELECT *` + `row_to_dict`, então as chaves do dict são
exatamente os nomes do banco. `_prefill_quimico` lia só `am.get('volume_L')`, com
L maiúsculo, e dicionário em Python diferencia caixa: **em produção o volume
voltava vazio em todo laudo químico**.

O que fez o defeito durar: rodando o app local em SQLite o campo aparece
preenchido, porque lá a coluna é maiúscula mesmo. O bug só existe do lado que
importa, e é o lado que não se testa. Por isso este teste monta o dicionário na
grafia do **Postgres** — é a única forma de a suíte, que roda em SQLite, cobrir
o caso de produção.

O volume é o número que o guia de métodos cobra (a sílica pede de 400 a 1000 L):
sem ele, o laudo sai sem o dado que prova que a amostra é válida.
"""
import controle.routes as R


def _coleta(chave_do_volume):
    """Coleta química como cada banco devolveria, mudando só a caixa da coluna."""
    return {
        'id': 1, 'empresa_id': 1, 'nome_funcionario': 'Trabalhador Um',
        'funcao': 'Operador', 'setor': 'Produção', 'jornada': '8h',
        'data_coleta': '2026-09-10', 'tempo_exposto': '480', 'acessorios': '',
        'bomba': 'BOMBA-01', 'id_bomba': 'B01', 'id_calibrador': 'ROT-01',
        'amostradores': [{
            'id_amostrador': 'PVC0001', 'substancia': 'Poeira Total',
            'vazao_inicial': 2.0, 'vazao_final': 1.98, 'vazao_media': 1.99,
            'tempo_min': 480.0, chave_do_volume: 955.2,
        }],
    }


def _volume_do_prefill(monkeypatch, chave_do_volume):
    monkeypatch.setattr(R, 'get_coleta_quimico',
                        lambda cid: _coleta(chave_do_volume))
    coleta, res = R._prefill_quimico(1)
    assert coleta, 'a coleta tem de ser encontrada'
    _tipo, dados, _faltam = res
    avaliacoes = dados['avaliacoes']
    assert len(avaliacoes) == 1, avaliacoes
    return avaliacoes[0]['volume']


def test_volume_sai_no_laudo_com_a_coluna_do_postgres(monkeypatch):
    """`volume_l` minúsculo é o que existe em PRODUÇÃO. Era este que falhava."""
    assert _volume_do_prefill(monkeypatch, 'volume_l') == 955.2


def test_volume_sai_no_laudo_com_a_coluna_do_sqlite(monkeypatch):
    """`volume_L` maiúsculo é o que existe no banco local e nos testes."""
    assert _volume_do_prefill(monkeypatch, 'volume_L') == 955.2


def test_volume_ausente_nao_quebra_o_prefill(monkeypatch):
    """Tubo antigo, sem volume gravado: o laudo sai com o campo vazio, não com erro."""
    assert _volume_do_prefill(monkeypatch, 'coluna_que_nao_existe') is None
