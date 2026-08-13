# -*- coding: utf-8 -*-
"""Log de atividade humana: cada clique e cada mexida de cada pessoa.

Por que existe separado do perf_log: requisição HTTP **não é uso**. A aba aberta
fica chamando `/controle/eventos` sozinha a cada minuto, e isso fazia o painel
dizer que a pessoa estava usando o sistema quando ela só tinha a aba parada —
mesmo defeito do "abriu a proposta" que contava robô no CRM. Clique é humano;
requisição não é.

Guarda o RÓTULO do elemento, nunca o que a pessoa digitou.
Leitura: admin. Escrita: qualquer pessoa logada (só grava a própria atividade,
com o ator vindo da sessão, nunca do corpo do pedido).
"""
import threading
import time

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS atividade_log (
    id          SERIAL PRIMARY KEY,
    criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usuario_id  INTEGER,
    usuario     TEXT,
    tipo        TEXT,
    rotulo      TEXT,
    alvo        TEXT,
    tela        TEXT
);
CREATE INDEX IF NOT EXISTS idx_ativ_criado  ON atividade_log(criado_em);
CREATE INDEX IF NOT EXISTS idx_ativ_usuario ON atividade_log(usuario_id, criado_em);
CREATE INDEX IF NOT EXISTS idx_ativ_tela    ON atividade_log(tela);
"""

SCHEMA_SQLITE = SCHEMA_PG.replace('SERIAL PRIMARY KEY',
                                  'INTEGER PRIMARY KEY AUTOINCREMENT')

TIPOS = ('clique', 'tela', 'envio', 'busca', 'atalho')
MAX_LOTE = 80            # por chamada
TETO_HORA = 4000         # por pessoa/hora — clique é frequente, mas não infinito
GUARDA_DIAS = 60         # mais que o perf_log: aqui é rastro de gente, não de máquina
_PODA_CADA_S = 3600

_tabela_ok = False
_ultima_poda = 0.0
_lock = threading.Lock()


def garantir_tabela():
    global _tabela_ok
    if _tabela_ok:
        return True
    try:
        from .db import get_db, USE_PG
        with get_db() as conn:
            conn.executescript(SCHEMA_PG if USE_PG else SCHEMA_SQLITE)
        _tabela_ok = True
    except Exception as e:
        print(f'[atividade] tabela não criada: {e}')
    return _tabela_ok


def _podar_se_na_hora():
    global _ultima_poda
    agora = time.time()
    with _lock:
        if agora - _ultima_poda < _PODA_CADA_S:
            return
        _ultima_poda = agora
    try:
        from .db import get_db, USE_PG
        with get_db() as conn:
            if USE_PG:
                conn.execute("DELETE FROM atividade_log WHERE criado_em < "
                             "NOW() - INTERVAL '%d days'" % GUARDA_DIAS)
            else:
                conn.execute("DELETE FROM atividade_log WHERE criado_em < "
                             "datetime('now', '-%d days')" % GUARDA_DIAS)
    except Exception:
        pass


def registrar_lote(eventos, usuario_id=None, usuario=None):
    """Grava um lote vindo do navegador. Devolve quantos entraram.

    O ator vem SEMPRE de quem está logado (parâmetro resolvido no handler),
    nunca do corpo — o navegador não decide de quem é a atividade.
    """
    if not garantir_tabela():
        return 0
    if not isinstance(eventos, list) or not eventos:
        return 0

    from .db import get_db

    # freio: pessoa não passa de TETO_HORA por hora
    try:
        with get_db() as conn:
            r = conn.execute(
                'SELECT COUNT(*) AS c FROM atividade_log WHERE usuario_id = ? '
                "AND criado_em > " + ("NOW() - INTERVAL '1 hour'"
                                      if _usa_pg() else "datetime('now','-1 hour')"),
                (usuario_id,)).fetchone()
        if r and (r['c'] or 0) > TETO_HORA:
            return 0
    except Exception:
        pass

    limpos = []
    for e in eventos[:MAX_LOTE]:
        if not isinstance(e, dict):
            continue
        tipo = str(e.get('tipo') or 'clique')[:20]
        if tipo not in TIPOS:
            continue
        limpos.append((
            usuario_id, usuario,
            tipo,
            (str(e.get('rotulo') or '')[:160]) or None,
            (str(e.get('alvo') or '')[:160]) or None,
            (str(e.get('tela') or '')[:120]) or None,
        ))
    if not limpos:
        return 0

    try:
        with get_db() as conn:
            for linha in limpos:
                conn.execute(
                    'INSERT INTO atividade_log (usuario_id, usuario, tipo, rotulo, '
                    'alvo, tela, criado_em) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                    linha)
    except Exception as e:
        print(f'[atividade] falha ao gravar {len(limpos)}: {e}')
        return 0
    _podar_se_na_hora()
    return len(limpos)


def _usa_pg():
    from .db import USE_PG
    return USE_PG


def _janela(dias, col='criado_em'):
    from .db import USE_PG
    if USE_PG:
        return "%s >= NOW() - INTERVAL '%d days'" % (col, int(dias))
    return "%s >= datetime('now', '-%d days')" % (col, int(dias))


def por_pessoa(dias=30):
    """Contagem por pessoa: cliques, telas abertas, envios."""
    from .db import get_db, USE_PG
    if not garantir_tabela():
        return {}
    filtro = ("COUNT(*) FILTER (WHERE tipo = '%s')" if USE_PG
              else "SUM(CASE WHEN tipo = '%s' THEN 1 ELSE 0 END)")
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT usuario_id,
                   COUNT(*)                       AS total,
                   {filtro % 'clique'}            AS cliques,
                   {filtro % 'tela'}              AS telas,
                   {filtro % 'envio'}             AS envios,
                   MAX(criado_em)                 AS ultimo
              FROM atividade_log
             WHERE {_janela(dias)} AND usuario_id IS NOT NULL
             GROUP BY usuario_id""").fetchall()
    return {int(dict(r)['usuario_id']): dict(r) for r in rows}


def recentes(dias=7, usuario_id=None, tela=None, tipo=None, limite=200):
    """Rastro cronológico — o 'todos os cliques' de fato."""
    from .db import get_db
    if not garantir_tabela():
        return []
    where = [_janela(dias, 'a.criado_em')]
    params = []
    if usuario_id:
        where.append('a.usuario_id = ?'); params.append(int(usuario_id))
    if tela:
        where.append('a.tela = ?'); params.append(tela)
    if tipo:
        where.append('a.tipo = ?'); params.append(tipo)
    sql = f"""
        SELECT a.criado_em, a.tipo, a.rotulo, a.alvo, a.tela,
               a.usuario_id, COALESCE(u.nome, a.usuario) AS pessoa
          FROM atividade_log a
          LEFT JOIN usuarios u ON u.id = a.usuario_id
         WHERE {' AND '.join(where)}
         ORDER BY a.criado_em DESC
         LIMIT {int(min(limite, 500))}"""
    with get_db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    saida = []
    for r in rows:
        d = dict(r)
        try:
            d['criado_em'] = d['criado_em'].isoformat(sep=' ')
        except AttributeError:
            pass
        saida.append(d)
    return saida


def mais_clicados(dias=7, limite=20):
    """O que mais recebe clique — diz onde o sistema é realmente usado."""
    from .db import get_db
    if not garantir_tabela():
        return []
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT tela, rotulo, COUNT(*) AS n,
                   COUNT(DISTINCT usuario_id) AS pessoas
              FROM atividade_log
             WHERE {_janela(dias)} AND tipo = 'clique' AND rotulo IS NOT NULL
             GROUP BY tela, rotulo
             ORDER BY COUNT(*) DESC
             LIMIT {int(limite)}""").fetchall()
    return [dict(r) for r in rows]
