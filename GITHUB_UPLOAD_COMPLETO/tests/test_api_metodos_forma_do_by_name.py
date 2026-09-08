# -*- coding: utf-8 -*-
"""Forma de `by_name` em /api/metodos: NOME -> string do CAS, nunca lista.

Registro do invariante que enganou o cliente. `ctrlAplicarMetodo`
(templates/index.html, modal "Dar Baixa em Amostrador") fazia:

    const k = Object.keys(m.by_name).find(k => k.toLowerCase() === agente.toLowerCase());
    if (k) metodo = m.by_name[k][0];

`by_name[k]` é a STRING do CAS, então `[0]` devolvia a primeira LETRA ('P' de
'Poeira Respirável...'), e `metodo.metodoCod`, `.vazao`, `.volume` saíam todos
undefined. O painel de vazão e volume daquele modal nunca achava método nenhum,
em nenhum agente, desde que foi escrito — o técnico via tudo em branco e
preenchia a vazão a mão. Conferido no navegador em 08/09/2026.

O modal continua vivo: `ctrlAbrirBaixa`, `ctrlAbrirBaixaDemand` e
`ctrlAbrirBaixaEmpresa` abrem `ctrl-baixa-overlay`, e há botão "Dar Baixa" na
lista de empresas, na de demandas e na tabela de OS.

A tela passou a pedir o método ao endpoint `/controle/agente/<nome>/estoque`,
que resolve nome sem acento, nome colado pelo navegador e campo com dois
agentes. Este teste guarda a forma do payload: se algum dia `by_name` virar
lista de métodos, a mudança é quebra de contrato e tem de aparecer aqui, não
numa tela em branco.
"""
import json

from app import app


def test_by_name_aponta_para_chave_de_by_cas():
    with app.test_client() as cli:
        r = cli.get('/api/metodos')
    assert r.status_code == 200, r.status_code
    guia = json.loads(r.get_data(as_text=True))
    by_name, by_cas = guia['by_name'], guia['by_cas']
    assert by_name and by_cas

    for nome, valor in by_name.items():
        assert isinstance(valor, str), (nome, type(valor))
        assert valor in by_cas, (nome, valor)


def test_by_cas_e_que_tem_a_lista_de_metodos():
    with app.test_client() as cli:
        guia = json.loads(cli.get('/api/metodos').get_data(as_text=True))
    for cas, entradas in guia['by_cas'].items():
        assert isinstance(entradas, list), (cas, type(entradas))
        for e in entradas:
            assert isinstance(e, dict), (cas, type(e))
            assert 'metodoCod' in e, (cas, sorted(e))


def test_indexar_by_name_com_zero_da_letra_e_nao_metodo():
    """O defeito, congelado: `by_name[k][0]` é caractere, não objeto."""
    with app.test_client() as cli:
        guia = json.loads(cli.get('/api/metodos').get_data(as_text=True))
    nome = next(iter(guia['by_name']))
    primeiro = guia['by_name'][nome][0]
    assert isinstance(primeiro, str) and len(primeiro) == 1, primeiro
