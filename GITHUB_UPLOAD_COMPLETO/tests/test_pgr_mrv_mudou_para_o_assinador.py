# -*- coding: utf-8 -*-
"""O gerador de PGR MRV saiu do portal em 25/09/2026: agora é o /pgr-mrv do Assinador.

As rotas antigas não podem sumir em silêncio: quem ainda tiver a aba aberta (ou um
atalho) recebe 410 com o endereço novo em vez de um 404 sem explicação. E a aba do
PGR no portal vira o aviso com o link do Assinador, sem o formulário antigo.
"""
from app import ASSINADOR_PGR_MRV_URL, app
from controle.db import get_db, init_db


def _cliente_logado():
    init_db()
    with get_db() as conn:
        uid = conn.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()['id']
    cli = app.test_client()
    with cli.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return cli


def test_rotas_antigas_respondem_410_com_o_endereco_novo():
    assert ASSINADOR_PGR_MRV_URL.endswith('/pgr-mrv')
    cli = _cliente_logado()
    for metodo, rota in (('post', '/gerar'), ('post', '/extrair'), ('get', '/ghe/PEDREIRO')):
        r = cli.post(rota, json={}) if metodo == 'post' else cli.get(rota)
        assert r.status_code == 410, (rota, r.status_code)
        assert r.get_json()['url'] == ASSINADOR_PGR_MRV_URL, rota


def test_aba_do_pgr_vira_aviso_com_o_link_do_assinador():
    cli = _cliente_logado()
    html = cli.get('/').get_data(as_text=True)
    i = html.find('id="tab-pgr"')
    assert i >= 0, 'aba do PGR sumiu (o menu e a busca ainda levam a ela)'
    aba = html[i:html.find('id="tab-calor"', i)]
    assert ASSINADOR_PGR_MRV_URL in aba, 'aviso sem o link do Assinador'
    assert 'drop-zone' not in aba, 'formulário antigo ainda na aba'
    assert 'gerarPGR' not in html and "fetch('/gerar'" not in html and "fetch('/extrair'" not in html
