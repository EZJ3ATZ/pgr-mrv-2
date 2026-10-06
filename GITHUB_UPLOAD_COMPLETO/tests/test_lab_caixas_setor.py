# -*- coding: utf-8 -*-
"""A varredura do laboratório lê as caixas de setor, não só login de técnico.

Desde 04/08/2026 o lab manda parte dos RAs só para a suporteengenharia@, que
não é login de ninguém. A lista de caixas saía de `usuarios` + engenharia19@,
então 15 RAs (ago-set) nunca entraram e 7 tubos ficaram "no laboratório" com
o resultado já entregue (conferido em 06/10).
"""
from controle.db import init_db
from controle.lab_inbox import MAILBOX, _mailboxes


def test_varredura_inclui_caixa_de_setor_do_lab():
    init_db()
    caixas = [c.lower() for c in _mailboxes()]
    assert 'suporteengenharia@ocupacional.com.br' in caixas
    assert caixas[0] == MAILBOX.lower()
    assert len(caixas) == len(set(caixas))   # sem caixa repetida
