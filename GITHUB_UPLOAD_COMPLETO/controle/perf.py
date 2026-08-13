# -*- coding: utf-8 -*-
"""Log de tempo de resposta de cada interação do portal.

Mede o que a PESSOA sente: quanto cada requisição HTTP demorou, quanto disso foi
banco e quantas consultas o pedido disparou. É telemetria — nunca levanta exceção,
nunca atrasa a resposta de forma perceptível, nunca bloqueia a operação.

Complementa a tabela `eventos` (que diz O QUE aconteceu, não quanto demorou).
Leitura: apenas admin, dentro de "Saúde do Sistema" (/controle/admin/saude).
"""
import os
import threading
import time
from datetime import datetime, timedelta

# ── Tabela ─────────────────────────────────────────────────────────────
# TEXT em criado_em para casar com o resto do schema (SQLite e PG convivem).
SCHEMA_PERF_PG = """
CREATE TABLE IF NOT EXISTS perf_log (
    id          SERIAL PRIMARY KEY,
    criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rota        TEXT,
    metodo      TEXT,
    path        TEXT,
    status      INTEGER,
    duracao_ms  INTEGER,
    ms_banco    INTEGER,
    consultas   INTEGER,
    bytes_resp  INTEGER,
    usuario     TEXT,
    usuario_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_perf_criado ON perf_log(criado_em);
CREATE INDEX IF NOT EXISTS idx_perf_rota   ON perf_log(rota, criado_em);
CREATE INDEX IF NOT EXISTS idx_perf_lentas ON perf_log(duracao_ms);
"""

SCHEMA_PERF_SQLITE = SCHEMA_PERF_PG.replace('SERIAL PRIMARY KEY',
                                            'INTEGER PRIMARY KEY AUTOINCREMENT')

# ── Estado do processo ─────────────────────────────────────────────────
LOTE          = 15        # grava quando junta 15 medições…
INTERVALO_S   = 60        # …ou quando a mais antiga passa de 60 s
TETO_BUFFER   = 200       # trava: se o banco cair, não cresce sem limite
GUARDA_DIAS   = 30        # poda automática
_PODA_CADA_S  = 3600      # no máximo 1 poda por hora por processo

_buffer = []
_lock = threading.Lock()
_primeiro_em = None
_tabela_ok = False
_ultima_poda = 0.0
_desligado = os.environ.get('PERF_LOG', '1') == '0'   # válvula de escape

# Rotas que não interessam medir (ruído puro)
_IGNORAR_PREFIXO = ('/static/', '/sw.js', '/favicon', '/health', '/manifest.json')


def _agora():
    return time.time()


def garantir_tabela():
    """Cria a tabela uma vez por processo. Silencioso em caso de falha."""
    global _tabela_ok
    if _tabela_ok:
        return True
    try:
        from .db import get_db, USE_PG, _add_col
        with get_db() as conn:
            conn.executescript(SCHEMA_PERF_PG if USE_PG else SCHEMA_PERF_SQLITE)
            # colunas que nasceram depois da tabela (CREATE IF NOT EXISTS não add)
            _add_col(conn, 'perf_log', 'usuario_id', 'INTEGER')
        _tabela_ok = True
    except Exception as e:
        print(f'[perf] tabela não criada: {e}')
    return _tabela_ok


# ── Gravação em lote ───────────────────────────────────────────────────

def _gravar(linhas):
    from .db import get_db
    with get_db() as conn:
        for l in linhas:
            conn.execute(
                'INSERT INTO perf_log (rota,metodo,path,status,duracao_ms,'
                'ms_banco,consultas,bytes_resp,usuario,usuario_id,criado_em) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                (l['rota'], l['metodo'], l['path'], l['status'], l['duracao_ms'],
                 l['ms_banco'], l['consultas'], l['bytes_resp'], l['usuario'],
                 l.get('usuario_id')))


def _podar_se_na_hora():
    """Poda oportunista: no máximo 1x por hora por processo. Aqui o volume é
    alto (1 linha por requisição), ao contrário da tabela `eventos`."""
    global _ultima_poda
    agora = _agora()
    if agora - _ultima_poda < _PODA_CADA_S:
        return
    _ultima_poda = agora
    try:
        from .db import get_db, USE_PG
        with get_db() as conn:
            if USE_PG:
                conn.execute("DELETE FROM perf_log WHERE criado_em < "
                             "NOW() - INTERVAL '%d days'" % GUARDA_DIAS)
            else:
                conn.execute("DELETE FROM perf_log WHERE criado_em < "
                             "datetime('now', '-%d days')" % GUARDA_DIAS)
    except Exception:
        pass


def _descarregar(forcar=False):
    global _buffer, _primeiro_em
    with _lock:
        if not _buffer:
            return
        cheio = len(_buffer) >= LOTE
        velho = _primeiro_em is not None and (_agora() - _primeiro_em) >= INTERVALO_S
        if not (forcar or cheio or velho):
            return
        lote, _buffer, _primeiro_em = _buffer, [], None
    try:
        if garantir_tabela():
            _gravar(lote)
            _podar_se_na_hora()
    except Exception as e:
        print(f'[perf] falha ao gravar {len(lote)} medições: {e}')


def registrar(rota, metodo, path, status, duracao_ms, ms_banco, consultas,
              bytes_resp, usuario, usuario_id=None):
    global _primeiro_em
    with _lock:
        if len(_buffer) >= TETO_BUFFER:
            return
        if _primeiro_em is None:
            _primeiro_em = _agora()
        _buffer.append({
            'rota': (rota or '?')[:120], 'metodo': (metodo or '')[:10],
            'path': (path or '')[:200], 'status': status,
            'duracao_ms': duracao_ms, 'ms_banco': ms_banco,
            'consultas': consultas, 'bytes_resp': bytes_resp,
            'usuario': (usuario or '')[:120] or None,
            'usuario_id': usuario_id,
        })
    _descarregar()


# ── Ganchos do Flask ───────────────────────────────────────────────────

def init_app(app):
    """Liga a medição em TODAS as rotas (app + blueprints), sem tocar em view."""
    if _desligado:
        print('[perf] desligado por PERF_LOG=0')
        return

    from flask import g, request

    @app.before_request
    def _perf_inicio():
        try:
            from .db import perf_zerar
            perf_zerar()
            g._perf_t0 = time.perf_counter()
        except Exception:
            pass
        return None

    @app.after_request
    def _perf_fim(resp):
        try:
            t0 = getattr(g, '_perf_t0', None)
            if t0 is None:
                return resp
            path = request.path or ''
            if path.startswith(_IGNORAR_PREFIXO):
                return resp
            dur = int((time.perf_counter() - t0) * 1000)

            from .db import perf_ler
            n_consultas, ms_banco = perf_ler()

            usuario, usuario_id = None, None
            try:
                from flask_login import current_user
                if current_user.is_authenticated:
                    usuario = getattr(current_user, 'email', None) or \
                              getattr(current_user, 'nome', None)
                    # o ID é a chave que presta; o texto é só para ler
                    try:
                        usuario_id = int(current_user.id)
                    except Exception:
                        usuario_id = None
            except Exception:
                pass

            tamanho = None
            try:
                tamanho = resp.calculate_content_length()
            except Exception:
                pass

            registrar(request.endpoint or path, request.method, path,
                      resp.status_code, dur, int(ms_banco), n_consultas,
                      tamanho, usuario, usuario_id)
        except Exception:
            pass   # telemetria nunca quebra a resposta
        return resp

    print('[perf] medição de tempo de resposta ativa')


# ── Leitura (admin) ────────────────────────────────────────────────────

def _janela_sql(dias):
    from .db import USE_PG
    if USE_PG:
        return "criado_em >= NOW() - INTERVAL '%d days'" % int(dias)
    return "criado_em >= datetime('now', '-%d days')" % int(dias)


def kpis(dias=7):
    """Uma linha, calculada NO BANCO (nunca agregar amostra no cliente)."""
    from .db import get_db, USE_PG
    if not garantir_tabela():
        return {}
    w = _janela_sql(dias)
    with get_db() as conn:
        if USE_PG:
            r = conn.execute(f"""
                SELECT COUNT(*) AS requisicoes,
                       PERCENTILE_DISC(0.5)  WITHIN GROUP (ORDER BY duracao_ms) AS p50_ms,
                       PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY duracao_ms) AS p95_ms,
                       MAX(duracao_ms) AS pior_ms,
                       COUNT(*) FILTER (WHERE duracao_ms >= 1000) AS acima_1s,
                       COUNT(*) FILTER (WHERE duracao_ms >= 3000) AS acima_3s,
                       COUNT(*) FILTER (WHERE status >= 500) AS erros,
                       COUNT(DISTINCT usuario) AS pessoas,
                       ROUND(AVG(consultas)::numeric, 1) AS consultas_media,
                       SUM(ms_banco) AS ms_banco_total,
                       SUM(duracao_ms) AS ms_total
                  FROM perf_log WHERE {w}""").fetchone()
        else:
            r = conn.execute(f"""
                SELECT COUNT(*) AS requisicoes, NULL AS p50_ms, NULL AS p95_ms,
                       MAX(duracao_ms) AS pior_ms,
                       SUM(CASE WHEN duracao_ms >= 1000 THEN 1 ELSE 0 END) AS acima_1s,
                       SUM(CASE WHEN duracao_ms >= 3000 THEN 1 ELSE 0 END) AS acima_3s,
                       SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) AS erros,
                       COUNT(DISTINCT usuario) AS pessoas,
                       ROUND(AVG(consultas), 1) AS consultas_media,
                       SUM(ms_banco) AS ms_banco_total,
                       SUM(duracao_ms) AS ms_total
                  FROM perf_log WHERE {w}""").fetchone()
    d = dict(r) if r else {}
    total = d.get('ms_total') or 0
    banco = d.get('ms_banco_total') or 0
    d['pct_banco'] = round(banco / total * 100, 1) if total else None
    return d


def por_rota(dias=7, limite=25):
    """Agrupado por rota, do maior tempo TOTAL para o menor — é o total que diz
    onde o sistema gasta a vida, não a média."""
    from .db import get_db, USE_PG
    if not garantir_tabela():
        return []
    w = _janela_sql(dias)
    pct = ("PERCENTILE_DISC(0.5)  WITHIN GROUP (ORDER BY duracao_ms) AS p50_ms, "
           "PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY duracao_ms) AS p95_ms, ") \
        if USE_PG else "NULL AS p50_ms, NULL AS p95_ms, "
    acima = ("COUNT(*) FILTER (WHERE duracao_ms >= 1000)") if USE_PG else \
            ("SUM(CASE WHEN duracao_ms >= 1000 THEN 1 ELSE 0 END)")
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT rota, metodo, COUNT(*) AS chamadas, {pct}
                   MAX(duracao_ms) AS pior_ms,
                   ROUND(AVG(duracao_ms)) AS media_ms,
                   SUM(duracao_ms) AS total_ms,
                   ROUND(AVG(ms_banco)) AS media_banco_ms,
                   ROUND(AVG(consultas), 1) AS consultas_media,
                   MAX(consultas) AS consultas_pior,
                   ROUND(AVG(bytes_resp)) AS bytes_media,
                   {acima} AS acima_1s
              FROM perf_log WHERE {w}
             GROUP BY rota, metodo
             ORDER BY SUM(duracao_ms) DESC
             LIMIT {int(limite)}""").fetchall()
    return [dict(r) for r in rows]


def lentas(dias=7, min_ms=1000, limite=25):
    from .db import get_db
    if not garantir_tabela():
        return []
    w = _janela_sql(dias)
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT criado_em, rota, metodo, path, status, duracao_ms,
                   ms_banco, consultas, usuario
              FROM perf_log
             WHERE {w} AND duracao_ms >= ?
             ORDER BY duracao_ms DESC LIMIT {int(limite)}""",
            (int(min_ms),)).fetchall()
    return [dict(r) for r in rows]


def consultas_do_banco(limite=20):
    """Visão do próprio Postgres (pg_stat_statements) — retroativa, independe de
    alguém usar o app. Só PG; ignora silenciosamente se a extensão não existir."""
    from .db import get_db, USE_PG
    if not USE_PG:
        return []
    try:
        with get_db() as conn:
            rows = conn.execute(f"""
                SELECT ROUND(mean_exec_time::numeric)      AS media_ms,
                       ROUND(max_exec_time::numeric)       AS pior_ms,
                       calls                               AS chamadas,
                       ROUND(total_exec_time::numeric)     AS total_ms,
                       LEFT(REGEXP_REPLACE(query, '\\s+', ' ', 'g'), 240) AS consulta
                  FROM pg_stat_statements
                 WHERE query NOT ILIKE '%pg_stat_statements%'
                   AND query NOT ILIKE '%perf_log%'
                 ORDER BY total_exec_time DESC LIMIT {int(limite)}""").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def uso_por_pessoa(dias=30):
    """Quem está usando o portal, por pessoa — equivalente ao painel do CRM.

    A chave é `usuarios.id`. `perf_log` antigo (antes de 13/08/2026) não tem
    usuario_id, então casa pelo e-mail como reserva. `eventos` só tinha nome em
    texto livre; o backfill de `_casar_eventos_com_usuarios` resolveu o
    histórico, e desde agora o evento já nasce com o id.
    """
    from .db import get_db
    if not garantir_tabela():
        return []
    corte = (datetime.utcnow() - timedelta(days=int(dias))).strftime('%Y-%m-%d %H:%M:%S')
    w = _janela_sql(dias)                     # perf_log.criado_em é TIMESTAMP
    # eventos.criado_em é TEXT ISO -> comparação lexicográfica funciona
    liga = '(p.usuario_id = u.id OR (p.usuario_id IS NULL AND '\
           'LOWER(p.usuario) = LOWER(u.email)))'
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT u.id, u.nome, u.email, u.role, u.ativo,
              (SELECT MAX(e.criado_em) FROM eventos e
                WHERE e.usuario_id = u.id AND e.tipo = 'login')          AS ultimo_login,
              (SELECT COUNT(*) FROM eventos e
                WHERE e.usuario_id = u.id AND e.tipo = 'login'
                  AND e.criado_em >= ?)                                  AS logins,
              (SELECT COUNT(*) FROM eventos e
                WHERE e.usuario_id = u.id AND e.criado_em >= ?
                  AND e.tipo NOT IN ('login', 'logout'))                 AS acoes,
              (SELECT COUNT(*)          FROM perf_log p WHERE {liga} AND {w}) AS requisicoes,
              (SELECT COUNT(DISTINCT p.rota) FROM perf_log p WHERE {liga} AND {w}) AS telas,
              (SELECT MAX(p.criado_em)  FROM perf_log p WHERE {liga} AND {w}) AS ultima_atividade,
              (SELECT ROUND(AVG(p.duracao_ms)) FROM perf_log p WHERE {liga} AND {w}) AS media_ms,
              (SELECT MAX(p.duracao_ms) FROM perf_log p WHERE {liga} AND {w}) AS pior_ms,
              (SELECT COUNT(*) FROM perf_log p
                WHERE {liga} AND {w} AND p.status >= 500)                 AS erros
            FROM usuarios u
            ORDER BY u.nome""", (corte, corte)).fetchall()
    lista = [dict(r) for r in rows]

    # ordem do painel do CRM: quem usou mais recentemente primeiro; quem nunca
    # entrou vai para o fim, em ordem alfabética
    def marca(r):
        return r.get('ultima_atividade') or r.get('ultimo_login')

    ativos = sorted([r for r in lista if marca(r)],
                    key=lambda r: str(marca(r)), reverse=True)
    nunca = sorted([r for r in lista if not marca(r)],
                   key=lambda r: (r.get('nome') or ''))
    return ativos + nunca


def resumo_completo(dias=7):
    """Payload único consumido pela tela Saúde do Sistema.

    Cada pedaço é isolado: se um falhar (tabela que ainda não existe, extensão
    ausente), os outros continuam aparecendo. Um erro num canto não pode apagar
    o painel inteiro.
    """
    out = {'dias': dias}
    partes = (
        ('kpis',    lambda: kpis(dias),        {}),
        ('rotas',   lambda: por_rota(dias),    []),
        ('lentas',  lambda: lentas(dias),      []),
        ('banco',   consultas_do_banco,        []),
        ('pessoas', lambda: uso_por_pessoa(30), []),   # 30d fixos, igual ao CRM
    )
    problemas = []
    for chave, fn, vazio in partes:
        try:
            out[chave] = fn()
        except Exception as e:
            out[chave] = vazio
            problemas.append(f'{chave}: {e}')
    if problemas:
        out['avisos'] = problemas
    return out
