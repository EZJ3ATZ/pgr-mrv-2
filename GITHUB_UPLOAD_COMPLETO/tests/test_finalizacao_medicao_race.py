# -*- coding: utf-8 -*-
"""_baixar_medicao_pendente: finalizar a MESMA medição 2× em paralelo não pode
gravar/baixar duas vezes (TOCTOU/CWE-362).

Duas planilhas de campo da mesma OS+tipo finalizando ao mesmo tempo: ambas leem
a medição como 'pendente' (SELECT), mas só uma pode marcá-la 'realizado'. O
UPDATE agora re-afirma o estado no WHERE (padrão do commit 0ad9508); quem perde
a corrida afeta 0 linhas (rowcount 0) e é tratado como 'duplicada' em vez de
gravar coleta em dobro.
"""
from contextlib import contextmanager
from controle import routes as routes_mod
from controle.db import get_db, init_db, row_to_dict


def _seed_os_com_medicao(agente='Ruído', os_num='RACEOS1'):
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM demandas WHERE numero_os=?", (os_num,))
        emp = conn.execute("INSERT INTO empresas (nome) VALUES ('EMP RACE')").lastrowid
        cur = conn.execute(
            "INSERT INTO demandas (titulo, numero_os, status, empresa_id) "
            "VALUES ('OS race', ?, 'aberta', ?)", (os_num, emp))
        dem = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO medicoes (demanda_id, agente, status, qtd_pontos_prevista, qtd_pontos_feita) "
            "VALUES (?, ?, 'pendente', 2, 0)", (dem, agente))
        mid = cur.lastrowid
    return dem, mid


def _status_medicao(mid):
    with get_db() as conn:
        row = conn.execute("SELECT status, qtd_pontos_feita FROM medicoes WHERE id=?", (mid,)).fetchone()
        return row_to_dict(row)


def test_baixa_normal_finaliza_a_medicao():
    dem, mid = _seed_os_com_medicao(os_num='RACEOS0')
    res = routes_mod._baixar_medicao_pendente(dem, 'ruido')
    assert res['baixada'] == mid and not res['duplicada']
    assert _status_medicao(mid)['status'] == 'realizado'


def test_corrida_perdedora_vira_duplicada_sem_regravar(monkeypatch):
    """Injeta a corrida no ponto exato: quando _baixar_medicao_pendente vai rodar
    o UPDATE condicional, OUTRA planilha já finalizou a mesma medição. O WHERE
    condicional afeta 0 linhas -> a função retorna duplicada (não baixa de novo)."""
    dem, mid = _seed_os_com_medicao(os_num='RACEOS2')
    real_get_db = routes_mod.get_db
    injected = {'done': False}

    @contextmanager
    def racing_get_db():
        with real_get_db() as conn:
            class Wrap:
                def execute(self, sql, params=None):
                    low = ' '.join(sql.lower().split())
                    # No UPDATE condicional da medição (novo WHERE), simula a outra
                    # planilha vencendo a corrida ANTES de nós escrevermos.
                    if (not injected['done'] and 'update medicoes' in low
                            and 'status not in' in low):
                        injected['done'] = True
                        conn.execute("UPDATE medicoes SET status='realizado', qtd_pontos_feita=2 WHERE id=?", (mid,))
                    return conn.execute(sql, params) if params is not None else conn.execute(sql)
                def __getattr__(self, name):
                    return getattr(conn, name)
            yield Wrap()

    monkeypatch.setattr(routes_mod, 'get_db', racing_get_db)
    res = routes_mod._baixar_medicao_pendente(dem, 'ruido')
    assert injected['done'], 'a corrida deveria ter sido injetada no UPDATE condicional'
    assert res['duplicada'] is True and res['baixada'] is None
    # a medição continua finalizada uma única vez (não regravada por nós)
    assert _status_medicao(mid)['status'] == 'realizado'
