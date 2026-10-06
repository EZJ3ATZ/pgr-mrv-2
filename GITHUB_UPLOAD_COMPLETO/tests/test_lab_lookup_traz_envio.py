# -*- coding: utf-8 -*-
"""O índice do inventário do lab traz a data de envio.

O backfill de RAs decide "concluir" por `no_laboratorio(look[codigo])`, que
olha `data_envio_lab`. O índice não trazia a coluna: todo tubo caía em "fora
do lab", o laudo era casado e ninguém era concluído. Em 06/10/2026, 7 tubos com
RA já entregue seguiam no laboratório.
"""
from controle.db import get_db, init_db
from controle.lab_inbox import _norm, _sistema_lookup, no_laboratorio


def _limpa():
    with get_db() as conn:
        conn.execute("DELETE FROM amostradores WHERE codigo LIKE 'LKENV%'")


def setup_function(_):
    init_db()
    _limpa()


def teardown_function(_):
    _limpa()


def test_tubo_no_lab_e_reconhecido_pelo_indice():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO amostradores (codigo, tipo, status, data_envio_lab, data_resultado, arquivado) "
            "VALUES ('LKENV01', 'PVC', 'laboratorio', '2026-08-27', '', 0)")
    amos = _sistema_lookup()[_norm('LKENV01')]
    assert amos['data_envio_lab'] == '2026-08-27'
    assert no_laboratorio(amos)
