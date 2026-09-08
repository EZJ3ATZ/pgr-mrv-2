# -*- coding: utf-8 -*-
"""Backfill das coletas químicas antigas: corrige o dado JÁ GRAVADO.

O código foi corrigido em 08/09/2026, mas as linhas gravadas antes continuam no
banco com o valor velho. Medido em produção no ensaio (somente leitura):

  - 15 de 41 linhas com `tipo_amostrador` divergente do cadastro do tubo
    (10 com 'TCP' fixo do default antigo da tela, 5 com o texto inteiro do guia)
  - 39 de 41 com `tempo_min` e `volume_L` zerados

As duas correções são DERIVADAS de fonte melhor que já está no banco — o tipo
sai do cadastro do amostrador, o tempo sai das horas gravadas — então rodar de
novo dá o mesmo resultado. Isso importa porque `_migrate` roda a cada deploy
que muda a impressão digital do schema.

🔴 O ensaio pegou uma linha que NÃO pode ser preenchida: a id 19 de produção
(tubo PVC94117, dióxido de titânio, 16/07) tem `vazao_final` 19974 L/min — erro
de digitação — e `vazao_media` 9988. Volume derivado disso daria 679.184 L, um
número absurdo com aparência de dado. Nessas o tempo entra (sai das horas, é
confiável) e o volume fica zerado, para o técnico corrigir a vazão na tela.
"""
from controle.db import (_VAZAO_MAX_PLAUSIVEL, _backfill_coleta_quimico,
                         get_db, init_db, row_to_dict)


def _linha(conn, cid, cod, tipo, h1, h2, vm, tempo=0, vol=0, intervalos=''):
    conn.execute(
        "INSERT INTO coletas_quimico_amostr "
        "(coleta_id, seq, id_amostrador, tipo_amostrador, substancia, "
        " vazao_media, hora_inicio, hora_final, intervalos, tempo_min, volume_L) "
        "VALUES (?, 1, ?, ?, 'Sílica Cristalina', ?, ?, ?, ?, ?, ?)",
        (cid, cod, tipo, vm, h1, h2, intervalos, tempo, vol))
    return conn.execute(
        'SELECT id FROM coletas_quimico_amostr WHERE coleta_id=? ORDER BY id DESC '
        'LIMIT 1', (cid,)).fetchone()


def _montar(cenarios):
    """Semeia tubos + uma coleta com uma linha por cenário. Devolve os ids."""
    init_db()
    ids = {}
    with get_db() as conn:
        cur = conn.execute("INSERT INTO empresas (nome) VALUES ('ZBACKFILL')")
        eid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO coletas_quimico "
            "(empresa_id, empresa_nome, data_coleta, substancias, nome_funcionario, status) "
            "VALUES (?, 'ZBACKFILL', '2026-07-01', 'Sílica Cristalina', 'Fulano', 'concluida')",
            (eid,))
        cid = cur.lastrowid
        for nome, (cod, tipo_cad, tipo_col, h1, h2, vm) in cenarios.items():
            if tipo_cad:
                conn.execute(
                    "INSERT INTO amostradores (codigo, tipo, status, arquivado) "
                    "VALUES (?, ?, 'disponivel', 0)", (cod, tipo_cad))
            r = _linha(conn, cid, cod, tipo_col, h1, h2, vm)
            ids[nome] = (r['id'] if hasattr(r, 'keys') else r[0])
    return cid, eid, ids


def _ler(ids):
    with get_db() as conn:
        out = {}
        for nome, i in ids.items():
            r = conn.execute(
                'SELECT tipo_amostrador, tempo_min, volume_L FROM '
                'coletas_quimico_amostr WHERE id=?', (i,)).fetchone()
            out[nome] = row_to_dict(r)
        return out


def _limpar(cid, eid, ids, codigos):
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
        conn.execute('DELETE FROM coletas_quimico WHERE id=?', (cid,))
        conn.execute('DELETE FROM empresas WHERE id=?', (eid,))
        for c in codigos:
            conn.execute('DELETE FROM amostradores WHERE codigo=?', (c,))


CENARIOS = {
    # nome:            (codigo,        tipo_cad, tipo_col,               h1,      h2,      vazao)
    'tipo_tcp_fixo':   ('ZBF-PVC12',   'PVC',    'TCP',                  '09:10', '11:50', 2.5),
    'texto_do_guia':   ('ZBF-EC98',    'EC',     'SKC 225-5 (EC*****)',  '14:45', '15:45', 2.01),
    'fora_do_cadastro':('ZBF-SOLTO1',  None,     'IOL',                  '08:00', '12:00', 2.0),
    'vazao_impossivel':('ZBF-RUIM1',   'PVC',    'PVC',                  '13:50', '14:58', 9988.006),
    'sem_hora':        ('ZBF-SEMH1',   'PVC',    'PVC',                  '',      '',      2.5),
}


def test_backfill_corrige_e_e_idempotente():
    cid, eid, ids = _montar(CENARIOS)
    try:
        with get_db() as conn:
            _backfill_coleta_quimico(conn)
        d1 = _ler(ids)

        # 1) tipo vem do cadastro do tubo
        assert d1['tipo_tcp_fixo']['tipo_amostrador'] == 'PVC', d1['tipo_tcp_fixo']
        assert d1['texto_do_guia']['tipo_amostrador'] == 'EC', d1['texto_do_guia']
        # tubo fora do cadastro: nao ha fonte melhor, mantem o que estava
        assert d1['fora_do_cadastro']['tipo_amostrador'] == 'IOL', d1['fora_do_cadastro']

        # 2) tempo e volume saem das horas
        assert d1['tipo_tcp_fixo']['tempo_min'] == 160, d1['tipo_tcp_fixo']
        assert round(d1['tipo_tcp_fixo']['volume_L'], 1) == 400.0, d1['tipo_tcp_fixo']
        assert d1['texto_do_guia']['tempo_min'] == 60, d1['texto_do_guia']
        assert round(d1['texto_do_guia']['volume_L'], 1) == 120.6, d1['texto_do_guia']

        # 3) vazao impossivel: tempo entra, volume NAO
        assert d1['vazao_impossivel']['tempo_min'] == 68, d1['vazao_impossivel']
        assert (d1['vazao_impossivel']['volume_L'] or 0) == 0, d1['vazao_impossivel']

        # 4) sem hora nenhuma continua zerado, sem inventar
        assert (d1['sem_hora']['tempo_min'] or 0) == 0, d1['sem_hora']
        assert (d1['sem_hora']['volume_L'] or 0) == 0, d1['sem_hora']

        # 5) rodar de novo nao muda nada — `_migrate` roda a cada deploy
        with get_db() as conn:
            _backfill_coleta_quimico(conn)
        assert _ler(ids) == d1, 'backfill nao e idempotente'
    finally:
        _limpar(cid, eid, ids, [c for c, *_ in CENARIOS.values()])


def test_teto_de_vazao_plausivel_e_o_do_asbesto():
    """16 L/min e a maior vazao REAL da guia (asbesto, ABNT NBR 13.158/94).

    A primeira versao desta guarda usava 5 L/min e reprovava o asbesto, que e
    metodo legitimo. O numero nao e chute: saiu do catalogo.
    """
    assert _VAZAO_MAX_PLAUSIVEL == 16.0
