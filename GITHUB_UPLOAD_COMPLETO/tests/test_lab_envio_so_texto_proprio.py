# -*- coding: utf-8 -*-
"""Envio ao laboratório: só conta código que a pessoa ESCREVEU, não o citado.

02/09/2026: a resposta ao e-mail "Amostradores pendentes de retorno a mais de
30 dias" levava, no histórico citado, a lista de 63 códigos que o próprio lab
mandou. O sync lia o `body` inteiro e carimbou data de envio em 60 tubos que
estavam na prateleira; quando o sync voltou a gravar (06/10), viraram 45
alertas de "atrasado no laboratório".
"""
import controle.lab_inbox as lab

LOOK = {'TCP3046AV3': {'id': 1, 'codigo': 'TCP3046AV3'},
        'TCP2134AV3': {'id': 2, 'codigo': 'TCP2134AV3'}}


def _msg(unique, citado='', anexo=False):
    return {
        'id': 'm1', 'subject': 'RE: Amostradores pendentes de retorno a mais de 30 dias',
        'toRecipients': [{'emailAddress': {'address': 'resultados@uniscientificgroup.com.br'}}],
        'sentDateTime': '2026-09-02T13:20:00Z', 'hasAttachments': anexo,
        'uniqueBody': {'content': unique},
        'body': {'content': unique + citado},
    }


def _rodar(monkeypatch, msg):
    pedidos = []

    def fake_get(path):
        pedidos.append(path)
        if 'sentitems' in path:
            return {'value': [msg]}
        return {'value': []}
    monkeypatch.setattr(lab, 'graph_get', fake_get)
    monkeypatch.setattr(lab, '_codigos_no_texto',
                        lambda txt, look: [c for c in look if c in (txt or '')])
    return lab._fetch_sent_to_lab(['engenharia7@ocupacional.com.br'], LOOK), pedidos


def test_codigo_so_no_historico_citado_nao_e_envio(monkeypatch):
    msg = _msg('Bom dia, segue conferência.',
               citado='<hr>De: lab ... pendentes: TCP3046AV3 TCP2134AV3')
    out, pedidos = _rodar(monkeypatch, msg)
    assert out == []
    assert 'uniqueBody' in pedidos[0]


def test_codigo_escrito_pela_pessoa_conta(monkeypatch):
    out, _ = _rodar(monkeypatch, _msg('Seguem para análise: TCP3046AV3'))
    assert out and out[0]['codigos'] == ['TCP3046AV3'] and out[0]['data'] == '2026-09-02'
