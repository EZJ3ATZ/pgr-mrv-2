# -*- coding: utf-8 -*-
"""Alerta do log: transforma medição em aviso que chega em quem pode agir.

Por que existe: o log de auditoria do CRM ficou meses no ar com a RLS rodando por
linha e ninguém soube — painel é arqueologia, só serve depois que alguém
desconfia. Sem limiar e destinatário, log não muda nada.

DESTINATÁRIO (decisão do Matheus, 13/08/2026): **só ele**. Alerta técnico não vai
para o Bernardo nem para "a equipe" — quem não pode consertar não deve receber.
Endereço em `ALERTA_PARA` (default matheus.costa@).

AS 4 TRAVAS ANTI-RUÍDO — entram junto, não depois. O aviso de atribuição do
Portal CS gerou 444 alertas repetidos e inflou o KPI; o Escudo apontava 90% de
coisa que não era erro. Aqui:
  1. só **mudança de estado** gera e-mail (problema que continua igual vira
     "ainda aberto" no digest, não um segundo e-mail);
  2. **dedup por chave** — um alerta por causa, não por ocorrência;
  3. **teto diário** — estourando, um e-mail só dizendo quantos foram suprimidos;
  4. **interruptor**: `ALERTA_EMAIL=0` desliga o envio sem desligar a medição.
     (Se não pudesse desligar, ele criaria filtro no Outlook e o alerta morreria
     calado, o que é pior que não existir.)

NÍVEIS
  quebrou  → e-mail na hora (erro 5xx em série, sync parado, log sem linha)
  piorou   → 1 digest por dia (p95 de rota, consultas por requisição, rota nova lenta)
  uso      → no mesmo digest, sem e-mail próprio
"""
import os
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))

PARA = os.environ.get('ALERTA_PARA', 'matheus.costa@ocupacional.com.br')
TETO_DIA = int(os.environ.get('ALERTA_TETO_DIA', '5'))       # e-mails imediatos/dia
JANELA_QUEBROU_MIN = 60
MIN_AMOSTRA = 20          # abaixo disso não compara p95 (número de 3 chamadas mente)

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerta_estado (
    chave       TEXT PRIMARY KEY,
    nivel       TEXT,
    titulo      TEXT,
    detalhe     TEXT,
    valor       REAL,
    aberto      INTEGER DEFAULT 1,
    primeiro_em TEXT,
    ultimo_em   TEXT,
    avisado_em  TEXT,
    fechado_em  TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerta_aberto ON alerta_estado(aberto, nivel);

-- Uma linha por E-MAIL enviado. O teto tem de contar envio, não chave: o mesmo
-- alerta reaberto 3× manda 3 e-mails e contaria como 1 se eu olhasse
-- `alerta_estado.avisado_em` (defeito pego pelo teste em 13/08/2026).
CREATE TABLE IF NOT EXISTS alerta_envio (
    id     SERIAL PRIMARY KEY,
    em     TEXT,
    chave  TEXT,
    nivel  TEXT,
    assunto TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerta_envio_em ON alerta_envio(em);
"""

SCHEMA_SQLITE = SCHEMA.replace('SERIAL PRIMARY KEY',
                               'INTEGER PRIMARY KEY AUTOINCREMENT')

_pronto = False


def _agora():
    return datetime.now(tz=BRT)


def garantir_tabela():
    global _pronto
    if _pronto:
        return True
    try:
        from .db import get_db, USE_PG
        with get_db() as conn:
            conn.executescript(SCHEMA if USE_PG else SCHEMA_SQLITE)
        _pronto = True
    except Exception as e:
        print(f'[alerta] tabela não criada: {e}')
    return _pronto


def _envio_ligado():
    return os.environ.get('ALERTA_EMAIL', '1') != '0'


def _horario_comercial(dt=None):
    """Só usa regra de 'sem tráfego' em dia útil das 8h às 18h — silêncio às 3h
    da manhã não é defeito."""
    d = dt or _agora()
    return d.weekday() < 5 and 8 <= d.hour < 18


# ── as regras ──────────────────────────────────────────────────────────
def _achados_quebrou(conn):
    """Coisas que exigem alguém agora."""
    from .db import USE_PG
    out = []
    corte = (_agora() - timedelta(minutes=JANELA_QUEBROU_MIN))
    w = ("criado_em >= NOW() - INTERVAL '%d minutes'" % JANELA_QUEBROU_MIN) if USE_PG \
        else ("criado_em >= datetime('now', '-%d minutes')" % JANELA_QUEBROU_MIN)

    # 1) erro 5xx em série
    try:
        r = conn.execute(f"SELECT COUNT(*) AS c FROM perf_log "
                         f"WHERE {w} AND status >= 500").fetchone()
        n = (r['c'] if r else 0) or 0
        if n >= 5:
            out.append(('erro_5xx', 'quebrou',
                        f'{n} erros 500 na última hora',
                        'Alguma rota está estourando. Abra Saúde do Sistema → '
                        'Piores ocorrências.', float(n)))
    except Exception:
        pass

    # 2) o log parou de chegar (mede o próprio medidor)
    try:
        if _horario_comercial():
            r = conn.execute('SELECT MAX(criado_em) AS m FROM perf_log').fetchone()
            ultimo = (r['m'] if r else None)
            if ultimo:
                txt = str(ultimo)[:19].replace('T', ' ')
                try:
                    dt = datetime.strptime(txt, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    horas = (datetime.now(tz=timezone.utc) - dt).total_seconds() / 3600
                except Exception:
                    horas = 0
                if horas >= 3:
                    out.append(('log_parou', 'quebrou',
                                f'Nenhuma requisição medida há {int(horas)}h',
                                'Ou ninguém está usando em horário comercial, ou a '
                                'medição parou de gravar.', float(horas)))
    except Exception:
        pass

    # 3) sync do Planner parado — é a espinha do portal
    try:
        r = conn.execute("SELECT MAX(criado_em) AS m FROM eventos "
                         "WHERE tipo IN ('sync_planner','demanda_criada_planner')").fetchone()
        ultimo = (r['m'] if r else None)
        if ultimo and _horario_comercial():
            txt = str(ultimo)[:19].replace('T', ' ')
            try:
                dt = datetime.strptime(txt, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                horas = (datetime.now(tz=timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                horas = 0
            if horas >= 6:
                out.append(('sync_parado', 'quebrou',
                            f'Sync do Planner sem novidade há {int(horas)}h',
                            'O pipeline Planner → demandas pode estar parado.',
                            float(horas)))
    except Exception:
        pass
    return out


def _achados_piorou(conn):
    """Comparação com a semana anterior. Sem linha de base, não alerta."""
    from .db import USE_PG
    out = []
    if not USE_PG:
        return out    # percentil só no PG; em SQLite o digest sai sem esta parte
    try:
        rows = conn.execute("""
            WITH agora AS (
              SELECT rota,
                     COUNT(*) AS n,
                     (PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY duracao_ms))::int AS p95,
                     AVG(consultas) AS q
                FROM perf_log
               WHERE criado_em >= NOW() - INTERVAL '24 hours'
                 AND COALESCE(automatico,0) = 0
               GROUP BY rota
            ), antes AS (
              SELECT rota,
                     COUNT(*) AS n,
                     (PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY duracao_ms))::int AS p95,
                     AVG(consultas) AS q
                FROM perf_log
               WHERE criado_em >= NOW() - INTERVAL '8 days'
                 AND criado_em <  NOW() - INTERVAL '24 hours'
                 AND COALESCE(automatico,0) = 0
               GROUP BY rota
            )
            SELECT a.rota, a.n, a.p95, b.p95 AS p95_antes,
                   ROUND(a.q::numeric,1) AS q, ROUND(b.q::numeric,1) AS q_antes
              FROM agora a JOIN antes b ON b.rota = a.rota
             WHERE a.n >= %d AND b.n >= %d
        """ % (MIN_AMOSTRA, MIN_AMOSTRA)).fetchall()
    except Exception as e:
        print(f'[alerta] comparação piorou falhou: {e}')
        return out

    for r in rows:
        d = dict(r)
        rota, p95, antes = d['rota'], d['p95'] or 0, d['p95_antes'] or 0
        # p95 dobrou E passou de 1s (subir de 20ms para 60ms não é notícia)
        if antes > 0 and p95 >= 1000 and p95 >= antes * 1.5:
            out.append((f'p95:{rota}', 'piorou',
                        f'{rota} ficou {int(p95/max(antes,1))}× mais lenta',
                        f'p95 passou de {antes} ms para {p95} ms nas últimas 24h.',
                        float(p95)))
        # consultas por requisição explodindo: foi ISSO que denunciou o bug do
        # init_db em 13/08 (2 → 523 consultas por requisição)
        q, q_antes = float(d.get('q') or 0), float(d.get('q_antes') or 0)
        if q_antes >= 1 and q >= q_antes * 3 and q >= 10:
            out.append((f'consultas:{rota}', 'piorou',
                        f'{rota} passou a fazer {int(q)} consultas por requisição',
                        f'Antes eram {q_antes:.1f}. Suspeita de N+1 ou migration '
                        f'rodando fora de hora.', q))
    return out


def _resumo_uso(conn):
    """Vai no corpo do digest, sem e-mail próprio."""
    from .db import USE_PG
    try:
        w = ("criado_em >= NOW() - INTERVAL '7 days'") if USE_PG \
            else ("criado_em >= datetime('now','-7 days')")
        r = conn.execute(f"""
            SELECT COUNT(*) AS req,
                   COUNT(DISTINCT usuario_id) AS pessoas,
                   {"COUNT(*) FILTER (WHERE duracao_ms >= 1000)" if USE_PG
                    else "SUM(CASE WHEN duracao_ms >= 1000 THEN 1 ELSE 0 END)"} AS lentas,
                   {"COUNT(*) FILTER (WHERE COALESCE(automatico,0)=1)" if USE_PG
                    else "SUM(CASE WHEN automatico=1 THEN 1 ELSE 0 END)"} AS robo
              FROM perf_log WHERE {w}""").fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}


# ── estado (dedup + mudança de estado) ─────────────────────────────────
def _sincronizar_estado(conn, achados):
    """Grava o que está aberto e devolve só o que MUDOU (novo ou reaberto)."""
    agora = _agora().strftime('%Y-%m-%d %H:%M:%S')
    vistos = {a[0] for a in achados}
    novos = []
    for chave, nivel, titulo, detalhe, valor in achados:
        r = conn.execute('SELECT aberto FROM alerta_estado WHERE chave=?',
                         (chave,)).fetchone()
        if r is None:
            conn.execute('INSERT INTO alerta_estado (chave,nivel,titulo,detalhe,'
                         'valor,aberto,primeiro_em,ultimo_em) VALUES (?,?,?,?,?,1,?,?)',
                         (chave, nivel, titulo, detalhe, valor, agora, agora))
            novos.append((chave, nivel, titulo, detalhe, valor))
        elif not (r['aberto'] or 0):
            conn.execute('UPDATE alerta_estado SET aberto=1, titulo=?, detalhe=?, '
                         'valor=?, ultimo_em=?, fechado_em=NULL WHERE chave=?',
                         (titulo, detalhe, valor, agora, chave))
            novos.append((chave, nivel, titulo, detalhe, valor))
        else:
            # já aberto: atualiza o número, NÃO gera e-mail de novo
            conn.execute('UPDATE alerta_estado SET titulo=?, detalhe=?, valor=?, '
                         'ultimo_em=? WHERE chave=?',
                         (titulo, detalhe, valor, agora, chave))
    # fecha o que saiu da lista
    for r in conn.execute('SELECT chave FROM alerta_estado WHERE aberto=1').fetchall():
        if r['chave'] not in vistos:
            conn.execute('UPDATE alerta_estado SET aberto=0, fechado_em=? WHERE chave=?',
                         (agora, r['chave']))
    return novos


def _marcar_avisado(conn, chaves, nivel='', assunto=''):
    agora = _agora().strftime('%Y-%m-%d %H:%M:%S')
    for c in chaves:
        conn.execute('UPDATE alerta_estado SET avisado_em=? WHERE chave=?', (agora, c))
        # registra o ENVIO (é isso que o teto conta)
        conn.execute('INSERT INTO alerta_envio (em,chave,nivel,assunto) '
                     'VALUES (?,?,?,?)', (agora, c, nivel, assunto[:160]))


def _enviados_hoje(conn):
    """Quantos E-MAILS saíram hoje — não quantas chaves foram avisadas."""
    hoje = _agora().strftime('%Y-%m-%d')
    r = conn.execute('SELECT COUNT(*) AS c FROM alerta_envio WHERE em LIKE ?',
                     (hoje + '%',)).fetchone()
    return (r['c'] if r else 0) or 0


# ── envio ──────────────────────────────────────────────────────────────
# 🔴 Remetente: NÃO usar o do orquestrador. O default dele é
# `medicoes@ocupacional.com.br`, que **não existe** no tenant (404 no Graph em
# 13/08/2026) — o envio falhava calado e a rota de prévia ainda dizia
# "enviou=True". Aqui usa a MESMA caixa que já manda e-mail neste app hoje
# (a de reset de senha, controle/auth.py::MAIL_SENDER).
DE = os.environ.get('ALERTA_DE') or os.environ.get('MAIL_SENDER') \
     or 'engenharia19@ocupacional.com.br'


def _enviar(assunto, corpo):
    """Devolve (ok, erro). Quem chama TEM de olhar o retorno — foi ignorá-lo que
    deixou o envio falhar em silêncio."""
    if not _envio_ligado():
        print(f'[alerta] envio desligado (ALERTA_EMAIL=0) — seria: {assunto}')
        return False, 'desligado'
    try:
        from .graph import graph_post, graph_ok
        if not graph_ok():
            return False, 'graph sem credencial'
        graph_post(f'/users/{DE}/sendMail', {
            'message': {
                'subject': assunto,
                'body': {'contentType': 'Text', 'content': corpo},
                'toRecipients': [{'emailAddress': {'address': PARA}}],
            },
            'saveToSentItems': True,
        })
        return True, None
    except Exception as e:
        print(f'[alerta] FALHA ao enviar "{assunto}": {e}')
        return False, str(e)


_LINK = 'https://medicoes-ocupacional.up.railway.app/  →  Saúde do Sistema'


def verificar(forcar_digest=False, dry_run=False):
    """Roda as regras. Chamado pelo agendador. Devolve o que fez."""
    if not garantir_tabela():
        return {'erro': 'tabela'}
    from .db import get_db
    saida = {'quebrou': [], 'piorou': [], 'suprimidos': 0, 'digest': False}
    with get_db() as conn:
        achados = _achados_quebrou(conn) + _achados_piorou(conn)
        novos = _sincronizar_estado(conn, achados)
        uso = _resumo_uso(conn)
        abertos = [dict(r) for r in conn.execute(
            'SELECT chave,nivel,titulo,detalhe,primeiro_em FROM alerta_estado '
            'WHERE aberto=1 ORDER BY nivel, primeiro_em').fetchall()]
        ja = _enviados_hoje(conn)

        # 1) quebrou: e-mail na hora, respeitando o teto
        urgentes = [n for n in novos if n[1] == 'quebrou']
        for chave, nivel, titulo, detalhe, valor in urgentes:
            if ja >= TETO_DIA:
                saida['suprimidos'] += 1
                continue
            assunto = f'[Medições] {titulo}'
            corpo = (f'{titulo}\n\n{detalhe}\n\n{_LINK}\n\n'
                     f'(Alerta automático do Portal de Medições. '
                     f'Para desligar: ALERTA_EMAIL=0 no Railway.)')
            if not dry_run:
                ok, err = _enviar(assunto, corpo)
                saida.setdefault('envios', []).append(
                    {'chave': chave, 'ok': ok, 'erro': err})
                if ok:
                    _marcar_avisado(conn, [chave], nivel, assunto)
                    ja += 1
            saida['quebrou'].append(titulo)

        # um e-mail só dizendo quantos ficaram de fora — nunca N e-mails
        if saida['suprimidos'] and not dry_run:
            _enviar('[Medições] alertas suprimidos',
                    f"{saida['suprimidos']} alerta(s) além do teto de {TETO_DIA} "
                    f"por dia. Abra o painel: {_LINK}")

        # 2) digest: 1x por dia, com o que é novo no topo e o que segue aberto
        saida['piorou'] = [n[2] for n in novos if n[1] == 'piorou']
        if forcar_digest:
            linhas = []
            if novos:
                linhas.append('NOVO DESDE ONTEM')
                linhas += [f'  · {t}\n    {d}' for _, _, t, d, _ in novos]
            ainda = [a for a in abertos if a['chave'] not in {n[0] for n in novos}]
            if ainda:
                linhas.append('\nAINDA ABERTO')
                linhas += [f"  · {a['titulo']} (desde {str(a['primeiro_em'])[:16]})"
                           for a in ainda]
            if not linhas:
                linhas.append('Nada aberto. Nenhuma rota piorou nas últimas 24h.')
            if uso:
                linhas.append(
                    f"\nUSO (7 dias): {uso.get('req') or 0} requisições · "
                    f"{uso.get('pessoas') or 0} pessoa(s) · "
                    f"{uso.get('lentas') or 0} acima de 1s · "
                    f"{uso.get('robo') or 0} de aba parada")
            corpo = '\n'.join(linhas) + f'\n\n{_LINK}\n\n(Digest diário. ALERTA_EMAIL=0 desliga.)'
            if not dry_run:
                ok, err = _enviar('[Medições] resumo do dia', corpo)
                saida['envio_ok'] = ok
                saida['envio_erro'] = err          # sem isto, falha vira silêncio
                if ok:
                    _marcar_avisado(conn, [n[0] for n in novos if n[1] == 'piorou'],
                                    'piorou', '[Medições] resumo do dia')
            saida['digest'] = True
            saida['corpo_digest'] = corpo

        saida['abertos'] = abertos
        saida['uso'] = uso
    return saida
