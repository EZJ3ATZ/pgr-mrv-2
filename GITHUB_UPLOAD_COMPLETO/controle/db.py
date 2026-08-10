# -*- coding: utf-8 -*-
"""
Database layer — suporta SQLite (dev local) e PostgreSQL (Railway produção).
DATABASE_URL no ambiente ativa modo PostgreSQL automaticamente.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

# ── Detecção de banco ──────────────────────────────────────────────────
_DATABASE_URL = os.environ.get('DATABASE_URL', '')
USE_PG = bool(_DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    # Railway fornece postgres:// mas psycopg2 exige postgresql://
    if _DATABASE_URL.startswith('postgres://'):
        _DATABASE_URL = _DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# SQLite fallback (dev local)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.environ.get('CONTROLE_DATA_DIR', os.path.join(BASE_DIR, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'controle.db')


# ── Helpers de dialeto SQL ─────────────────────────────────────────────
if USE_PG:
    def _ds(col):
        """Dias desde uma data (days since). Safe cast — retorna 0 se valor não for data ISO."""
        return (
            f"(CURRENT_DATE - "
            f"CASE WHEN ({col})::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
            f"THEN LEFT(({col})::text, 10)::date "
            f"ELSE CURRENT_DATE END)"
        )

    def _du(col):
        """Dias até uma data (days until). Safe cast."""
        return (
            f"(CASE WHEN ({col})::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
            f"THEN LEFT(({col})::text, 10)::date "
            f"ELSE CURRENT_DATE END - CURRENT_DATE)"
        )

    def _gc(col, sep=','):
        """GROUP_CONCAT equivalente."""
        return f"STRING_AGG(({col})::text, '{sep}')"

    def _gcd(col, sep=','):
        """GROUP_CONCAT DISTINCT equivalente."""
        return f"STRING_AGG(DISTINCT ({col})::text, '{sep}')"

    def _lab_expire_cond(lab_col, val_col="COALESCE(dias_validade,45)"):
        """Condição: data de vencimento do laboratório."""
        return f"CURRENT_DATE > ({lab_col})::date + {val_col}"

    def _lab_days_left(lab_col, val_col="COALESCE(dias_validade,45)"):
        return f"(({lab_col})::date + {val_col} - CURRENT_DATE)"

    def _lab_days_in(lab_col):
        return f"(CURRENT_DATE - ({lab_col})::date)"
else:
    def _ds(col):
        return f"CAST(julianday('now') - julianday({col}) AS INTEGER)"

    def _du(col):
        return f"CAST(julianday({col}) - julianday('now') AS INTEGER)"

    def _gc(col, sep=','):
        return f"GROUP_CONCAT({col}, '{sep}')"

    def _gcd(col, sep=','):
        return f"GROUP_CONCAT(DISTINCT {col})"

    def _lab_expire_cond(lab_col, val_col="COALESCE(dias_validade,45)"):
        return f"julianday('now') > julianday({lab_col}) + {val_col}"

    def _lab_days_left(lab_col, val_col="COALESCE(dias_validade,45)"):
        return f"CAST(julianday({lab_col}) + {val_col} - julianday('now') AS INTEGER)"

    def _lab_days_in(lab_col):
        return f"CAST(julianday('now') - julianday({lab_col}) AS INTEGER)"


# ── Status canônico do amostrador (fonte única de verdade) ─────────────
# Fluxo operacional real (29/05/2026): disponivel → reservado → laboratorio
# → concluido (resultado do lab recebido). devolvido/manutencao/descartado
# são estados auxiliares. O antigo "UTILIZADO?" foi REMOVIDO (era estado
# fantasma de auditoria que contaminava analytics) → volta para disponivel.
STATUS_AMOSTRADOR = (
    'disponivel', 'reservado', 'laboratorio',
    'concluido', 'devolvido', 'manutencao', 'descartado',
)
STATUS_AMOSTRADOR_LABEL = {
    'disponivel': 'Disponível',
    'reservado':  'Reservado',
    'laboratorio': 'No laboratório',
    'concluido':  'Concluído',
    'devolvido':  'Devolvido',
    'manutencao': 'Manutenção',
    'descartado': 'Descartado',
}
# Mapa de valores legados/variações → status canônico
_STATUS_LEGADO = {
    'estoque': 'disponivel', 'disponivel': 'disponivel', 'disponível': 'disponivel',
    'reservado': 'reservado',
    'laboratorio': 'laboratorio', 'laboratório': 'laboratorio',
    'emuso': 'laboratorio', 'em uso': 'laboratorio', 'em_uso': 'laboratorio',
    'enviado': 'laboratorio', 'em_analise': 'laboratorio',
    'em analise': 'laboratorio', 'análise': 'laboratorio', 'analise': 'laboratorio',
    'concluido': 'concluido', 'concluído': 'concluido', 'resultado': 'concluido',
    'devolvido': 'devolvido',
    'manutencao': 'manutencao', 'manutenção': 'manutencao',
    'descartado': 'descartado',
    # 'utilizado' = saiu a campo → NUNCA volta ao estoque (fix 03/07/2026:
    # antes mapeava p/ 'disponivel' e o amostrador usado reaparecia como novo)
    'utilizado': 'laboratorio', 'utilizado?': 'laboratorio', 'verificar': 'disponivel',
}

def normalizar_status_amostrador(raw):
    """Converte qualquer valor de status (legado/maiúsculo/nome de empresa) no
    status canônico. Valor desconhecido (ex: nome de empresa gravado por engano)
    → 'laboratorio' (preserva o comportamento antigo de auditoria)."""
    if not raw:
        return 'disponivel'
    k = str(raw).strip().lower()
    return _STATUS_LEGADO.get(k, 'laboratorio')


# ── Wrapper PostgreSQL (faz psycopg2 se comportar como sqlite3) ────────

class _PGCursor:
    """Cursor psycopg2 com interface compatível com sqlite3.Row."""

    def __init__(self, pg_conn):
        self._pg_conn = pg_conn
        self._cur = None
        self._lastrowid = None

    # Tabelas SEM coluna 'id' (PK textual ou sem PK) — não anexar RETURNING id,
    # senão o Postgres lança 'column "id" does not exist' e ABORTA a transação
    # inteira (InFailedSqlTransaction), fazendo rollback de tudo que veio antes.
    # RA_LAUDOS entrou aqui: o INSERT do backfill de RAs (lab_inbox._upsert_ra_laudo)
    # derrubava a transação toda — conclusões de amostrador e baixas de medição
    # eram desfeitas junto (bug do "backfill nunca conclui em prod").
    _NO_ID_TABLES = ('MS_USERS', 'MS_SYNC_STATE', 'RA_LAUDOS')

    def execute(self, sql, params=None):
        self._cur = self._pg_conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor)
        if params:
            # Escape literal % (LIKE patterns etc.) before replacing ? with %s
            sql = sql.replace('%', '%%').replace('?', '%s')
        upper = sql.strip().upper()
        is_insert = upper.startswith('INSERT')
        _is_upsert = 'ON CONFLICT' in upper
        _has_returning = 'RETURNING' in upper
        _table_has_id = not any(f'INTO {t}' in upper for t in self._NO_ID_TABLES)
        _add_returning = is_insert and not _is_upsert and not _has_returning and _table_has_id
        if _add_returning:
            sql = sql.rstrip(' \n;') + ' RETURNING id'
            _has_returning = True
        if params:
            self._cur.execute(sql, params)
        else:
            self._cur.execute(sql)
        # Só busca lastrowid quando há RETURNING (anexado por nós ou já no SQL).
        # Upserts e inserts em tabelas sem 'id' não produzem essa linha.
        if is_insert and not _is_upsert and _has_returning:
            row = self._cur.fetchone()
            self._lastrowid = int(row['id']) if row and row.get('id') is not None else None
        return self

    def executemany(self, sql, params_list):
        self._cur = self._pg_conn.cursor()
        sql = sql.replace('%', '%%').replace('?', '%s')
        self._cur.executemany(sql, params_list)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        return self._lastrowid


class _PGConn:
    """Conexão psycopg2 com interface compatível com sqlite3.Connection.

    Ao chamar close(), devolve a conexão ao pool em vez de fechá-la fisicamente,
    evitando pool exhaustion quando usado fora do context manager get_db().
    """

    def __init__(self, pg_conn, pool=None):
        self._conn = pg_conn
        self._pool = pool   # referência ao pool para devolver no close()
        self._closed = False

    def execute(self, sql, params=None):
        cur = _PGCursor(self._conn)
        return cur.execute(sql, params)

    def executescript(self, sql):
        """Executa múltiplos statements usando SAVEPOINTs (idempotente)."""
        cur = self._conn.cursor()
        for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
            try:
                cur.execute('SAVEPOINT _sp')
                cur.execute(stmt)
                cur.execute('RELEASE SAVEPOINT _sp')
            except Exception as e:
                try:
                    cur.execute('ROLLBACK TO SAVEPOINT _sp')
                    cur.execute('RELEASE SAVEPOINT _sp')
                except Exception:
                    pass
                msg = str(e).lower()
                if 'already exists' not in msg and 'does not exist' not in msg:
                    print(f'[db.pg] executescript: {e}')

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        """Devolve ao pool (não fecha fisicamente) — evita pool exhaustion."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.rollback()  # limpa transação pendente antes de devolver
        except Exception:
            pass
        if self._pool is not None:
            try:
                self._pool.putconn(self._conn)
                return
            except Exception:
                pass
        # fallback: fechar fisicamente só se não tiver pool
        try:
            self._conn.close()
        except Exception:
            pass


# ── Schema SQLite ──────────────────────────────────────────────────────
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS empresas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cnpj        TEXT UNIQUE,
    nome        TEXT NOT NULL,
    unidade     TEXT,
    contato     TEXT,
    telefone    TEXT,
    email       TEXT,
    cidade      TEXT,
    uf          TEXT,
    pendente    INTEGER DEFAULT 0,
    criado_em   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS demandas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_os       TEXT,
    empresa_id      INTEGER NOT NULL,
    prazo           TEXT,
    status          TEXT DEFAULT 'pendente',
    observacao      TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    nome_tarefa     TEXT,
    data_conclusao  TEXT,
    responsavel     TEXT,
    status_planner  TEXT,
    progresso       INTEGER DEFAULT 0,
    checklist       TEXT,
    checklist_prog  TEXT,
    bucket          TEXT,
    etiquetas       TEXT,
    descricao       TEXT,
    cnpj            TEXT,
    tem_comentarios INTEGER DEFAULT 0,
    contato_feito       INTEGER DEFAULT 0,
    contato_feito_em    TEXT,
    contato_feito_por   TEXT,
    planner_task_id     TEXT,
    planner_plan_id     TEXT,
    planner_plan_nome   TEXT,
    planner_bucket_id   TEXT,
    planner_bucket      TEXT,
    planner_group_id    TEXT,
    planner_group_nome  TEXT,
    titulo              TEXT,
    prioridade          TEXT DEFAULT 'media',
    percent_complete    INTEGER DEFAULT 0,
    criado_em_ms        TEXT,
    concluido_em_ms     TEXT,
    ms_assignee_id      TEXT,
    ms_assignees_json   TEXT,
    etiquetas_json      TEXT,
    origem              TEXT DEFAULT 'manual',
    atualizado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    empresa_match_score     REAL,
    empresa_match_metodo    TEXT,
    tipo_demanda            TEXT DEFAULT 'operacional',
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS amostradores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    status          TEXT DEFAULT 'disponivel',
    data_entrada    TEXT,
    empresa_id      INTEGER,
    avaliador       TEXT,
    data_medicao    TEXT,
    observacao      TEXT,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
    data_envio_lab  TEXT,
    dias_validade   INTEGER DEFAULT 45,
    lote            TEXT,
    observacao_venc TEXT,
    cert_numero     TEXT,
    cert_validade   TEXT,
    cert_laboratorio TEXT,
    cert_arquivo    TEXT,
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS medicoes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id          INTEGER NOT NULL,
    agente              TEXT NOT NULL,
    tipo_amostrador     TEXT,
    qtd_pontos_prevista INTEGER DEFAULT 1,
    qtd_pontos_feita    INTEGER DEFAULT 0,
    necessita_laudo     TEXT,
    status              TEXT DEFAULT 'pendente',
    observacao          TEXT,
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id)
);

CREATE TABLE IF NOT EXISTS baixas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    medicao_id          INTEGER NOT NULL,
    -- amostrador_id aceita NULL (10/08/2026): avaliação feita por empresa
    -- terceira (ex.: Opus) não consome amostrador nosso, mas a baixa da
    -- medição precisa ser registrada igual.
    amostrador_id       INTEGER,
    avaliador           TEXT,
    bomba               TEXT,
    vazao_calibrada     REAL,
    volume_recomendado  REAL,
    tempo_calculado_min REAL,
    tempo_calculado_max REAL,
    data_medicao        TEXT,
    observacao          TEXT,
    -- Obrigatorio quando amostrador_id e NULL: por que a baixa nao teve
    -- dispositivo nosso em campo (terceiro, agente fisico, retroativo...).
    motivo_sem_amostrador TEXT,
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (medicao_id)    REFERENCES medicoes(id),
    FOREIGN KEY (amostrador_id) REFERENCES amostradores(id)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,
    arquivo_nome    TEXT,
    registros_novos INTEGER DEFAULT 0,
    registros_atu   INTEGER DEFAULT 0,
    usuario         TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS coletas_ruido (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id          INTEGER,
    empresa_id          INTEGER,
    empresa_nome        TEXT,
    acompanhante        TEXT,
    cargo_acompanhante  TEXT,
    tecnico             TEXT,
    data_coleta         TEXT,
    hora_inicio         TEXT,
    hora_termino        TEXT,
    calibrador          TEXT,
    calibracao_inicial  REAL,
    calibracao_final    REAL,
    desvio_calibracao   REAL,
    status_calibracao   TEXT DEFAULT 'pendente',
    unidade             TEXT,
    cidade              TEXT,
    resp_empresa        TEXT,
    os                  TEXT,
    observacao          TEXT,
    status              TEXT DEFAULT 'rascunho',
    visita_id           INTEGER,
    planejamento_id     INTEGER,
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS coletas_ruido_func (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coleta_id       INTEGER NOT NULL,
    seq             INTEGER DEFAULT 1,
    nome            TEXT,
    cargo           TEXT,
    setor           TEXT,
    almoco          INTEGER DEFAULT 0,
    serie_dosimetro TEXT,
    pausa           TEXT,
    almoco_fim      TEXT,
    FOREIGN KEY (coleta_id) REFERENCES coletas_ruido(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS visita_anexos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id   INTEGER,
    empresa_nome TEXT,
    data_coleta  TEXT,
    tipo         TEXT,
    seq          INTEGER DEFAULT 1,
    legenda      TEXT,
    cargo        TEXT,
    data_b64     TEXT,
    criado_em    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS coletas_quimico (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id          INTEGER,
    empresa_id          INTEGER,
    empresa_nome        TEXT,
    responsavel_coleta  TEXT,
    cidade              TEXT,
    unidade             TEXT,
    data_coleta         TEXT,
    dia_semana          TEXT,
    turno               TEXT,
    nome_funcionario    TEXT,
    jornada             TEXT,
    funcao              TEXT,
    setor               TEXT,
    local_atividade     TEXT,
    atividade           TEXT,
    ventilacao          TEXT,
    ambiente            TEXT,
    condicoes_meteo     TEXT,
    temperatura         TEXT,
    umidade             TEXT,
    outras_condicoes    TEXT,
    substancias         TEXT,
    fracao              TEXT,
    tempo_exposto       TEXT,
    bomba               TEXT,
    id_bomba            TEXT,
    data_cal_bomba      TEXT,
    id_calibrador       TEXT,
    acessorios          TEXT,
    epis                TEXT,
    epc                 TEXT,
    observacao          TEXT,
    status              TEXT DEFAULT 'rascunho',
    visita_id           INTEGER,
    planejamento_id     INTEGER,
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS coletas_quimico_amostr (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coleta_id       INTEGER NOT NULL,
    seq             INTEGER DEFAULT 1,
    id_amostrador   TEXT,
    tipo_amostrador TEXT,
    substancia      TEXT,
    bomba           TEXT,
    vazao_inicial   REAL,
    vazao_final     REAL,
    vazao_media     REAL,
    hora_inicio     TEXT,
    hora_final      TEXT,
    intervalos      TEXT,
    tempo_min       REAL,
    volume_L        REAL,
    variacao_vazao  REAL,
    status_variacao TEXT DEFAULT 'pendente',
    FOREIGN KEY (coleta_id) REFERENCES coletas_quimico(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS coletas_outros (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,
    empresa_id      INTEGER,
    empresa_nome    TEXT,
    demanda_id      INTEGER,
    numero_os       TEXT,
    avaliador       TEXT,
    data_coleta     TEXT,
    acompanhante    TEXT,
    hora_inicio     TEXT,
    hora_termino    TEXT,
    unidade         TEXT,
    cidade          TEXT,
    observacao      TEXT,
    dados_json      TEXT,
    status          TEXT DEFAULT 'concluida',
    visita_id       INTEGER,
    planejamento_id INTEGER,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS ms_users (
    ms_id        TEXT PRIMARY KEY,
    display_name TEXT,
    email        TEXT,
    job_title    TEXT,
    department   TEXT,
    atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ms_sync_state (
    chave        TEXT PRIMARY KEY,
    valor        TEXT,
    atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS planner_raw_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    planner_task_id     TEXT UNIQUE NOT NULL,
    planner_plan_id     TEXT,
    planner_plan_nome   TEXT,
    planner_bucket_id   TEXT,
    planner_bucket      TEXT,
    planner_group_id    TEXT,
    planner_group_nome  TEXT,
    titulo              TEXT,
    descricao           TEXT,
    checklist_json      TEXT,
    raw_json            TEXT,
    percent_complete    INTEGER DEFAULT 0,
    prazo               TEXT,
    criado_em_ms        TEXT,
    concluido_em_ms     TEXT,
    ms_assignee_id      TEXT,
    ms_assignees_json   TEXT,
    etiquetas_json      TEXT,
    sync_status         TEXT DEFAULT 'raw',
    ignored_reason      TEXT,
    synced_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at        TEXT
);

CREATE TABLE IF NOT EXISTS eventos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT NOT NULL,
    descricao   TEXT,
    ref_id      INTEGER,
    ref_tipo    TEXT,
    usuario     TEXT,
    ms_user_id  TEXT,
    ip          TEXT,
    criado_em   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contatos_empresa (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL,
    resultado       TEXT NOT NULL,
    obs             TEXT,
    proximo_contato TEXT,
    feito_por       TEXT DEFAULT 'Matheus',
    feito_em        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS planejamentos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id           INTEGER,
    empresa_id           INTEGER NOT NULL,
    numero_os            TEXT,
    tecnico              TEXT NOT NULL,
    data_prevista        TEXT,
    agentes_previstos    TEXT,
    qtd_dosim_prevista   INTEGER DEFAULT 0,
    qtd_bombas_previstas INTEGER DEFAULT 0,
    equipamentos_json    TEXT,
    observacao           TEXT,
    status               TEXT DEFAULT 'rascunho',
    criado_em            TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em        TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS visitas_tecnicas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    planejamento_id INTEGER,
    demanda_id      INTEGER,
    empresa_id      INTEGER,
    tecnico         TEXT NOT NULL,
    data_visita     TEXT NOT NULL,
    hora_inicio     TEXT,
    hora_termino    TEXT,
    tipo_visita     TEXT DEFAULT 'medicao',
    resultado       TEXT DEFAULT 'pendente',
    retrabalho      INTEGER DEFAULT 0,
    justificativa   TEXT,
    observacao_geral TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id),
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS execucao_campo (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    visita_id              INTEGER,
    planejamento_id        INTEGER,
    agentes_executados     TEXT,
    agentes_nao_executados TEXT,
    agentes_adicionados    TEXT,
    justificativa_causa    TEXT,
    cobravel               INTEGER DEFAULT 0,
    observacao             TEXT,
    criado_em              TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (visita_id)       REFERENCES visitas_tecnicas(id),
    FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id)
);

CREATE TABLE IF NOT EXISTS equipamentos_inventario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,
    marca           TEXT,
    modelo          TEXT,
    numero_serie    TEXT,
    compatibilidade TEXT DEFAULT '',
    status          TEXT DEFAULT 'disponivel',
    cert_numero     TEXT,
    cert_validade   TEXT,
    observacao      TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metricas_operacionais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id      INTEGER,
    tecnico         TEXT,
    lead_time_dias  INTEGER,
    delay_dias      INTEGER,
    retrabalho      INTEGER DEFAULT 0,
    visitas_total   INTEGER DEFAULT 0,
    calculado_em    TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nome         TEXT NOT NULL,
    email        TEXT UNIQUE NOT NULL,
    senha_hash   TEXT NOT NULL,
    registro_mte TEXT,
    role         TEXT DEFAULT 'tecnico',
    ativo        INTEGER DEFAULT 1,
    criado_em    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    token      TEXT UNIQUE NOT NULL,
    usado      INTEGER DEFAULT 0,
    criado_em  TEXT DEFAULT CURRENT_TIMESTAMP,
    expira_em  TEXT NOT NULL
);

-- Catálogo nome ↔ registro MTE. Existe porque quem assina/executa não é
-- necessariamente quem tem login: o select de técnico dos documentos vinha
-- só de `usuarios` e o time de medições ficava fora da lista.
CREATE TABLE IF NOT EXISTS tecnicos_mte (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,
    nome_norm     TEXT,
    registro_mte  TEXT,
    cargo         TEXT DEFAULT 'TST',
    ativo         INTEGER DEFAULT 1,
    origem        TEXT DEFAULT 'seed',
    criado_em     TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_amostr_codigo   ON amostradores(codigo);
CREATE INDEX IF NOT EXISTS idx_amostr_status   ON amostradores(status);
CREATE INDEX IF NOT EXISTS idx_amostr_tipo     ON amostradores(tipo);
CREATE INDEX IF NOT EXISTS idx_med_demanda     ON medicoes(demanda_id);
CREATE INDEX IF NOT EXISTS idx_med_status      ON medicoes(status);
CREATE INDEX IF NOT EXISTS idx_med_agente      ON medicoes(agente);
CREATE INDEX IF NOT EXISTS idx_dem_status      ON demandas(status);
CREATE INDEX IF NOT EXISTS idx_dem_empresa     ON demandas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_dem_prazo       ON demandas(prazo);
CREATE INDEX IF NOT EXISTS idx_emp_nome        ON empresas(nome);
CREATE INDEX IF NOT EXISTS idx_col_ruido_empresa ON coletas_ruido(empresa_id);
CREATE INDEX IF NOT EXISTS idx_col_ruido_status  ON coletas_ruido(status);
CREATE INDEX IF NOT EXISTS idx_col_ruido_func    ON coletas_ruido_func(coleta_id);
CREATE INDEX IF NOT EXISTS idx_col_quim_empresa  ON coletas_quimico(empresa_id);
CREATE INDEX IF NOT EXISTS idx_col_quim_status   ON coletas_quimico(status);
CREATE INDEX IF NOT EXISTS idx_col_quim_amostr   ON coletas_quimico_amostr(coleta_id);
CREATE INDEX IF NOT EXISTS idx_vis_anexos_dem    ON visita_anexos(demanda_id);
CREATE INDEX IF NOT EXISTS idx_vis_anexos_emp    ON visita_anexos(empresa_nome, data_coleta);
CREATE INDEX IF NOT EXISTS idx_col_outros_tipo    ON coletas_outros(tipo);
CREATE INDEX IF NOT EXISTS idx_col_outros_empresa ON coletas_outros(empresa_id);
CREATE INDEX IF NOT EXISTS idx_raw_planner_task  ON planner_raw_tasks(planner_task_id);
CREATE INDEX IF NOT EXISTS idx_raw_bucket        ON planner_raw_tasks(planner_bucket);
CREATE INDEX IF NOT EXISTS idx_raw_sync_status   ON planner_raw_tasks(sync_status);
CREATE INDEX IF NOT EXISTS idx_raw_synced_at     ON planner_raw_tasks(synced_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tecmte_norm ON tecnicos_mte(nome_norm);
CREATE INDEX IF NOT EXISTS idx_eventos_tipo      ON eventos(tipo);
CREATE INDEX IF NOT EXISTS idx_eventos_ref       ON eventos(ref_id, ref_tipo);
CREATE INDEX IF NOT EXISTS idx_eventos_criado    ON eventos(criado_em);
CREATE INDEX IF NOT EXISTS idx_plan_demanda      ON planejamentos(demanda_id);
CREATE INDEX IF NOT EXISTS idx_plan_tecnico      ON planejamentos(tecnico);
CREATE INDEX IF NOT EXISTS idx_plan_status       ON planejamentos(status);
CREATE INDEX IF NOT EXISTS idx_plan_data         ON planejamentos(data_prevista);
CREATE INDEX IF NOT EXISTS idx_visita_tecnico    ON visitas_tecnicas(tecnico);
CREATE INDEX IF NOT EXISTS idx_visita_demanda    ON visitas_tecnicas(demanda_id);
CREATE INDEX IF NOT EXISTS idx_visita_data       ON visitas_tecnicas(data_visita);
CREATE INDEX IF NOT EXISTS idx_visita_planejamento ON visitas_tecnicas(planejamento_id);
CREATE INDEX IF NOT EXISTS idx_exec_visita       ON execucao_campo(visita_id);
CREATE INDEX IF NOT EXISTS idx_exec_planejamento ON execucao_campo(planejamento_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_metricas_demanda ON metricas_operacionais(demanda_id);

CREATE TABLE IF NOT EXISTS divergencias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,
    severidade      TEXT DEFAULT 'medio',
    entidade_tipo   TEXT,
    entidade_id     INTEGER,
    descricao       TEXT,
    status          TEXT DEFAULT 'aberta',
    resolvido_em    TEXT,
    resolvido_por   TEXT,
    detectado_em    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS justificativas_operacionais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    divergencia_id  INTEGER,
    motivo          TEXT,
    descricao       TEXT,
    tecnico         TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alertas_operacionais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT,
    prioridade      TEXT DEFAULT 'media',
    titulo          TEXT,
    descricao       TEXT,
    entidade_tipo   TEXT,
    entidade_id     INTEGER,
    status          TEXT DEFAULT 'ativo',
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    reconhecido_em  TEXT,
    reconhecido_por TEXT
);
"""

# Schema PostgreSQL — apenas diferenças de sintaxe
SCHEMA_PG = SCHEMA_SQLITE.replace(
    'INTEGER PRIMARY KEY AUTOINCREMENT',
    'SERIAL PRIMARY KEY'
)


# ── Conexão ────────────────────────────────────────────────────────────

def _connect_sqlite():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=10000')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


_pg_pool = None
_pg_pool_lock = None

def _get_pool():
    global _pg_pool, _pg_pool_lock
    import threading
    if _pg_pool_lock is None:
        _pg_pool_lock = threading.Lock()
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:  # double-check
                _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=15,   # Railway Pro suporta até ~97 conexões simultâneas
                    dsn=_DATABASE_URL,
                    connect_timeout=10,
                )
    return _pg_pool


def _get_pool_conn(pool, retries=4, delay=0.25):
    """Tenta pegar conexão do pool com retries — evita PoolError imediato."""
    import time
    last_err = None
    for i in range(retries):
        try:
            return pool.getconn()
        except psycopg2.pool.PoolError as e:
            last_err = e
            if i < retries - 1:
                time.sleep(delay * (i + 1))
    raise last_err


def _connect():
    return _connect_pg() if USE_PG else _connect_sqlite()

def _connect_pg():
    pool = _get_pool()
    raw = _get_pool_conn(pool)
    raw.autocommit = False
    return _PGConn(raw, pool=pool)   # passa pool → close() vai devolver, não fechar


@contextmanager
def get_db():
    if USE_PG:
        pool = _get_pool()
        raw = _get_pool_conn(pool)
        raw.autocommit = False
        conn = _PGConn(raw, pool=pool)
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            # putconn direto aqui (mais seguro que delegar ao close)
            try:
                pool.putconn(raw)
                conn._closed = True  # marca como devolvida para evitar double-putconn
            except Exception:
                pass
    else:
        conn = _connect_sqlite()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ── Helper: colunas de uma tabela ──────────────────────────────────────

def _get_table_cols(conn, table):
    """Retorna lista de nomes de colunas de uma tabela."""
    if USE_PG:
        rows = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = ? AND table_schema = 'public'",
            (table,)
        ).fetchall()
    else:
        rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
    return [r['name'] for r in rows]


def _add_col(conn, table, col, col_type):
    """Adiciona coluna se não existir (idempotente)."""
    try:
        if USE_PG:
            # ADD COLUMN IF NOT EXISTS evita 50+ queries a information_schema
            conn.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}')
        else:
            cols = _get_table_cols(conn, table)
            if col not in cols:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')
    except Exception as e:
        print(f'[migrate] {table}.{col}: {e}')


# ── init_db ────────────────────────────────────────────────────────────

_db_ready = False

def init_db():
    """Cria tabelas se não existirem. Idempotente. Roda só 1x por processo."""
    global _db_ready
    if _db_ready:
        return
    schema = SCHEMA_PG if USE_PG else SCHEMA_SQLITE
    with get_db() as conn:
        conn.executescript(schema)
        conn.executescript(SCHEMA_INDEXES)
        _migrate(conn)
        count = conn.execute('SELECT COUNT(*) AS c FROM amostradores').fetchone()['c']
    _db_ready = True
    if count == 0:
        _auto_seed()


def _migrate(conn):
    """Garante que todas as colunas e índices existam. Idempotente."""

    # ── empresas ──
    _add_col(conn, 'empresas', 'pendente', 'INTEGER DEFAULT 0')
    # Cadastro completo da empresa — preenchido automaticamente ao gerar laudo
    # (enriquecer_empresa). Reusado nos próximos laudos/planilhas (cruzamento).
    for _c in ('cep', 'endereco', 'numero', 'bairro', 'cnae', 'descricao_cnae'):
        _add_col(conn, 'empresas', _c, 'TEXT')
    # grau_risco (NR-4, Quadro I — decorre do CNAE). Fica FORA do backfill
    # automático de propósito: os formulários de laudo trazem '3' como default,
    # então gravar o que veio do form encheria o cadastro de grau 3 falso. É
    # preenchido à mão por empresa e consumido pelo PGR (28/07/2026).
    _add_col(conn, 'empresas', 'grau_risco', 'TEXT')

    # ── demandas: campos Planner ──
    demandas_extra = {
        'nome_tarefa': 'TEXT', 'data_conclusao': 'TEXT', 'responsavel': 'TEXT',
        'status_planner': 'TEXT', 'progresso': 'INTEGER DEFAULT 0',
        'checklist': 'TEXT', 'checklist_prog': 'TEXT', 'bucket': 'TEXT',
        'etiquetas': 'TEXT', 'descricao': 'TEXT', 'cnpj': 'TEXT',
        'tem_comentarios': 'INTEGER DEFAULT 0',
        'contato_feito': 'INTEGER DEFAULT 0', 'contato_feito_em': 'TEXT',
        'contato_feito_por': 'TEXT', 'contato_resultado': 'TEXT DEFAULT NULL',
        'contato_obs': 'TEXT DEFAULT NULL', 'proximo_contato': 'TEXT DEFAULT NULL',
        'planner_task_id': 'TEXT',
        'planner_plan_id': 'TEXT', 'planner_plan_nome': 'TEXT',
        'planner_bucket_id': 'TEXT', 'planner_bucket': 'TEXT',
        'planner_group_id': 'TEXT', 'planner_group_nome': 'TEXT',
        'titulo': 'TEXT', "prioridade": "TEXT DEFAULT 'media'",
        'percent_complete': 'INTEGER DEFAULT 0', 'criado_em_ms': 'TEXT',
        'concluido_em_ms': 'TEXT', 'ms_assignee_id': 'TEXT',
        'ms_assignees_json': 'TEXT', 'etiquetas_json': 'TEXT',
        "origem": "TEXT DEFAULT 'manual'",
        'atualizado_em': 'TEXT DEFAULT CURRENT_TIMESTAMP',
        'empresa_match_score': 'REAL', 'empresa_match_metodo': 'TEXT',
        "tipo_demanda": "TEXT DEFAULT 'operacional'",
    }
    for col, tipo in demandas_extra.items():
        _add_col(conn, 'demandas', col, tipo)

    # Motor inteligente: score de confiança e fila de revisão humana
    # IMPORTANT: usar _add_col() — usa ADD COLUMN IF NOT EXISTS no PG,
    # evita aborto de transação quando coluna já existe.
    for col, dfn in [
        ('needs_review',  'INTEGER DEFAULT 0'),
        ('extracao_json', 'TEXT DEFAULT NULL'),
        ('agentes_manual','TEXT DEFAULT NULL'),
    ]:
        _add_col(conn, 'demandas', col, dfn)

    # Coluna dias_estimados na tabela planejamentos
    _add_col(conn, 'planejamentos', 'dias_estimados',     'INTEGER DEFAULT NULL')
    _add_col(conn, 'planejamentos', 'checklist_prevista', 'TEXT DEFAULT NULL')
    _add_col(conn, 'planejamentos', 'divergencias_json',  'TEXT DEFAULT NULL')

    # Assinatura da visita: data-URL base64 do PNG, persistida no banco.
    # (Antes era salva em disco, mas o filesystem do Railway é efêmero e
    #  apagava a evidência a cada redeploy.)
    _add_col(conn, 'visitas_tecnicas', 'assinatura', 'TEXT DEFAULT NULL')
    # Nº da OS digitado em visita avulsa (sem planejamento vinculado)
    _add_col(conn, 'visitas_tecnicas', 'numero_os', 'TEXT DEFAULT NULL')
    # Assinatura do responsável da empresa (mobile envia 2 assinaturas)
    _add_col(conn, 'visitas_tecnicas', 'assinatura_empresa', 'TEXT DEFAULT NULL')

    # Relatório de Visita obrigatório (Diretriz Mestra 11/06/2026):
    # assinatura com identificação + texto de ciência + carimbo de hora,
    # tipo de imprevisto padronizado e motivo quando não há assinatura.
    _add_col(conn, 'visitas_tecnicas', 'assinante_nome',        'TEXT DEFAULT NULL')
    _add_col(conn, 'visitas_tecnicas', 'assinante_cargo',       'TEXT DEFAULT NULL')
    _add_col(conn, 'visitas_tecnicas', 'assinado_em',           'TEXT DEFAULT NULL')
    _add_col(conn, 'visitas_tecnicas', 'ciencia_texto',         'TEXT DEFAULT NULL')
    _add_col(conn, 'visitas_tecnicas', 'imprevisto_tipo',       'TEXT DEFAULT NULL')
    _add_col(conn, 'visitas_tecnicas', 'sem_assinatura_motivo', 'TEXT DEFAULT NULL')

    # Idempotência do save mobile/offline: UUID gerado 1x no cliente por visita.
    # Reenvio (retry, fila offline, background sync) não cria visita duplicada.
    _add_col(conn, 'visitas_tecnicas', 'client_uuid', 'TEXT DEFAULT NULL')
    try:
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_visita_client_uuid '
                     'ON visitas_tecnicas(client_uuid) WHERE client_uuid IS NOT NULL')
    except Exception as e:
        print(f'[migrate] idx_visita_client_uuid: {e}')

    # Fotos da visita persistidas NO BANCO (filesystem do Railway é efêmero).
    # categoria: ambiente | atividade | equipamentos
    _pk_vf = 'SERIAL PRIMARY KEY' if USE_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS visita_fotos (
            id         {_pk_vf},
            visita_id  INTEGER NOT NULL,
            categoria  TEXT DEFAULT 'ambiente',
            data       TEXT NOT NULL,
            legenda    TEXT,
            criado_em  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_vfotos_visita ON visita_fotos(visita_id)')

    # Tabela de inventário de equipamentos (Phase 1 — Jun 2026)
    _pk_equip = 'SERIAL PRIMARY KEY' if USE_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS equipamentos_inventario (
            id              {_pk_equip},
            tipo            TEXT NOT NULL,
            marca           TEXT,
            modelo          TEXT,
            numero_serie    TEXT,
            compatibilidade TEXT DEFAULT \'\',
            status          TEXT DEFAULT \'disponivel\',
            cert_numero     TEXT,
            cert_validade   TEXT,
            observacao      TEXT,
            criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
            atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Seed inicial (só insere se vazio)
    try:
        _eq_cnt = conn.execute('SELECT COUNT(*) AS c FROM equipamentos_inventario').fetchone()
        _eq_cnt_val = _eq_cnt['c'] if hasattr(_eq_cnt, '__getitem__') else _eq_cnt[0]
        if _eq_cnt_val == 0:
            _EQUIP_SEED = [
                ('calibrador_ruido', 'Chrompack', 'Calibrador Sonoro', None, 'chrompack', 'disponivel', None, None, 'Calibrador Chrompack 1'),
                ('calibrador_ruido', 'Chrompack', 'Calibrador Sonoro', None, 'chrompack', 'disponivel', None, None, 'Calibrador Chrompack 2'),
                ('calibrador_ruido', 'Chrompack', 'Calibrador Sonoro', None, 'chrompack', 'disponivel', None, None, 'Calibrador Chrompack 3'),
                ('calibrador_ruido', 'Chrompack', 'Calibrador Sonoro', None, 'chrompack', 'disponivel', None, None, 'Calibrador Chrompack 4'),
                ('calibrador_ruido', 'Inlite',    'Calibrador Sonoro', None, 'inlite',    'disponivel', None, None, 'Calibrador Inlite'),
                # Frota real (série + nº de certificado conferidos nos PDFs de calibração).
                # Datas de calibração são aplicadas pelo botão "Carregar frota dos certificados".
                ('bomba', 'SKC',    'AIRLITE',  'A060502',      '', 'disponivel', '315125B',    None, 'Bomba SKC AIRLITE'),
                ('bomba', 'SKC',    'AIRLITE',  'A061553',      '', 'disponivel', '270925B',    None, 'Bomba SKC AIRLITE'),
                ('bomba', 'SKC',    'AIRLITE',  'A061585',      '', 'disponivel', '315025B',    None, 'Bomba SKC AIRLITE'),
                ('bomba', 'SKC',    'AIRLITE',  'A062462',      '', 'disponivel', '315225B',    None, 'Bomba SKC AIRLITE'),
                ('bomba', 'SKC',    'AIRLITE',  'A63555',       '', 'disponivel', '171922B',    None, 'Bomba SKC AIRLITE'),
                ('bomba', 'Gilian', 'BDX-II',   '20230702029',  '', 'disponivel', '2602A38356', None, 'Bomba Gilian BDX-II'),
                ('bomba', 'Gilian', 'BDX-II',   '20141201119',  '', 'disponivel', '2602A38357', None, 'Bomba Gilian BDX-II'),
                ('bomba', 'Gilian', 'BDX-II',   '20230702030',  '', 'disponivel', '2602A38358', None, 'Bomba Gilian BDX-II'),
                ('bomba', 'Gilian', 'BDX-II',   '20230702024',  '', 'disponivel', '2602A38359', None, 'Bomba Gilian BDX-II'),
                ('bomba', 'Formis', 'TURAM',    '2420120549',   '', 'disponivel', None,         None, 'Bomba Formis TURAM'),
                ('bomba', 'Formis', 'TURAM',    '2420120550',   '', 'disponivel', None,         None, 'Bomba Formis TURAM'),
                ('bomba', 'Formis', 'TURAM',    '2420120551',   '', 'disponivel', None,         None, 'Bomba Formis TURAM'),
                ('bomba', 'Inlite', 'VENTUSPRO','25040902602B', '', 'disponivel', '42.188-2025', None, 'Bomba Inlite'),
                ('bomba', 'Inlite', 'VENTUSPRO','25040903102B', '', 'disponivel', '42.187-2025', None, 'Bomba Inlite'),
                ('bomba', 'Inlite', 'VENTUSPRO','25040907102B', '', 'disponivel', '42.186-2025', None, 'Bomba Inlite'),
                ('vibrador',   'Chrompack', 'Vibrador', None, '', 'disponivel', None, None, 'Aparelho 1'),
                ('vibrador',   'Chrompack', 'Vibrador', None, '', 'disponivel', None, None, 'Aparelho 2'),
                ('termometro', 'Chrompack', 'Termômetro IBUTG', None, '', 'disponivel', None, None, 'Único operacional'),
            ]
            for _s in _EQUIP_SEED:
                conn.execute(
                    'INSERT INTO equipamentos_inventario (tipo, marca, modelo, numero_serie, compatibilidade, status, cert_numero, cert_validade, observacao) VALUES (?,?,?,?,?,?,?,?,?)',
                    _s
                )
    except Exception as _e:
        print(f'[migrate] equipamentos seed: {_e}')
    # Data da última calibração (validade = +2 anos). cert_validade fica como override manual.
    _add_col(conn, 'equipamentos_inventario', 'data_calibracao', 'TEXT')

    # Tabela de log de extração (rastreabilidade)
    # ATENÇÃO: AUTOINCREMENT é SQLite — PostgreSQL usa SERIAL
    _pk_exlog = 'SERIAL PRIMARY KEY' if USE_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS extraction_log (
            id              {_pk_exlog},
            demanda_id      INTEGER,
            planner_task_id TEXT,
            score_geral     REAL,
            needs_review    INTEGER DEFAULT 0,
            numero_os       TEXT,
            os_confianca    REAL,
            empresa_nome    TEXT,
            empresa_conf    REAL,
            agentes_json    TEXT,
            inconsistencias TEXT,
            conflitos       TEXT,
            warnings_json   TEXT,
            fontes_lidas    TEXT,
            extraido_em     TEXT,
            criado_em       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exlog_demanda ON extraction_log(demanda_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exlog_task ON extraction_log(planner_task_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exlog_review ON extraction_log(needs_review)')
    except Exception:
        pass

    # ── amostradores ──
    amostr_extra = {
        'data_envio_lab': 'TEXT', 'dias_validade': 'INTEGER DEFAULT 45',
        'lote': 'TEXT', 'observacao_venc': 'TEXT',
        'cert_numero': 'TEXT', 'cert_validade': 'TEXT',
        'cert_laboratorio': 'TEXT', 'cert_arquivo': 'TEXT',
        # Fluxo de status (29/05/2026): data em que virou 'concluido' (cert recebido)
        'data_conclusao': 'TEXT',
        # Data REAL do resultado/laudo (RA) lido do e-mail/PDF do lab (15/06/2026)
        'data_resultado': 'TEXT',
        # Arquivamento automático: 30 dias após concluído (não deletar)
        'arquivado':    'INTEGER DEFAULT 0',
        'arquivado_em': 'TEXT',
        # Reserva automática pelo planejamento (08/07/2026): plano dono da reserva
        'reservado_por_plano': 'INTEGER',
    }
    for col, tipo in amostr_extra.items():
        _add_col(conn, 'amostradores', col, tipo)

    # ── Migração de status legados → canônico (remove 'UTILIZADO?' etc.) ──
    # Idempotente: só atualiza linhas cujo status atual difere do canônico.
    try:
        rows = conn.execute('SELECT id, status FROM amostradores').fetchall()
        for r in rows:
            sid = r['id'] if hasattr(r, '__getitem__') else r[0]
            atual = (r['status'] if hasattr(r, '__getitem__') else r[1]) or ''
            canon = normalizar_status_amostrador(atual)
            if canon != atual:
                conn.execute('UPDATE amostradores SET status=? WHERE id=?', (canon, sid))
            # Backfill data_conclusao para concluídos antigos sem timestamp
            if canon == 'concluido':
                conn.execute(
                    "UPDATE amostradores SET data_conclusao=COALESCE(data_conclusao, atualizado_em) WHERE id=?",
                    (sid,))
    except Exception as e:
        print(f'[migrate] status amostradores: {e}')

    # ── baixas: amostrador passa a ser opcional (10/08/2026) ──
    # Medição feita por empresa terceira (ex.: Opus) não tem amostrador nosso.
    # A tabela nasceu com NOT NULL, então banco já existente precisa da migração.
    if USE_PG:
        try:
            conn.execute('ALTER TABLE baixas ALTER COLUMN amostrador_id DROP NOT NULL')
        except Exception as e:
            print(f'[migrate] baixas.amostrador_id nullable: {e}')
    else:
        # SQLite não remove NOT NULL por ALTER — recria a tabela (mesmo padrão do
        # fix de execucao_campo.visita_id). Idempotente: só entra se ainda for
        # NOT NULL. Sem isso o banco local recusa a baixa que a produção aceita.
        try:
            pragma = conn.execute('PRAGMA table_info(baixas)').fetchall()
            col = next((r for r in pragma if r['name'] == 'amostrador_id'), None)
            if col and col['notnull'] == 1:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS baixas_new (
                        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                        medicao_id          INTEGER NOT NULL,
                        amostrador_id       INTEGER,
                        avaliador           TEXT,
                        bomba               TEXT,
                        vazao_calibrada     REAL,
                        volume_recomendado  REAL,
                        tempo_calculado_min REAL,
                        tempo_calculado_max REAL,
                        data_medicao        TEXT,
                        observacao          TEXT,
                        motivo_sem_amostrador TEXT,
                        criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (medicao_id)    REFERENCES medicoes(id),
                        FOREIGN KEY (amostrador_id) REFERENCES amostradores(id)
                    );
                    -- Lista de colunas SEM motivo_sem_amostrador de proposito: a
                    -- tabela antiga nao tem essa coluna (ela entra logo abaixo
                    -- por _add_col). Nomear os campos evita depender da ordem.
                    INSERT INTO baixas_new
                      (id,medicao_id,amostrador_id,avaliador,bomba,vazao_calibrada,
                       volume_recomendado,tempo_calculado_min,tempo_calculado_max,
                       data_medicao,observacao,criado_em)
                      SELECT id,medicao_id,amostrador_id,avaliador,bomba,
                             vazao_calibrada,volume_recomendado,tempo_calculado_min,
                             tempo_calculado_max,data_medicao,observacao,criado_em
                      FROM baixas;
                    DROP TABLE baixas;
                    ALTER TABLE baixas_new RENAME TO baixas;
                ''')
        except Exception as e:
            print(f'[migrate] baixas.amostrador_id nullable (sqlite): {e}')

    # Motivo da baixa sem amostrador (obrigatorio quando amostrador_id e NULL)
    _add_col(conn, 'baixas', 'motivo_sem_amostrador', 'TEXT')

    # ── coletas_ruido ──
    for col, tipo in [('calibrador', 'TEXT'), ('unidade', 'TEXT'),
                      ('cidade', 'TEXT'), ('resp_empresa', 'TEXT'),
                      ('os', 'TEXT'), ('visita_id', 'INTEGER'),
                      ('planejamento_id', 'INTEGER'),
                      ('tecnico_login', 'TEXT')]:
        _add_col(conn, 'coletas_ruido', col, tipo)

    # ── coletas_ruido_func: horário de almoço (a coluna 'almoco' é só flag 0/1;
    #    o hh:mm que o técnico digita ia pro PDF e se perdia no banco) ──
    _add_col(conn, 'coletas_ruido_func', 'pausa', 'TEXT')
    _add_col(conn, 'coletas_ruido_func', 'almoco_fim', 'TEXT')

    # ── coletas_quimico_amostr ──
    _add_col(conn, 'coletas_quimico_amostr', 'bomba', 'TEXT')

    # ── coletas_quimico / coletas_outros ──
    for tbl in ('coletas_quimico', 'coletas_outros'):
        _add_col(conn, tbl, 'visita_id', 'INTEGER')
        _add_col(conn, tbl, 'planejamento_id', 'INTEGER')
        _add_col(conn, tbl, 'tecnico_login', 'TEXT')

    # ── visitas_tecnicas ──
    for col, tipo in [('planejamento_id', 'INTEGER'),
                      ("resultado", "TEXT DEFAULT 'pendente'"),
                      ('observacao_geral', 'TEXT'),
                      ('atualizado_em', 'TEXT DEFAULT CURRENT_TIMESTAMP'),
                      ('acompanhante', 'TEXT'),
                      ('cargo_acompanhante', 'TEXT')]:
        _add_col(conn, 'visitas_tecnicas', col, tipo)

    # ── execucao_campo: campos mobile ──
    for col, tipo in [('acompanhante', 'TEXT'),
                      ('cargo_acompanhante', 'TEXT'),
                      ('dosimetros_usados', 'TEXT'),
                      ('bombas_usadas', 'TEXT'),
                      ('trabalhadores_json', 'TEXT')]:
        _add_col(conn, 'execucao_campo', col, tipo)

    # ── Camada de Consistência Operacional ──
    # Usar executescript() para proteger com SAVEPOINTs (idempotente no PG)
    _pk = 'SERIAL PRIMARY KEY' if USE_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    conn.executescript(f'''
        CREATE TABLE IF NOT EXISTS divergencias (
            id              {_pk},
            tipo            TEXT NOT NULL,
            severidade      TEXT DEFAULT 'medio',
            entidade_tipo   TEXT,
            entidade_id     INTEGER,
            descricao       TEXT,
            status          TEXT DEFAULT 'aberta',
            resolvido_em    TEXT,
            resolvido_por   TEXT,
            detectado_em    TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS justificativas_operacionais (
            id              {_pk},
            divergencia_id  INTEGER,
            motivo          TEXT,
            descricao       TEXT,
            tecnico         TEXT,
            criado_em       TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alertas_operacionais (
            id              {_pk},
            tipo            TEXT,
            prioridade      TEXT DEFAULT 'media',
            titulo          TEXT,
            descricao       TEXT,
            entidade_tipo   TEXT,
            entidade_id     INTEGER,
            status          TEXT DEFAULT 'ativo',
            criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
            reconhecido_em  TEXT,
            resolvido_em    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_div_status    ON divergencias(status);
        CREATE INDEX IF NOT EXISTS idx_div_tipo      ON divergencias(tipo);
        CREATE INDEX IF NOT EXISTS idx_div_sev       ON divergencias(severidade);
        CREATE INDEX IF NOT EXISTS idx_div_entidade  ON divergencias(entidade_tipo, entidade_id);
        CREATE INDEX IF NOT EXISTS idx_just_div      ON justificativas_operacionais(divergencia_id);
        CREATE INDEX IF NOT EXISTS idx_alerta_status ON alertas_operacionais(status);
        CREATE INDEX IF NOT EXISTS idx_alerta_prio   ON alertas_operacionais(prioridade)
    ''')

    # ── Resultado do laboratório por amostrador ──
    # O valor analítico não tinha onde morar: `medicoes` só tem status e
    # `coletas_quimico_amostr` só tem vazão/volume, então a concentração só passava
    # a existir DENTRO do .docx do laudo. Aqui ela vira dado: uma linha por
    # (amostrador, agente, fonte) — 'pdf' é o laudo do lab, 'digitado' é o que o
    # técnico lançou. Guardar as duas é o que permite marcar divergência sem
    # nenhuma das duas sobrescrever a outra (decisão do Matheus, 05/08/2026).
    conn.executescript(f'''
        CREATE TABLE IF NOT EXISTS resultados_lab (
            id              {_pk},
            amostrador_cod  TEXT NOT NULL,
            amostrador_id   INTEGER,
            ra_num          TEXT,
            agente          TEXT,
            agente_key      TEXT,
            unidade         TEXT,
            valor_txt       TEXT,
            valor_num       REAL,
            nao_detectado   INTEGER DEFAULT 0,
            fracao          TEXT,
            lt_nr15         TEXT,
            lt_twa          TEXT,
            lt_stel         TEXT,
            trabalhador     TEXT,
            data_analise    TEXT,
            fonte           TEXT NOT NULL,
            origem          TEXT,
            criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
            atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reslab_chave
            ON resultados_lab(amostrador_cod, agente_key, fonte);
        CREATE INDEX IF NOT EXISTS idx_reslab_ra   ON resultados_lab(ra_num);
        CREATE INDEX IF NOT EXISTS idx_reslab_amos ON resultados_lab(amostrador_id)
    ''')

    # ── planner_raw_tasks: colunas extras ──
    raw_extra = {
        'planner_plan_id': 'TEXT', 'planner_plan_nome': 'TEXT',
        'planner_bucket_id': 'TEXT', 'planner_bucket': 'TEXT',
        'planner_group_id': 'TEXT', 'planner_group_nome': 'TEXT',
        'titulo': 'TEXT', 'descricao': 'TEXT',
        'checklist_json': 'TEXT', 'raw_json': 'TEXT',
        'percent_complete': 'INTEGER DEFAULT 0', 'prazo': 'TEXT',
        'criado_em_ms': 'TEXT', 'concluido_em_ms': 'TEXT',
        'ms_assignee_id': 'TEXT', 'ms_assignees_json': 'TEXT',
        'etiquetas_json': 'TEXT', "sync_status": "TEXT DEFAULT 'raw'",
        'ignored_reason': 'TEXT',
        'synced_at': 'TEXT DEFAULT CURRENT_TIMESTAMP', 'processed_at': 'TEXT',
    }
    for col, tipo in raw_extra.items():
        _add_col(conn, 'planner_raw_tasks', col, tipo)

    # ── Índice único planner_task_id ──
    try:
        if USE_PG:
            conn.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_dem_planner_task
                ON demandas(planner_task_id)
                WHERE planner_task_id IS NOT NULL
            ''')
        else:
            conn.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_dem_planner_task
                ON demandas(planner_task_id)
                WHERE planner_task_id IS NOT NULL
            ''')
    except Exception:
        pass

    # ── Reclassifica demandas por bucket ──
    try:
        conn.execute("""
            UPDATE demandas SET status='concluida'
            WHERE (LOWER(COALESCE(planner_bucket,'')) LIKE '%entregue%'
                   OR LOWER(COALESCE(planner_bucket,'')) LIKE '%conclu%')
              AND status != 'concluida'
              AND origem = 'planner'
        """)
    except Exception as e:
        print(f'[migrate] reclassifica_bucket: {e}')

    # ── View operational_demands ──
    try:
        if USE_PG:
            conn.execute('DROP VIEW IF EXISTS operational_demands')
            conn.execute(f'''
                CREATE VIEW operational_demands AS
                SELECT d.*,
                       e.nome AS empresa_nome,
                       e.cnpj AS empresa_cnpj,
                       e.pendente AS empresa_pendente,
                       COALESCE(u.display_name, \'\') AS responsavel_nome,
                       CASE
                         WHEN LOWER(COALESCE(d.planner_bucket,\'\')) LIKE \'%entregue%\'
                           OR LOWER(COALESCE(d.planner_bucket,\'\')) LIKE \'%conclu%\'
                         THEN \'concluida\'
                         WHEN d.status = \'em_andamento\' THEN \'em_andamento\'
                         ELSE \'aberta\'
                       END AS operational_status
                FROM demandas d
                JOIN empresas e ON e.id = d.empresa_id
                LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
                WHERE d.tipo_demanda NOT IN (\'interna\', \'administrativa\')
                  AND d.empresa_id > 0
                  AND d.origem = \'planner\'
                  AND UPPER(d.titulo) NOT LIKE \'%PROCESSO ANTIGO%\'
            ''')
        else:
            conn.executescript('''
                DROP VIEW IF EXISTS operational_demands;
                CREATE VIEW operational_demands AS
                SELECT d.*,
                       e.nome AS empresa_nome,
                       e.cnpj AS empresa_cnpj,
                       e.pendente AS empresa_pendente,
                       COALESCE(u.display_name, '') AS responsavel_nome,
                       CASE
                         WHEN LOWER(COALESCE(d.planner_bucket,'')) LIKE '%entregue%'
                           OR LOWER(COALESCE(d.planner_bucket,'')) LIKE '%conclu%'
                         THEN 'concluida'
                         WHEN d.status = 'em_andamento' THEN 'em_andamento'
                         ELSE 'aberta'
                       END AS operational_status
                FROM demandas d
                JOIN empresas e ON e.id = d.empresa_id
                LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
                WHERE d.tipo_demanda NOT IN ('interna', 'administrativa')
                  AND d.empresa_id > 0
                  AND d.origem = 'planner'
                  AND UPPER(d.titulo) NOT LIKE '%PROCESSO ANTIGO%';
            ''')
    except Exception as e:
        print(f'[migrate] view operational_demands: {e}')

    # ── SQLite-only: corrigir execucao_campo visita_id NOT NULL ──
    if not USE_PG:
        try:
            pragma = conn.execute('PRAGMA table_info(execucao_campo)').fetchall()
            if pragma:
                visita_col = next((r for r in pragma if r['name'] == 'visita_id'), None)
                if visita_col and visita_col['notnull'] == 1:
                    conn.executescript('''
                        CREATE TABLE IF NOT EXISTS execucao_campo_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            visita_id INTEGER,
                            planejamento_id INTEGER,
                            agentes_executados TEXT,
                            agentes_nao_executados TEXT,
                            agentes_adicionados TEXT,
                            justificativa_causa TEXT,
                            cobravel INTEGER DEFAULT 0,
                            observacao TEXT,
                            criado_em TEXT DEFAULT CURRENT_TIMESTAMP
                        );
                        INSERT INTO execucao_campo_new
                          SELECT id,visita_id,planejamento_id,agentes_executados,
                                 agentes_nao_executados,agentes_adicionados,
                                 justificativa_causa,cobravel,observacao,criado_em
                          FROM execucao_campo;
                        DROP TABLE execucao_campo;
                        ALTER TABLE execucao_campo_new RENAME TO execucao_campo;
                    ''')
        except Exception as e:
            print(f'[migrate] execucao_campo nullable: {e}')

    # ── usuarios: registro_mte ──
    _add_col(conn, 'usuarios', 'registro_mte', 'TEXT')

    # ── catálogo de TSTs (nome ↔ MTE) ──
    _corrigir_nomes_cadastro(conn)   # antes do seed, ver docstring
    _seed_tecnicos_mte(conn)
    _desativar_tecnicos_desligados(conn)   # depois do seed, senão volta ativo
    _corrigir_registros(conn)              # idem: o seed não atualiza quem existe

    # ── planejamentos: dias_estimados e cnpj ──
    _add_col(conn, 'planejamentos', 'dias_estimados', 'INTEGER DEFAULT NULL')
    _add_col(conn, 'planejamentos', 'cnpj', 'TEXT')

    # ── Garante que admin seed existe e é role=admin ──
    try:
        conn.execute(
            "UPDATE usuarios SET role='admin', ativo=1 WHERE email='engenharia19@ocupacional.com.br'"
        )
    except Exception:
        pass

    # ── Cria admin se tabela vazia (primeiro deploy no Railway) ──
    try:
        from werkzeug.security import generate_password_hash
        _crow = conn.execute('SELECT COUNT(*) AS c FROM usuarios').fetchone()
        count = (_crow.get('c', 0) if isinstance(_crow, dict) else _crow[0]) if _crow else 0
        if count == 0:
            pwd = os.environ.get('ADMIN_SETUP_PASSWORD', 'Ocupacional@2026')
            conn.execute(
                "INSERT INTO usuarios (nome, email, senha_hash, role, ativo) "
                "VALUES (?,?,?,?,1)",
                ('Matheus Costa', 'engenharia19@ocupacional.com.br',
                 generate_password_hash(pwd), 'admin')
            )
            print(f'[db] admin criado: engenharia19@ocupacional.com.br / {pwd}')
    except Exception as e:
        print(f'[db] seed admin erro: {e}')


def _auto_seed():
    """Importa planilhas seed se existirem, populando DB vazio."""
    seed_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seed')
    if not os.path.isdir(seed_dir):
        return
    try:
        from .import_xlsx import importar_amostradores, importar_medicoes
    except Exception as e:
        print(f'[controle] auto_seed import erro: {e}')
        return

    for fname, fn in [('amostradores.xlsx', importar_amostradores),
                      ('medicoes.xlsx', importar_medicoes)]:
        fpath = os.path.join(seed_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, 'rb') as f:
                    res = fn(f.read())
                print(f'[controle] seed {fname}: {res}')
            except Exception as e:
                print(f'[controle] seed {fname} erro: {e}')


# ── Helpers ────────────────────────────────────────────────────────────

def registrar_evento(tipo, descricao=None, ref_id=None, ref_tipo=None, usuario=None, ip=None):
    """Registra evento no log de auditoria. Silencioso em caso de erro."""
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo, usuario, ip) '
                'VALUES (?,?,?,?,?,?)',
                (tipo, descricao, ref_id, ref_tipo, usuario, ip)
            )
    except Exception as e:
        print(f'[eventos] {e}')


def registrar_sync(tipo, arquivo_nome, novos=0, atualizados=0, usuario='Matheus'):
    with get_db() as conn:
        conn.execute(
            'INSERT INTO sync_log (tipo, arquivo_nome, registros_novos, registros_atu, usuario) '
            'VALUES (?, ?, ?, ?, ?)',
            (tipo, arquivo_nome, novos, atualizados, usuario))


def list_sync_log(limit=20):
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM sync_log ORDER BY criado_em DESC LIMIT ?',
            (limit,)).fetchall()]


def row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(row)


# ── Catálogo de TSTs: nome ↔ registro MTE ──────────────────────────────
# Lista consolidada pelo grupo em 03/08/2026. Registros normalizados para o
# padrão da casa (7 posições + /UF) — quem informou sem os zeros à esquerda
# ("57462-MG") foi completado. Isto é CADASTRO, não login: o técnico entra
# aqui para aparecer no select dos documentos mesmo sem usar o sistema.
# Admin edita/inclui em /controle/admin/usuarios (o seed nunca sobrescreve
# nem ressuscita quem foi editado ou desativado à mão).
TECNICOS_MTE_SEED = [
    ('Matheus Vinícius Costa',                    '0086038/MG'),
    ('Vitoria Batista Ribeiro',                   '0071934/MG'),
    ('Heloisa Magalhães Assis',                   '0071884/MG'),
    ('Maria Letícia Profeta de Souza',            '0082402/MG'),
    # A lista do grupo trouxe "57462-MG", mas o registro confirmado por ele é
    # 0074881/MG (mesmo valor que saiu no certificado, Assinador 056a8c8).
    ('Guilherme Henrique Alkimim de Souza',       '0074881/MG'),
    ('Evelyn Nathally Duarte',                    '0071640/MG'),
    ('Larissa Elen Ferreira Gomes',               '0082855/MG'),
    ('Tauane dos Santos Virginio',                '0070341/MG'),
    ('Giovana Franciele de Carvalho Brito',       '0085489/MG'),
    ('Karina Lorrayne dos Santos Nepomuceno',     '0074997/MG'),
    ('Valéria de Jesus',                          '0026468/MG'),
    ('Bárbara Diamantino Santos',                 '0062238/MG'),
    ('Wesley Vieira Rodrigues',                   '0079720/MG'),
    ('Raniere Costa Vales',                       '0078034/RJ'),
    ('Isabelle Maria Vieira Aguiar Kalil',        '0058872/MG'),
    ('Denise dos Santos Soares',                  '0037083/MG'),
    ('Luiz Roberto de Assis Menezes',             '0081584/MG'),
    ('Kellen Ferreira',                           '0062879/MG'),
    ('Izaelen Gutemberg Rodrigues Alves Ribeiro', '0084944/MG'),
    ('Mario Lúcio Alves de Souza Aguiar',         '0013214/MG'),
    ('Rafael Junior Souza Nascimento',            '0073339/MG'),
    ('Arthur Fernandes Menezes',                  '0068404/MG'),
    ('Rosane Xavier Kalil',                       '0059387/MG'),
    ('Rômulo Augusto Dias',                       '0045121/MG'),
    ('Sued Mimrop Rodrigues da Silva',            '0065338/MG'),
    ('Kelly Elissama Firmino',                    '0072372/MG'),
    ('Tainara Gomes da Silva',                    '0062311/MG'),
    ('Helbert Gonçalves de Oliveira',             '0045376/MG'),
    # Estavam na aba Dados da BI com registro e ficaram fora da coleta do grupo.
    # Sobrenome da Janaina vem da BI (o AD só tem "Janaina"); o do Luiz Fernando
    # vem do AD (`coordenacao@`, surname Tavares) — ele NÃO é o Luiz Roberto.
    ('Janaina Bispo',                             '0074738/MG', 'TST'),
    ('Luiz Fernando Tavares',                     '0040977/MG',
     'Supervisor de Segurança do Trabalho'),
]


def norm_nome(s):
    """Chave de comparação de nome: sem acento, sem pontuação, minúsculo."""
    import unicodedata, re as _re
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    s = _re.sub(r'[^A-Za-z0-9 ]', ' ', s).lower()
    return _re.sub(r'\s+', ' ', s).strip()


def norm_registro_mte(s):
    """Normaliza registro MTE para o padrão da casa: 7 dígitos + /UF.
    '57462-MG' → '0057462/MG' · '007803.4/RJ' → '0078034/RJ'. Registro de
    engenheiro (CREA) e texto irreconhecível voltam intactos."""
    import re as _re
    raw = str(s or '').strip().upper()
    if not raw or 'CREA' in raw:
        return raw
    m = _re.search(r'([A-Z]{2})\s*$', raw)
    uf = m.group(1) if m else ''
    digits = _re.sub(r'\D', '', raw[:m.start()] if m else raw)
    if not digits:
        return raw
    num = digits.zfill(7) if len(digits) <= 7 else digits
    return f'{num}/{uf}' if uf else num


def _mesma_pessoa(a, b):
    """Nome curto vs nome completo é a MESMA pessoa ('Matheus Costa' =
    'Matheus Vinícius Costa'): 1º nome igual + todos os tokens do curto contidos
    no longo. Sem isso a pessoa aparece duas vezes no select do documento.

    Exigir também o ÚLTIMO sobrenome igual (como era até 03/08) deixava de fora
    o nome curto da BI que corta sobrenome do fim: "Luiz Fernando" ×
    "Luiz Fernando Tavares" dava False e o registro dele saía vazio. Mesma regra
    do `_mesmo_tst` do Assinador. Não confunde pessoas diferentes que só
    compartilham o 1º nome: "Luiz Fernando" ⊄ "Luiz Roberto de Assis Menezes"."""
    ta, tb = norm_nome(a).split(), norm_nome(b).split()
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return curto[0] == longo[0] and set(curto).issubset(set(longo))


# Nome de cadastro que estava incompleto/minúsculo. O documento imprime o nome
# do `usuarios`, então "Kelly Firmino" saía em laudo no lugar do nome completo.
# Corrigido a pedido do Matheus em 03/08/2026.
# O nome de destino NUNCA aparece como origem — senão a correção fica em ciclo
# e o resultado passa a depender da ordem da lista.
CORRECOES_NOME = [
    ('Kelly Firmino',                   'Kelly Elissama Firmino'),
    ('matheus costa',                   'Matheus Vinícius Costa'),
    ('Matheus Costa',                   'Matheus Vinícius Costa'),
    ('Matheus Vinícius Costa Carvalho', 'Matheus Vinícius Costa'),
    # Login dele é só "Luiz Fernando"; o AD (coordenacao@) diz Tavares. Sem
    # alinhar, o catálogo entra como pessoa nova e ele aparece 2x no select.
    ('Luiz Fernando',                   'Luiz Fernando Tavares'),
]

# Onde o nome da pessoa é gravado como TEXTO e o app FILTRA por ele ("minhas
# coletas", "meus planejamentos"). Renomear só `usuarios` deixaria o histórico
# órfão do filtro.
# FORA de propósito:
#   eventos.usuario / sync_log.usuario → log de auditoria, registra quem fez na
#     época; reescrever histórico de auditoria é adulteração, não correção.
#   demandas.responsavel → quem manda é o display name do Planner, o sync
#     sobrescreve no ciclo seguinte.
_COLS_NOME_PESSOA = (
    ('usuarios', 'nome'), ('tecnicos_mte', 'nome'),
    ('coletas_ruido', 'tecnico'), ('coletas_outros', 'avaliador'),
    ('coletas_quimico', 'responsavel_coleta'),
    ('amostradores', 'avaliador'), ('baixas', 'avaliador'),
    ('planejamentos', 'tecnico'), ('visitas_tecnicas', 'tecnico'),
    ('metricas_operacionais', 'tecnico'),
    ('demandas', 'contato_feito_por'), ('contatos_empresa', 'feito_por'),
)


# Quem saiu da empresa. A lista do grupo (03/08) trouxe gente já desligada, e
# nome de desligado no select vira documento assinado por quem não trabalha mais
# aqui. Desativa (ativo=0) em vez de apagar — apagar faria o seed ressuscitar.
# Fonte de "está ativo?": coluna STATUS da aba Dados da BI / RH. NUNCA o AD:
# a conta do Rômulo seguia habilitada no dia do desligamento.
TECNICOS_DESLIGADOS = [
    'Rômulo Augusto Dias',    # desligado 03/08/2026 (Assinador 056a8c8)
    'Kellen Ferreira',        # STATUS=DESLIGADO na aba Dados da BI (linha 6)
]

# Registro que já foi semeado errado e precisa ser corrigido no banco — o seed
# só INSERE quem falta, então mudar a lista acima não conserta quem já entrou.
CORRECOES_REGISTRO = [
    # veio "57462-MG" da lista do grupo; o confirmado é 0074881/MG
    ('Guilherme Henrique Alkimim de Souza', '0074881/MG'),
]


def _corrigir_registros(conn):
    """Idempotente. Não toca em registro editado à mão para OUTRO valor que não
    o errado conhecido — só troca quando ainda está com o valor antigo."""
    for nome, certo in CORRECOES_REGISTRO:
        try:
            conn.execute(
                'UPDATE tecnicos_mte SET registro_mte=?, atualizado_em=? '
                'WHERE nome_norm=? AND registro_mte <> ?',
                (norm_registro_mte(certo), datetime.now().isoformat(timespec='seconds'),
                 norm_nome(nome), norm_registro_mte(certo)))
        except Exception as e:
            print(f'[migrate] registro {nome}: {e}')


def _desativar_tecnicos_desligados(conn):
    """Idempotente: quem já está ativo=0 não é tocado de novo."""
    for nome in TECNICOS_DESLIGADOS:
        try:
            cur = conn.execute(
                'UPDATE tecnicos_mte SET ativo=0, atualizado_em=? '
                'WHERE nome_norm=? AND ativo=1',
                (datetime.now().isoformat(timespec='seconds'), norm_nome(nome)))
            if getattr(cur, 'rowcount', 0):
                print(f'[db] catálogo TST: {nome} desativado (desligado)')
        except Exception as e:
            print(f'[migrate] desligado {nome}: {e}')


def _corrigir_nomes_cadastro(conn):
    """Aplica CORRECOES_NOME. Idempotente (segunda passada não acha nada).
    Roda ANTES do seed do catálogo: se rodasse depois, o seed já teria inserido
    o nome novo e o rename bateria no índice único de nome_norm."""
    for errado, certo in CORRECOES_NOME:
        if errado == certo:
            continue
        for tab, col in _COLS_NOME_PESSOA:
            try:
                conn.execute(f'UPDATE {tab} SET {col}=? WHERE {col}=?', (certo, errado))
            except Exception as e:
                print(f'[migrate] nome {tab}.{col}: {e}')
    try:
        for _, certo in CORRECOES_NOME:
            conn.execute('UPDATE tecnicos_mte SET nome_norm=? WHERE nome=?',
                         (norm_nome(certo), certo))
    except Exception as e:
        print(f'[migrate] nome_norm: {e}')


def _seed_tecnicos_mte(conn):
    """Popula o catálogo com a lista do grupo. Idempotente: só INSERE quem
    ainda não existe — registro corrigido ou nome desativado pelo admin não
    é sobrescrito nem volta do túmulo no próximo deploy."""
    try:
        rows = conn.execute('SELECT nome_norm FROM tecnicos_mte').fetchall()
        existentes = {(row_to_dict(r).get('nome_norm') or '') for r in rows}
        novos = 0
        for entrada in TECNICOS_MTE_SEED:
            # 3º item (cargo) é opcional — quase todos são TST, mas quem executa
            # visita e assina não é só TST (o Luiz Fernando é supervisor).
            nome, reg, cargo = (tuple(entrada) + ('TST',))[:3]
            nn = norm_nome(nome)
            if not nn or nn in existentes:
                continue
            conn.execute(
                'INSERT INTO tecnicos_mte (nome, nome_norm, registro_mte, cargo, ativo, origem) '
                'VALUES (?,?,?,?,1,?)',
                (nome, nn, norm_registro_mte(reg), cargo or 'TST', 'seed')
            )
            existentes.add(nn)
            novos += 1
        if novos:
            print(f'[db] catálogo TST: {novos} inseridos')
        # Espelha o MTE em quem TEM login: campo vazio é preenchido, e registro
        # sem UF ("0072372", caso da Kelly em prod) é completado quando os
        # dígitos são os mesmos do catálogo — senão o documento sai "MTE
        # 0072372" e o padrão da casa é "0072372/MG". Registro com dígitos
        # DIFERENTES nunca é tocado: aí é divergência para humano resolver.
        urows = [row_to_dict(r) for r in conn.execute(
            'SELECT id, nome, registro_mte FROM usuarios').fetchall()]
        crows = [row_to_dict(r) for r in conn.execute(
            "SELECT nome, registro_mte FROM tecnicos_mte "
            "WHERE ativo=1 AND COALESCE(registro_mte,'') <> ''").fetchall()]
        _digs = lambda s: ''.join(ch for ch in str(s or '') if ch.isdigit()).lstrip('0')
        for u in urows:
            atual = (u.get('registro_mte') or '').strip()
            for c in crows:
                if not _mesma_pessoa(u['nome'], c['nome']):
                    continue
                novo = c['registro_mte']
                if atual and not (_digs(atual) == _digs(novo) and '/' not in atual):
                    break   # já tem registro próprio e completo — não mexer
                conn.execute('UPDATE usuarios SET registro_mte=? WHERE id=?',
                             (novo, u['id']))
                print(f"[db] MTE de {u['nome']}: {atual or '(vazio)'} → {novo}")
                break
    except Exception as e:
        print(f'[db] seed tecnicos_mte: {e}')


def list_tecnicos_mte(incluir_inativos=False):
    """Catálogo cru, para a tela de admin."""
    sql = ('SELECT id, nome, registro_mte, cargo, ativo, origem, criado_em, '
           'atualizado_em FROM tecnicos_mte')
    if not incluir_inativos:
        sql += ' WHERE ativo=1'
    sql += ' ORDER BY nome'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql).fetchall()]


def list_tecnicos_documento():
    """Fonte única do select de técnico dos documentos: usuários com login +
    catálogo de TSTs. Dedupe por pessoa (o usuário vence, porque tem id para
    auto-selecionar) e o MTE vazio do usuário é completado pelo catálogo."""
    with get_db() as conn:
        urows = [row_to_dict(r) for r in conn.execute(
            'SELECT id, nome, registro_mte FROM usuarios WHERE ativo=1').fetchall()]
        crows = [row_to_dict(r) for r in conn.execute(
            'SELECT id, nome, registro_mte, cargo FROM tecnicos_mte WHERE ativo=1').fetchall()]
    out = []
    for u in urows:
        mte = (u.get('registro_mte') or '').strip()
        if not mte:
            for c in crows:
                if _mesma_pessoa(u['nome'], c['nome']):
                    mte = (c.get('registro_mte') or '').strip()
                    break
        out.append({'id': u['id'], 'nome': u['nome'], 'registro_mte': mte,
                    'cargo': 'TST', 'origem': 'usuario'})
    for c in crows:
        if any(_mesma_pessoa(c['nome'], u['nome']) for u in urows):
            continue
        # id textual ('c12') — nunca colide com o id numérico do usuário
        # logado na hora de auto-selecionar a opção no select.
        out.append({'id': f"c{c['id']}", 'nome': c['nome'],
                    'registro_mte': (c.get('registro_mte') or '').strip(),
                    'cargo': c.get('cargo') or 'TST', 'origem': 'catalogo'})
    out.sort(key=lambda x: norm_nome(x['nome']))
    return out


def mte_por_nome(nome):
    """Registro MTE a partir de um nome livre (o que vem do formulário).
    Procura em usuarios e no catálogo: exato primeiro, depois nome curto."""
    nome = (nome or '').strip()
    if not nome:
        return ''
    alvo = norm_nome(nome)
    try:
        with get_db() as conn:
            cands = []
            for sql in ("SELECT nome, registro_mte FROM usuarios "
                        "WHERE ativo=1 AND COALESCE(registro_mte,'') <> ''",
                        "SELECT nome, registro_mte FROM tecnicos_mte "
                        "WHERE ativo=1 AND COALESCE(registro_mte,'') <> ''"):
                cands += [row_to_dict(r) for r in conn.execute(sql).fetchall()]
        for c in cands:
            if norm_nome(c['nome']) == alvo:
                return (c['registro_mte'] or '').strip()
        for c in cands:
            if _mesma_pessoa(nome, c['nome']):
                return (c['registro_mte'] or '').strip()
    except Exception as e:
        print(f'[mte_por_nome] {e}')
    return ''


def salvar_tecnico_mte(nome, registro, cargo='TST', ativo=1, tid=None):
    """Insere ou atualiza um técnico do catálogo. Sem tid, casa pelo nome
    normalizado (evita duplicar a mesma pessoa)."""
    nome = (nome or '').strip()
    if not nome:
        raise ValueError('Nome é obrigatório')
    nn = norm_nome(nome)
    reg = norm_registro_mte(registro)
    agora = datetime.now().isoformat(timespec='seconds')
    with get_db() as conn:
        if tid is None:
            row = conn.execute('SELECT id FROM tecnicos_mte WHERE nome_norm=?',
                               (nn,)).fetchone()
            tid = row_to_dict(row)['id'] if row else None
        if tid:
            conn.execute(
                'UPDATE tecnicos_mte SET nome=?, nome_norm=?, registro_mte=?, '
                'cargo=?, ativo=?, atualizado_em=? WHERE id=?',
                (nome, nn, reg, cargo or 'TST', 1 if ativo else 0, agora, tid))
        else:
            cur = conn.execute(
                'INSERT INTO tecnicos_mte (nome, nome_norm, registro_mte, cargo, '
                'ativo, origem, atualizado_em) VALUES (?,?,?,?,?,?,?)',
                (nome, nn, reg, cargo or 'TST', 1 if ativo else 0, 'manual', agora))
            tid = cur.lastrowid
    return {'id': tid, 'nome': nome, 'registro_mte': reg,
            'cargo': cargo or 'TST', 'ativo': 1 if ativo else 0}


# ── Amostradores ───────────────────────────────────────────────────────

def arquivar_amostradores_concluidos(dias=30):
    """Arquiva (não deleta) amostradores concluídos há >= `dias` dias.
    Mantém auditoria/rastreabilidade — só saem da visão principal (TASK D).
    Retorna quantos foram arquivados nesta passada."""
    if USE_PG:
        cond = (f"status='concluido' AND COALESCE(arquivado,0)=0 "
                f"AND data_conclusao ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
                f"AND (CURRENT_DATE - data_conclusao::date) >= {int(dias)}")
    else:
        cond = (f"status='concluido' AND COALESCE(arquivado,0)=0 "
                f"AND data_conclusao GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' "
                f"AND CAST(julianday('now') - julianday(data_conclusao) AS INTEGER) >= {int(dias)}")
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        n = conn.execute(f"SELECT COUNT(*) c FROM amostradores WHERE {cond}").fetchone()['c']
        if n:
            conn.execute(
                f"UPDATE amostradores SET arquivado=1, arquivado_em=? WHERE {cond}", (now,))
            conn.commit()
    return n


# Condição do "estoque": o que está na prateleira, sem compromisso nenhum.
# Reservado/laboratorio/concluido/devolvido/manutencao/descartado NÃO entram —
# são ciclo em andamento ou histórico, arquivar sumiria com coleta viva.
_COND_ESTOQUE = ("status='disponivel' AND COALESCE(arquivado,0)=0 "
                 "AND reservado_por_plano IS NULL")


def arquivar_amostradores_estoque():
    """Arquiva em lote os amostradores EM ESTOQUE (status 'disponivel').

    Serve ao recadastro manual do inventário físico: quando o estoque do
    sistema deixa de refletir o que está na prateleira, ele sai da visão
    principal e o técnico reinsere à mão o que tem em mãos.

    NÃO deleta. Só marca `arquivado=1`, então as `baixas`, o RA e o histórico
    de cada dispositivo continuam íntegros e a operação é reversível — quem
    recadastrar o mesmo código cai em `reativar_amostrador()`.

    Retorna (quantidade, lista de códigos arquivados).
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT codigo FROM amostradores WHERE {_COND_ESTOQUE}').fetchall()
        codigos = [(r['codigo'] or '') for r in rows]
        if codigos:
            conn.execute(
                f"UPDATE amostradores SET arquivado=1, arquivado_em=? "
                f"WHERE {_COND_ESTOQUE}", (now,))
    return len(codigos), codigos


def contar_amostradores_estoque():
    """Quantos amostradores o arquivamento de estoque pegaria agora (preview)."""
    with get_db() as conn:
        return conn.execute(
            f'SELECT COUNT(*) c FROM amostradores WHERE {_COND_ESTOQUE}'
        ).fetchone()['c']


def reativar_amostrador(conn, aid, tipo=None, status='disponivel',
                        data_entrada=None, observacao=None):
    """Traz de volta um amostrador ARQUIVADO como se fosse cadastro novo.

    O código é a identidade física do dispositivo, então recadastrar um código
    que existe arquivado não pode abrir uma segunda linha: o mesmo amostrador
    passaria a ter dois ids e todo relatório agrupado por código contaria em
    dobro.

    Zera o que é do CICLO DE USO anterior (empresa, avaliador, datas de
    medição/envio/resultado/conclusão, reserva) e PRESERVA o que é do
    dispositivo em si — `lote` e os campos `cert_*` de calibração — além do
    histórico em `baixas`, que aponta para este mesmo id.

    Recebe `conn` para rodar dentro da transação de quem chamou.
    """
    sets = ['arquivado=0', 'arquivado_em=NULL', 'empresa_id=NULL',
            'avaliador=NULL', 'data_medicao=NULL', 'data_envio_lab=NULL',
            'data_resultado=NULL', 'data_conclusao=NULL',
            'reservado_por_plano=NULL', 'atualizado_em=CURRENT_TIMESTAMP',
            'status=?']
    params = [normalizar_status_amostrador(status or 'disponivel')]
    if tipo:
        sets.append('tipo=?')
        params.append(tipo)
    sets.append('data_entrada=?')
    params.append(data_entrada or datetime.now().strftime('%Y-%m-%d'))
    sets.append('observacao=?')
    params.append(observacao or '')
    params.append(aid)
    conn.execute(f"UPDATE amostradores SET {','.join(sets)} WHERE id=?", params)
    return aid


def list_amostradores(filtros=None):
    f = filtros or {}
    # Arquivamento automático ao listar (lazy) — independe de scheduler
    try:
        arquivar_amostradores_concluidos(30)
    except Exception as e:
        print(f'[controle] arquivamento auto falhou: {e}')
    sql = f"""
        SELECT a.*, e.nome AS empresa_nome,
               {_ds("COALESCE(NULLIF(a.data_medicao,''), NULLIF(a.data_entrada,''), a.atualizado_em)")} AS tempo_parado
        FROM amostradores a
        LEFT JOIN empresas e ON e.id = a.empresa_id
        WHERE 1=1
    """
    params = []
    # Por padrão esconde arquivados; arquivados=1 mostra só o histórico
    if str(f.get('arquivados', '')) in ('1', 'true', 'True'):
        sql += ' AND COALESCE(a.arquivado,0)=1'
    else:
        sql += ' AND COALESCE(a.arquivado,0)=0'
    if f.get('status'):
        sql += ' AND a.status = ?'; params.append(f['status'])
    if f.get('tipo'):
        sql += ' AND a.tipo = ?'; params.append(f['tipo'])
    if f.get('codigo'):
        sql += " AND LOWER(COALESCE(a.codigo,'')) LIKE LOWER(?)"; params.append(f'%{f["codigo"]}%')
    if f.get('empresa'):
        sql += " AND LOWER(COALESCE(e.nome,'')) LIKE LOWER(?)"; params.append(f'%{f["empresa"]}%')
    sql += ' ORDER BY a.atualizado_em DESC LIMIT 2000'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Demandas ───────────────────────────────────────────────────────────

def list_demandas(filtros=None):
    sql = f"""
        SELECT d.*, e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
               (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id = d.id) AS total_medicoes,
               (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id = d.id AND m.status='realizado') AS realizadas,
               (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id = d.id AND m.status='pendente') AS pendentes,
               {_ds("COALESCE(d.criado_em_ms, d.criado_em)")} AS dias_aberta,
               {_du("d.prazo")} AS dias_para_prazo
        FROM demandas d
        JOIN empresas e ON e.id = d.empresa_id
        WHERE 1=1
    """
    params = []
    f = filtros or {}
    if f.get('status'):
        sql += ' AND d.status = ?'; params.append(f['status'])
    if f.get('empresa'):
        sql += " AND LOWER(COALESCE(e.nome,'')) LIKE LOWER(?)"; params.append(f'%{f["empresa"]}%')
    if f.get('os'):
        sql += " AND LOWER(COALESCE(d.numero_os,'')) LIKE LOWER(?)"; params.append(f'%{f["os"]}%')
    if f.get('urgencia') == 'atrasada':
        if USE_PG:
            sql += (" AND d.status != 'concluida'"
                    " AND d.prazo IS NOT NULL AND d.prazo != ''"
                    " AND d.prazo::date < CURRENT_DATE")
        else:
            sql += (" AND d.status != 'concluida'"
                    " AND d.prazo IS NOT NULL AND d.prazo != ''"
                    " AND julianday(d.prazo) < julianday('now')")
    ordem = f.get('ordem', 'prazo')
    if ordem == 'empresa':
        sql += ' ORDER BY e.nome ASC, d.criado_em ASC LIMIT 2000'
    elif ordem == 'data_criacao':
        sql += ' ORDER BY d.criado_em ASC LIMIT 2000'
    else:
        sql += ' ORDER BY CASE WHEN d.status=\'concluida\' THEN 1 ELSE 0 END, d.prazo ASC LIMIT 2000'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def list_demandas_por_empresa(filtros=None):
    sql = f"""
        SELECT e.id AS empresa_id,
               e.nome AS empresa_nome,
               e.cnpj,
               e.email,
               e.telefone,
               e.contato,
               COUNT(DISTINCT d.id) AS total_demandas,
               SUM(CASE WHEN d.status='concluida' THEN 1 ELSE 0 END) AS demandas_concluidas,
               SUM(CASE WHEN d.status!='concluida' THEN 1 ELSE 0 END) AS demandas_pendentes,
               COALESCE(SUM(d.progresso), 0) / NULLIF(COUNT(DISTINCT d.id),0) AS progresso_medio,
               COUNT(m.id) AS total_medicoes,
               SUM(CASE WHEN m.status='realizado' THEN 1 ELSE 0 END) AS medicoes_realizadas,
               MIN(d.criado_em) AS demanda_mais_antiga,
               MIN(NULLIF(d.prazo,'')) AS prazo_mais_proximo,
               COALESCE(MAX(d.responsavel), MAX(u.display_name)) AS responsavel,
               MAX(d.contato_feito) AS contato_feito,
               MAX(d.contato_feito_em) AS contato_feito_em,
               MAX(d.contato_resultado) AS contato_resultado,
               MAX(d.contato_obs) AS contato_obs,
               MIN(CASE WHEN d.status!='concluida' AND d.proximo_contato IS NOT NULL THEN d.proximo_contato END) AS proximo_contato,
               {_gcd("d.numero_os")} AS numeros_os
        FROM empresas e
        JOIN demandas d ON d.empresa_id = e.id
        LEFT JOIN medicoes m ON m.demanda_id = d.id
        LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
        WHERE 1=1
    """
    params = []
    f = filtros or {}
    if f.get('status') == 'pendente':
        sql += " AND d.status != 'concluida'"
    elif f.get('status') == 'concluida':
        sql += " AND d.status = 'concluida'"
    if f.get('empresa'):
        sql += ' AND LOWER(e.nome) LIKE LOWER(?)'; params.append(f'%{f["empresa"]}%')
    ordem = f.get('ordem', 'nome')
    order_clause = {
        'nome': 'empresa_nome ASC',
        'data': 'demanda_mais_antiga ASC',
        'pend': 'demandas_pendentes DESC, demanda_mais_antiga ASC',
    }.get(ordem, 'empresa_nome ASC')
    sql += f' GROUP BY e.id, e.nome ORDER BY {order_clause} LIMIT 500'
    with get_db() as conn:
        rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            if r.get('demanda_mais_antiga'):
                try:
                    dt = datetime.fromisoformat(r['demanda_mais_antiga'].replace(' ', 'T').split('.')[0])
                    r['dias_aberta'] = (datetime.now() - dt).days
                except Exception:
                    r['dias_aberta'] = 0
            else:
                r['dias_aberta'] = 0
        return rows


def mesclar_empresas_duplicatas():
    mescladas = 0
    with get_db() as conn:
        grupos = conn.execute("""
            SELECT LOWER(TRIM(nome)) AS nome_key, MIN(id) AS id_principal,
                   COUNT(*) AS qtd
            FROM empresas
            GROUP BY LOWER(TRIM(nome))
            HAVING COUNT(*) > 1
        """).fetchall()
        for g in grupos:
            id_princ = g['id_principal']
            dups = [r['id'] for r in conn.execute(
                "SELECT id FROM empresas WHERE LOWER(TRIM(nome)) = ? AND id != ?",
                (g['nome_key'], id_princ)).fetchall()]
            for dup_id in dups:
                # Reaponta TODAS as tabelas com empresa_id para a principal antes
                # de deletar a duplicata; senão coletas/planejamentos/visitas/
                # contatos ficam órfãos apontando para uma empresa inexistente.
                for _tbl in ('demandas', 'amostradores', 'coletas_ruido',
                             'coletas_quimico', 'coletas_outros', 'planejamentos',
                             'visitas_tecnicas', 'contatos_empresa'):
                    conn.execute(f"UPDATE {_tbl} SET empresa_id=? WHERE empresa_id=?",
                                 (id_princ, dup_id))
                conn.execute("DELETE FROM empresas WHERE id=?", (dup_id,))
                mescladas += 1
    return mescladas


def get_empresa_demandas(empresa_id):
    with get_db() as conn:
        emp = conn.execute('SELECT * FROM empresas WHERE id=?', (empresa_id,)).fetchone()
        if not emp:
            return None
        emp = row_to_dict(emp)
        nome_key = (emp.get('nome') or '').strip().lower()
        ids_iguais = [r['id'] for r in conn.execute(
            "SELECT id FROM empresas WHERE LOWER(TRIM(nome))=?", (nome_key,)).fetchall()]
        if not ids_iguais:
            ids_iguais = [empresa_id]
        ph = ','.join(['?'] * len(ids_iguais))
        dems = [row_to_dict(r) for r in conn.execute(f"""
            SELECT d.*,
                   e.nome AS empresa_nome,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id) AS total_medicoes,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id AND m.status='realizado') AS realizadas,
                   {_ds("d.criado_em")} AS dias_aberta,
                   COALESCE(d.responsavel, u.display_name) AS responsavel_efetivo,
                   COALESCE(d.nome_tarefa, d.titulo) AS tarefa_display
            FROM demandas d
            JOIN empresas e ON e.id = d.empresa_id
            LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
            WHERE d.empresa_id IN ({ph})
            ORDER BY d.status ASC, d.criado_em DESC
        """, ids_iguais).fetchall()]
        for d in dems:
            # 'aguardando_lab' NÃO entra nas pendentes (não volta pro
            # replanejamento) — vai em lista própria p/ badge "aguardando lab"
            d['medicoes_pendentes'] = [row_to_dict(r) for r in conn.execute(
                "SELECT id, agente, tipo_amostrador, qtd_pontos_feita, qtd_pontos_prevista "
                "FROM medicoes WHERE demanda_id=? AND status NOT IN ('realizado','aguardando_lab') "
                "ORDER BY agente",
                (d['id'],)).fetchall()]
            d['medicoes_aguardando_lab'] = [row_to_dict(r) for r in conn.execute(
                "SELECT id, agente, tipo_amostrador "
                "FROM medicoes WHERE demanda_id=? AND status='aguardando_lab' ORDER BY agente",
                (d['id'],)).fetchall()]
            # Fallback: quando não há medições formais, usa agentes do motor inteligente
            if not d.get('medicoes_pendentes') and d.get('status') != 'concluida':
                _ej = d.get('extracao_json') or ''
                _raw = []
                # 1. Tentar extracao_json (já calculado no sync)
                if _ej:
                    try:
                        import json as _jj
                        _raw = _jj.loads(_ej).get('agentes', [])
                    except Exception:
                        pass
                # 2. Se vazio, rodar motor diretamente (título + desc + checklist + bucket)
                if not _raw:
                    try:
                        from .inteligencia_demandas import extrair_agentes_multifonte
                        import json as _jj
                        _cl = []
                        try:
                            _cl = _jj.loads(d.get('checklist') or '[]')
                        except Exception:
                            pass
                        _ags = extrair_agentes_multifonte(
                            titulo=d.get('titulo') or '',
                            descricao=d.get('descricao') or '',
                            checklist=_cl,
                            bucket=d.get('planner_bucket') or '',
                        )
                        _raw = [{'canonical': a.canonical, 'quantidade': a.quantidade,
                                 'confianca': a.confianca, 'tipo': a.tipo} for a in _ags]
                    except Exception:
                        pass
                if _raw:
                    # Tipos que são documentos, não medições de campo — excluir dos chips
                    _DOC_TIPOS = {'documento'}
                    _DOC_CANONICALS = {
                        'PGR', 'LTCAT', 'PCMSO', 'PPRA', 'PPP', 'AET',
                        'Laudo de Insalubridade', 'Laudo de Periculosidade',
                    }
                    def _tip(c):
                        c = (c or '').lower()
                        if 'ruído' in c or 'ruido' in c or 'dosimetria' in c: return 'ruido'
                        if 'calor' in c or 'ibutg' in c: return 'calor'
                        if 'corpo inteiro' in c or ' vci' in c: return 'vibracao_vci'
                        if 'mão' in c or 'braço' in c or ' vmb' in c or 'mao' in c or 'braco' in c: return 'vibracao_vbma'
                        if 'vibr' in c: return 'vibracao'
                        if 'silica' in c or 'sílica' in c or 'poeira' in c or 'particul' in c: return 'particulado'
                        return 'quimico'
                    d['medicoes_pendentes'] = [
                        {'id': None, 'agente': a.get('canonical', ''),
                         'tipo_amostrador': _tip(a.get('canonical', '')),
                         'qtd_pontos_feita': 0,
                         'qtd_pontos_prevista': a.get('quantidade', 1),
                         'fonte': 'planner'}
                        for a in _raw
                        if (a.get('canonical')
                            and float(a.get('confianca', 1)) >= 0.55
                            and a.get('tipo', '') not in _DOC_TIPOS
                            and a.get('canonical') not in _DOC_CANONICALS)
                    ]
                    # Tag "Laudar" — menção a laudo/resultado no texto, mas não é uma medição
                    _txt_busca = ((d.get('descricao') or '') + ' ' + (d.get('titulo') or '')).lower()
                    _laudar_kws = ['laudar', 'laudo de', 'emitir laudo', 'elaborar laudo',
                                   'liberar resultado', 'lançar no soc', 'lancar no soc',
                                   'resultado da medição', 'resultado da medicao']
                    if any(kw in _txt_busca for kw in _laudar_kws):
                        d['laudar_tag'] = True

            # ── Fallback para OS manuais sem número e sem título ─────────────
            # origem='manual', numero_os='', titulo=None  →  gera display legível
            if d.get('origem') == 'manual' and not (d.get('numero_os') or '').strip():
                d['numero_os'] = f"MAN-{d['id']}"
                # Título: lista de agentes das medições (todos, não só pendentes)
                _rows_ag = conn.execute(
                    "SELECT DISTINCT agente FROM medicoes WHERE demanda_id=? AND agente IS NOT NULL AND agente != '' ORDER BY agente",
                    (d['id'],)).fetchall()
                # r['agente'] funciona em SQLite (sqlite3.Row) e PG (RealDictRow);
                # r[0] quebra no Postgres (RealDictRow não aceita índice inteiro).
                todos_agentes = [r['agente'] for r in _rows_ag if r['agente']]
                if todos_agentes:
                    d['tarefa_display'] = d['nome_tarefa'] = ', '.join(todos_agentes)
                else:
                    d['tarefa_display'] = d['nome_tarefa'] = 'OS Manual (sem descrição)'

        emp['demandas'] = dems
        return emp


def get_empresa_painel(empresa_id):
    """Painel completo de uma empresa: stats, demandas, agentes, técnicos, coletas, amostradores."""
    with get_db() as conn:
        emp = conn.execute('SELECT * FROM empresas WHERE id=?', (empresa_id,)).fetchone()
        if not emp:
            return None
        emp = row_to_dict(emp)

        # ─ Todas as IDs da empresa (nome duplicado) ──────────────────────
        nome_key = (emp.get('nome') or '').strip().lower()
        ids_iguais = [r['id'] for r in conn.execute(
            "SELECT id FROM empresas WHERE LOWER(TRIM(nome))=?", (nome_key,)).fetchall()]
        if not ids_iguais:
            ids_iguais = [empresa_id]
        ph = ','.join(['?'] * len(ids_iguais))

        # ─ Stats resumo ──────────────────────────────────────────────────
        stats = row_to_dict(conn.execute(f"""
            SELECT
              COUNT(*) AS total_demandas,
              COUNT(CASE WHEN status='concluida' THEN 1 END) AS concluidas,
              COUNT(CASE WHEN status!='concluida' THEN 1 END) AS abertas,
              COUNT(CASE WHEN status!='concluida' AND prazo IS NOT NULL AND prazo!=''
                         AND {_lab_expire_cond("prazo","0")} THEN 1 END) AS atrasadas,
              ROUND(AVG(CASE WHEN status='concluida' AND criado_em IS NOT NULL
                             THEN {_ds("criado_em")} END), 1) AS tempo_medio_conclusao_dias
            FROM demandas WHERE empresa_id IN ({ph})
              AND UPPER(COALESCE(titulo,'')) NOT LIKE '%PROCESSO ANTIGO%'
        """, ids_iguais).fetchone())
        emp['stats'] = stats

        # ─ Demandas recentes (abertas primeiro, depois concluídas) ──────
        import json as _json
        _DOC_CANONICALS = {'PGR','LTCAT','PCMSO','PPRA','PPP','AET',
                           'Laudo de Insalubridade','Laudo de Periculosidade'}
        _LAUDAR_KWS = ['laudar','laudo de','emitir laudo','elaborar laudo',
                       'liberar resultado','lançar no soc','lancar no soc']
        raw_dems = [row_to_dict(r) for r in conn.execute(f"""
            SELECT d.id, d.numero_os,
                   COALESCE(d.titulo, d.nome_tarefa) AS titulo,
                   d.status, d.prazo, d.bucket, d.planner_bucket,
                   COALESCE(d.responsavel, u.display_name) AS responsavel,
                   {_ds("d.criado_em")} AS dias_aberta,
                   d.extracao_json, d.descricao,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id) AS total_medicoes
            FROM demandas d
            LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
            WHERE d.empresa_id IN ({ph})
              AND UPPER(COALESCE(d.titulo,'')) NOT LIKE '%PROCESSO ANTIGO%'
            ORDER BY
              CASE WHEN d.status != 'concluida' THEN 0 ELSE 1 END,
              d.criado_em DESC
            LIMIT 20
        """, ids_iguais).fetchall()]
        for _d in raw_dems:
            # Parse extracao_json → chips de agentes
            try:
                _ext = _json.loads(_d.get('extracao_json') or '{}')
                _ags = [a for a in (_ext.get('agentes') or [])
                        if a.get('canonical') and a.get('canonical') not in _DOC_CANONICALS
                        and a.get('tipo','') != 'documento'
                        and float(a.get('confianca', 1)) >= 0.55]
                _d['medicoes_pendentes'] = _ags if _d.get('status') != 'concluida' else []
            except Exception:
                _d['medicoes_pendentes'] = []
            # Tag laudar
            _txt = ((_d.get('descricao') or '') + ' ' + (_d.get('titulo') or '')).lower()
            _d['laudar_tag'] = any(kw in _txt for kw in _LAUDAR_KWS)
            # Limpar campos pesados
            _d.pop('extracao_json', None)
            _d.pop('descricao', None)
        emp['demandas_recentes'] = raw_dems

        # ─ Agentes medidos (por demanda/medicao) ─────────────────────────
        emp['agentes'] = [row_to_dict(r) for r in conn.execute(f"""
            SELECT m.agente,
                   COUNT(*) AS qtd,
                   COUNT(CASE WHEN m.status='realizado' THEN 1 END) AS realizados
            FROM medicoes m
            JOIN demandas d ON d.id = m.demanda_id
            WHERE d.empresa_id IN ({ph})
            GROUP BY m.agente ORDER BY qtd DESC LIMIT 20
        """, ids_iguais).fetchall()]

        # Fallback: a tabela `medicoes` só é populada por import XLSX manual.
        # Demandas vindas do Planner guardam os agentes em extracao_json.
        # Se não houver medições, agregamos os agentes a partir do JSON.
        if not emp['agentes']:
            _ag_count = {}
            for _r in conn.execute(f"""
                SELECT d.status, d.extracao_json
                FROM demandas d
                WHERE d.empresa_id IN ({ph})
                  AND d.extracao_json IS NOT NULL AND d.extracao_json != ''
                  AND UPPER(COALESCE(d.titulo,'')) NOT LIKE '%PROCESSO ANTIGO%'
            """, ids_iguais).fetchall():
                _r = row_to_dict(_r)
                try:
                    _ext = _json.loads(_r.get('extracao_json') or '{}')
                except Exception:
                    continue
                _concl = _r.get('status') == 'concluida'
                for _a in (_ext.get('agentes') or []):
                    _can = _a.get('canonical')
                    if (not _can or _can in _DOC_CANONICALS
                            or _a.get('tipo', '') == 'documento'
                            or float(_a.get('confianca', 1)) < 0.55):
                        continue
                    _e = _ag_count.setdefault(_can, {'qtd': 0, 'realizados': 0})
                    _e['qtd'] += 1
                    if _concl:
                        _e['realizados'] += 1
            emp['agentes'] = sorted(
                [{'agente': k, 'qtd': v['qtd'], 'realizados': v['realizados']}
                 for k, v in _ag_count.items()],
                key=lambda x: -x['qtd'])[:20]

        # ─ Técnicos que atenderam ─────────────────────────────────────────
        tecnicos = {}
        for r in conn.execute(f"""
            SELECT cr.tecnico, COUNT(*) AS visitas
            FROM coletas_ruido cr WHERE cr.empresa_id IN ({ph}) AND cr.tecnico IS NOT NULL
            GROUP BY cr.tecnico
        """, ids_iguais).fetchall():
            t = r['tecnico'] if isinstance(r, dict) else r[0]
            v = r['visitas'] if isinstance(r, dict) else r[1]
            tecnicos[t] = tecnicos.get(t, 0) + v
        for r in conn.execute(f"""
            SELECT cq.responsavel_coleta, COUNT(*) AS visitas
            FROM coletas_quimico cq WHERE cq.empresa_id IN ({ph}) AND cq.responsavel_coleta IS NOT NULL
            GROUP BY cq.responsavel_coleta
        """, ids_iguais).fetchall():
            t = r['responsavel_coleta'] if isinstance(r, dict) else r[0]
            v = r['visitas'] if isinstance(r, dict) else r[1]
            if t:
                tecnicos[t] = tecnicos.get(t, 0) + v
        for r in conn.execute(f"""
            SELECT vt.tecnico, COUNT(*) AS visitas
            FROM visitas_tecnicas vt WHERE vt.empresa_id IN ({ph}) AND vt.tecnico IS NOT NULL
            GROUP BY vt.tecnico
        """, ids_iguais).fetchall():
            t = r['tecnico'] if isinstance(r, dict) else r[0]
            v = r['visitas'] if isinstance(r, dict) else r[1]
            if t:
                tecnicos[t] = tecnicos.get(t, 0) + v
        emp['tecnicos'] = sorted(
            [{'tecnico': k, 'visitas': v} for k, v in tecnicos.items()],
            key=lambda x: -x['visitas'])[:10]

        # ─ Coletas realizadas ────────────────────────────────────────────
        emp['coletas_ruido'] = row_to_dict(conn.execute(f"""
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN status='concluida' THEN 1 END) AS concluidas
            FROM coletas_ruido WHERE empresa_id IN ({ph})
        """, ids_iguais).fetchone())
        emp['coletas_quimico'] = row_to_dict(conn.execute(f"""
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN status='concluida' THEN 1 END) AS concluidas
            FROM coletas_quimico WHERE empresa_id IN ({ph})
        """, ids_iguais).fetchone())

        # ─ Amostradores utilizados ────────────────────────────────────────
        emp['amostradores_usados'] = [row_to_dict(r) for r in conn.execute(f"""
            SELECT a.codigo, a.tipo, a.status, a.data_medicao, a.data_envio_lab
            FROM amostradores a WHERE a.empresa_id IN ({ph})
            ORDER BY a.data_medicao DESC LIMIT 20
        """, ids_iguais).fetchall()]

        # ─ Visitas (retrabalho) ───────────────────────────────────────────
        emp['visitas_stats'] = row_to_dict(conn.execute(f"""
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN retrabalho=1 THEN 1 END) AS retrabalho,
                   COUNT(CASE WHEN resultado IN ('concluido','concluida') THEN 1 END) AS concluidas
            FROM visitas_tecnicas WHERE empresa_id IN ({ph})
        """, ids_iguais).fetchone())

        # ─ Histórico de visitas recentes ─────────────────────────────────
        emp['visitas'] = [row_to_dict(r) for r in conn.execute(f"""
            SELECT vt.id, vt.tecnico, vt.data_visita, vt.tipo_visita,
                   vt.resultado, vt.retrabalho, vt.hora_inicio, vt.hora_termino,
                   vt.observacao_geral,
                   d.numero_os, COALESCE(d.titulo, d.nome_tarefa) AS titulo_demanda
            FROM visitas_tecnicas vt
            LEFT JOIN demandas d ON d.id = vt.demanda_id
            WHERE vt.empresa_id IN ({ph})
            ORDER BY vt.data_visita DESC, vt.criado_em DESC LIMIT 20
        """, ids_iguais).fetchall()]

        return emp


def get_demanda_completa(demanda_id):
    with get_db() as conn:
        d = conn.execute("""
            SELECT d.*, e.nome AS empresa_nome, e.cnpj AS empresa_cnpj
            FROM demandas d JOIN empresas e ON e.id = d.empresa_id
            WHERE d.id = ?
        """, (demanda_id,)).fetchone()
        if not d:
            return None
        d = row_to_dict(d)
        meds = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM medicoes WHERE demanda_id = ? ORDER BY id', (demanda_id,)).fetchall()]
        for m in meds:
            m['baixas'] = [row_to_dict(r) for r in conn.execute("""
                SELECT b.avaliador, b.bomba, b.vazao_calibrada,
                       b.volume_recomendado, b.tempo_calculado_min,
                       b.tempo_calculado_max, b.data_medicao
                FROM baixas b WHERE b.medicao_id = ?
                ORDER BY b.id DESC""", (m['id'],)).fetchall()]
        d['medicoes'] = meds
        return d


# ── Planner Raw Tasks ──────────────────────────────────────────────────

def upsert_raw_task(conn, task_id: str, data: dict) -> tuple:
    existing = conn.execute(
        'SELECT id FROM planner_raw_tasks WHERE planner_task_id=?', (task_id,)
    ).fetchone()

    if existing:
        conn.execute('''
            UPDATE planner_raw_tasks SET
                planner_plan_id=?, planner_plan_nome=?,
                planner_bucket_id=?, planner_bucket=?,
                planner_group_id=?, planner_group_nome=?,
                titulo=?, descricao=?, checklist_json=?, raw_json=?,
                percent_complete=?, prazo=?, criado_em_ms=?, concluido_em_ms=?,
                ms_assignee_id=?, ms_assignees_json=?, etiquetas_json=?,
                sync_status='raw', ignored_reason=NULL,
                synced_at=CURRENT_TIMESTAMP, processed_at=NULL
            WHERE planner_task_id=?
        ''', (
            data.get('planner_plan_id'), data.get('planner_plan_nome'),
            data.get('planner_bucket_id'), data.get('planner_bucket'),
            data.get('planner_group_id'), data.get('planner_group_nome'),
            data.get('titulo'), data.get('descricao'),
            data.get('checklist_json'), data.get('raw_json'),
            data.get('percent_complete', 0), data.get('prazo'),
            data.get('criado_em_ms'), data.get('concluido_em_ms'),
            data.get('ms_assignee_id'), data.get('ms_assignees_json'),
            data.get('etiquetas_json'), task_id,
        ))
        return existing['id'], 'updated'

    cur = conn.execute('''
        INSERT INTO planner_raw_tasks (
            planner_task_id, planner_plan_id, planner_plan_nome,
            planner_bucket_id, planner_bucket, planner_group_id, planner_group_nome,
            titulo, descricao, checklist_json, raw_json,
            percent_complete, prazo, criado_em_ms, concluido_em_ms,
            ms_assignee_id, ms_assignees_json, etiquetas_json,
            sync_status, synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'raw',CURRENT_TIMESTAMP)
    ''', (
        task_id,
        data.get('planner_plan_id'), data.get('planner_plan_nome'),
        data.get('planner_bucket_id'), data.get('planner_bucket'),
        data.get('planner_group_id'), data.get('planner_group_nome'),
        data.get('titulo'), data.get('descricao'),
        data.get('checklist_json'), data.get('raw_json'),
        data.get('percent_complete', 0), data.get('prazo'),
        data.get('criado_em_ms'), data.get('concluido_em_ms'),
        data.get('ms_assignee_id'), data.get('ms_assignees_json'),
        data.get('etiquetas_json'),
    ))
    return cur.lastrowid, 'created'


def mark_raw_task(conn, raw_id: int, status: str, ignored_reason: str = None):
    if status == 'ignored':
        conn.execute(
            "UPDATE planner_raw_tasks SET sync_status='ignored', ignored_reason=? WHERE id=?",
            (ignored_reason, raw_id))
    elif status == 'processed':
        conn.execute(
            "UPDATE planner_raw_tasks SET sync_status='processed', "
            "ignored_reason=NULL, processed_at=CURRENT_TIMESTAMP WHERE id=?",
            (raw_id,))


# ── Estatísticas ───────────────────────────────────────────────────────

def list_amostradores_vencendo(dias_alerta=7):
    """Lista TODOS os amostradores no laboratório.
    Os que têm data de envio válida recebem dias_para_vencer; os que estão
    no lab SEM data de envio aparecem com sem_data_envio=1 (precisam que o
    técnico registre a data). Antes esses sumiam da tela (bug do '0')."""
    val = 'COALESCE(a.dias_validade,45)'
    if USE_PG:
        valid = "a.data_envio_lab ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
        dias_left = f"CASE WHEN {valid} THEN ((a.data_envio_lab)::date + {val} - CURRENT_DATE) ELSE NULL END"
        dias_in   = f"CASE WHEN {valid} THEN (CURRENT_DATE - (a.data_envio_lab)::date) ELSE NULL END"
    else:
        valid = "a.data_envio_lab GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"
        dias_left = f"CASE WHEN {valid} THEN CAST(julianday(a.data_envio_lab) + {val} - julianday('now') AS INTEGER) ELSE NULL END"
        dias_in   = f"CASE WHEN {valid} THEN CAST(julianday('now') - julianday(a.data_envio_lab) AS INTEGER) ELSE NULL END"
    sem_data = f"CASE WHEN {valid} THEN 0 ELSE 1 END"
    sql = f"""
        SELECT a.*, e.nome AS empresa_nome,
               {dias_left} AS dias_para_vencer,
               {dias_in} AS dias_no_lab,
               {sem_data} AS sem_data_envio
        FROM amostradores a
        LEFT JOIN empresas e ON e.id = a.empresa_id
        WHERE a.status = 'laboratorio' AND COALESCE(a.arquivado,0)=0
        ORDER BY dias_para_vencer ASC NULLS LAST
        LIMIT 500
    """
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql).fetchall()]


def contar_vencendo():
    """Conta amostradores no lab por faixa de vencimento.
    total_no_lab = TODOS no lab (com ou sem data). sem_data = no lab sem
    data de envio registrada."""
    val = 'COALESCE(dias_validade,45)'
    if USE_PG:
        valid = "data_envio_lab ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
        venc = f"CASE WHEN {valid} THEN (CASE WHEN CURRENT_DATE > (data_envio_lab)::date + {val} THEN 1 ELSE 0 END) ELSE 0 END"
        urg  = f"CASE WHEN {valid} THEN (CASE WHEN CURRENT_DATE BETWEEN (data_envio_lab)::date + {val} - 3 AND (data_envio_lab)::date + {val} THEN 1 ELSE 0 END) ELSE 0 END"
        aler = f"CASE WHEN {valid} THEN (CASE WHEN CURRENT_DATE BETWEEN (data_envio_lab)::date + {val} - 7 AND (data_envio_lab)::date + {val} - 4 THEN 1 ELSE 0 END) ELSE 0 END"
    else:
        valid = "data_envio_lab GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"
        venc = f"CASE WHEN {valid} THEN (CASE WHEN julianday('now') > julianday(data_envio_lab) + {val} THEN 1 ELSE 0 END) ELSE 0 END"
        urg  = f"CASE WHEN {valid} THEN (CASE WHEN julianday('now') BETWEEN julianday(data_envio_lab) + {val} - 3 AND julianday(data_envio_lab) + {val} THEN 1 ELSE 0 END) ELSE 0 END"
        aler = f"CASE WHEN {valid} THEN (CASE WHEN julianday('now') BETWEEN julianday(data_envio_lab) + {val} - 7 AND julianday(data_envio_lab) + {val} - 4 THEN 1 ELSE 0 END) ELSE 0 END"
    sem = f"CASE WHEN {valid} THEN 0 ELSE 1 END"
    sql = f"""
        SELECT
          SUM({venc}) AS vencidos,
          SUM({urg})  AS urgente,
          SUM({aler}) AS alerta,
          SUM({sem})  AS sem_data,
          COUNT(*) AS total_no_lab
        FROM amostradores
        WHERE status = 'laboratorio' AND COALESCE(arquivado,0)=0
    """
    with get_db() as conn:
        r = conn.execute(sql).fetchone()
        return {
            'vencidos':     int(r['vencidos'] or 0),
            'urgente':      int(r['urgente']  or 0),
            'alerta':       int(r['alerta']   or 0),
            'sem_data':     int(r['sem_data'] or 0),
            'total_no_lab': int(r['total_no_lab'] or 0),
        }


def stats_dashboard():
    lab = 'data_envio_lab'
    val = 'COALESCE(dias_validade,45)'
    if USE_PG:
        venc_cond = f"CURRENT_DATE > ({lab})::date + {val}"
        urg_cond  = f"CURRENT_DATE BETWEEN ({lab})::date + {val} - 3 AND ({lab})::date + {val}"
        aler_cond = f"CURRENT_DATE BETWEEN ({lab})::date + {val} - 7 AND ({lab})::date + {val} - 4"
    else:
        venc_cond = f"julianday('now') > julianday({lab}) + {val}"
        urg_cond  = f"julianday('now') BETWEEN julianday({lab}) + {val} - 3 AND julianday({lab}) + {val}"
        aler_cond = f"julianday('now') BETWEEN julianday({lab}) + {val} - 7 AND julianday({lab}) + {val} - 4"

    base_filter = f"data_envio_lab IS NOT NULL AND data_envio_lab != '' AND status='laboratorio' AND COALESCE(arquivado,0)=0"
    sql = f"""
        SELECT
          (SELECT COUNT(*) FROM amostradores WHERE COALESCE(arquivado,0)=0) AS total_amostradores,
          (SELECT COUNT(*) FROM amostradores WHERE status='disponivel' AND COALESCE(arquivado,0)=0) AS estoque,
          (SELECT COUNT(*) FROM amostradores WHERE status='laboratorio' AND COALESCE(arquivado,0)=0) AS laboratorio,
          (SELECT COUNT(*) FROM amostradores WHERE status='reservado' AND COALESCE(arquivado,0)=0) AS reservados,
          (SELECT COUNT(*) FROM amostradores WHERE status='concluido' AND COALESCE(arquivado,0)=0) AS concluidos,
          (SELECT COUNT(*) FROM amostradores WHERE status='devolvido' AND COALESCE(arquivado,0)=0) AS devolvidos,
          (SELECT COUNT(*) FROM amostradores WHERE status='manutencao' AND COALESCE(arquivado,0)=0) AS manutencao,
          (SELECT COUNT(*) FROM amostradores WHERE status='descartado' AND COALESCE(arquivado,0)=0) AS descartados,
          (SELECT COUNT(*) FROM medicoes WHERE status='realizado') AS medicoes_realizadas,
          (SELECT COUNT(*) FROM medicoes WHERE status='pendente') AS medicoes_pendentes,
          (SELECT COUNT(*) FROM demandas WHERE status!='concluida') AS demandas_pendentes,
          (SELECT COUNT(*) FROM demandas WHERE status='concluida') AS demandas_concluidas,
          (SELECT COUNT(DISTINCT empresa_id) FROM demandas WHERE status!='concluida') AS empresas_ativas,
          (SELECT COUNT(*) FROM amostradores WHERE {base_filter}) AS venc_total_no_lab,
          (SELECT COUNT(*) FROM amostradores WHERE {base_filter} AND {venc_cond}) AS venc_vencidos,
          (SELECT COUNT(*) FROM amostradores WHERE {base_filter} AND {urg_cond}) AS venc_urgente,
          (SELECT COUNT(*) FROM amostradores WHERE {base_filter} AND {aler_cond}) AS venc_alerta
    """
    with get_db() as conn:
        return row_to_dict(conn.execute(sql).fetchone())


def equipamentos_calibracao(dias_alerta=90):
    """Calcula o status de calibração de cada equipamento.
    Validade = data_calibracao + 2 anos. Se cert_validade estiver preenchido
    manualmente, ele tem prioridade (override).
    Retorna {itens:[...], vencidos:n, vencendo:n, dias_alerta}.
    status de cada item: 'vencido' | 'vencendo' (<= dias_alerta) | 'ok' | 'sem_data'.
    """
    from datetime import date as _date, timedelta as _td
    hoje = _date.today()

    def _parse(s):
        if not s:
            return None
        s = str(s)[:10]
        for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                from datetime import datetime as _dt
                return _dt.strptime(s, fmt).date()
            except Exception:
                pass
        return None

    itens, vencidos, vencendo = [], 0, 0
    with get_db() as conn:
        rows = conn.execute(
            'SELECT id, tipo, marca, modelo, numero_serie, observacao, '
            'cert_numero, cert_validade, data_calibracao '
            'FROM equipamentos_inventario ORDER BY tipo, marca, observacao'
        ).fetchall()
    for r in rows:
        d = row_to_dict(r) if 'row_to_dict' in globals() else (dict(r) if hasattr(r, 'keys') else {})
        dcal = _parse(d.get('data_calibracao'))
        venc_manual = _parse(d.get('cert_validade'))
        venc = venc_manual or (dcal + _td(days=730) if dcal else None)
        dias = (venc - hoje).days if venc else None
        if venc is None:
            status = 'sem_data'
        elif dias < 0:
            status = 'vencido'; vencidos += 1
        elif dias <= dias_alerta:
            status = 'vencendo'; vencendo += 1
        else:
            status = 'ok'
        nome = d.get('observacao') or d.get('modelo') or d.get('marca') or 'Equipamento'
        if d.get('numero_serie'):
            nome = f"{nome} (S/N {d['numero_serie']})"
        itens.append({
            'id': d.get('id'), 'tipo': d.get('tipo'), 'nome': nome,
            'data_calibracao': d.get('data_calibracao') or '',
            'vencimento': venc.isoformat() if venc else '',
            'dias_restantes': dias, 'status': status,
        })
    return {'itens': itens, 'vencidos': vencidos, 'vencendo': vencendo,
            'dias_alerta': dias_alerta}


def produtividade_por_tecnico(de=None, ate=None):
    """Produtividade contada POR MEDIÇÃO (coleta), atribuída ao técnico que a
    finalizou (coletas_*.tecnico_login). Cada coleta de ruído/químico/outros =
    1 medição feita. Legado sem tecnico_login cai no campo antigo
    (tecnico / responsavel_coleta / avaliador).
    Retorna lista de dicts: {tecnico, total, mes, ruido, quimico, outros, empresas}.
    'mes' = medições com data_coleta no mês corrente."""
    from datetime import date as _date
    mes_atual = _date.today().isoformat()[:7]  # 'YYYY-MM'
    # Filtro opcional por período (data_coleta entre de e ate, YYYY-MM-DD)
    cond = ''
    fparams = []
    de10 = str(de or '')[:10]; ate10 = str(ate or '')[:10]
    if de10:
        cond += " WHERE substr(COALESCE(data_coleta,''),1,10) >= ?"; fparams.append(de10)
    if ate10:
        cond += (' AND' if cond else ' WHERE') + " substr(COALESCE(data_coleta,''),1,10) <= ?"; fparams.append(ate10)
    # (tabela, coluna_tecnico_legado, rotulo_tipo) — SELECT * p/ pegar empresa,
    # data, OS e local sem quebrar se a coluna não existir (PG/SQLite).
    fontes = [
        ('coletas_ruido',   'tecnico',            'ruido'),
        ('coletas_quimico', 'responsavel_coleta', 'quimico'),
        ('coletas_outros',  'avaliador',          'outros'),
    ]
    agg = {}
    with get_db() as conn:
        for tbl, legcol, tipo in fontes:
            try:
                rows = conn.execute(f"SELECT * FROM {tbl}" + cond, fparams).fetchall()
            except Exception:
                rows = []
            for r in rows:
                d = row_to_dict(r)
                tec = (d.get('tecnico_login') or d.get(legcol) or '').strip() or 'Sem técnico'
                a = agg.get(tec)
                if not a:
                    a = {'tecnico': tec, 'total': 0, 'mes': 0,
                         'ruido': 0, 'quimico': 0, 'outros': 0,
                         'empresas': set(), 'medicoes': []}
                    agg[tec] = a
                a['total'] += 1
                a[tipo] += 1
                if (d.get('data_coleta') or '')[:7] == mes_atual:
                    a['mes'] += 1
                if d.get('empresa_nome'):
                    a['empresas'].add(d['empresa_nome'])
                # Detalhe: o que foi medido, onde e quando
                tipo_real = (d.get('tipo') or tipo) if tbl == 'coletas_outros' else tipo
                a['medicoes'].append({
                    'tipo':    tipo_real,
                    'empresa': d.get('empresa_nome') or '',
                    'data':    (d.get('data_coleta') or '')[:10],
                    'os':      d.get('os') or d.get('numero_os') or '',
                    'local':   d.get('unidade') or d.get('cidade') or d.get('setor') or '',
                })
    out = []
    for a in agg.values():
        a['empresas'] = len(a['empresas'])
        a['medicoes'].sort(key=lambda m: m.get('data') or '', reverse=True)
        a['medicoes'] = a['medicoes'][:200]
        out.append(a)
    out.sort(key=lambda x: x['total'], reverse=True)
    return out


def list_coletas_feitas(limit=300):
    """Lista unificada de planilhas (coletas) FINALIZADAS das 3 tabelas,
    para a aba 'Planilhas Feitas'. Cada item mostra o técnico que finalizou
    (tecnico_login; fallback no campo legado). Ordenada da mais recente."""
    fontes = [
        ('ruido',   'coletas_ruido',   'tecnico'),
        ('quimico', 'coletas_quimico', 'responsavel_coleta'),
        ('outros',  'coletas_outros',  'avaliador'),
    ]
    out = []
    with get_db() as conn:
        for tipo, tbl, legcol in fontes:
            try:
                rows = conn.execute(
                    f'SELECT * FROM {tbl} ORDER BY criado_em DESC LIMIT ?', (limit,)
                ).fetchall()
            except Exception:
                rows = []
            for r in rows:
                d = row_to_dict(r)
                tipo_real = (d.get('tipo') or tipo) if tbl == 'coletas_outros' else tipo
                out.append({
                    'id':           d.get('id'),
                    'tabela':       tbl,
                    'tipo':         tipo_real,
                    'demanda_id':   d.get('demanda_id'),
                    'empresa_nome': d.get('empresa_nome') or '',
                    'os':           d.get('os') or d.get('numero_os') or '',
                    'data_coleta':  d.get('data_coleta') or '',
                    'tecnico':      (d.get('tecnico_login') or d.get(legcol) or '').strip(),
                    'tem_login':    bool((d.get('tecnico_login') or '').strip()),
                    'status':       d.get('status') or '',
                    'criado_em':    d.get('criado_em') or '',
                })
    out.sort(key=lambda x: (x.get('criado_em') or x.get('data_coleta') or ''), reverse=True)
    return out[:limit]


def visita_chave(demanda_id, empresa_nome, data_coleta):
    """Chave da VISITA — o que junta as coletas de uma mesma ida a campo.

    A OS é o pivô quando existe (demanda_id). Sem OS (medição avulsa), cai em
    empresa + dia. Usada pela aba 'Planilhas Feitas' para reunir as coletas de
    ruído/químico/calor/vibração numa única planilha, como o técnico gerou ao
    finalizar a medição."""
    try:
        did = int(demanda_id)
    except (TypeError, ValueError):
        did = None
    if did:
        return f'd{did}'
    emp = (empresa_nome or '').strip().lower()
    return 'e' + emp + '|' + str(data_coleta or '')[:10]


def list_visitas_feitas(limit=300):
    """Lista as VISITAS finalizadas — uma linha por ida a campo, não por agente.

    Reúne as coletas das 3 tabelas pela chave da visita (ver visita_chave) e
    devolve os tipos medidos, o técnico e os ids de cada coleta, para que a tela
    regenere a planilha de campo COMPLETA da visita."""
    itens = list_coletas_feitas(limit=1000)
    grupos = {}
    for c in itens:
        ch = visita_chave(c.get('demanda_id'), c.get('empresa_nome'), c.get('data_coleta'))
        g = grupos.get(ch)
        if not g:
            g = {'chave': ch, 'demanda_id': c.get('demanda_id'),
                 'empresa_nome': c.get('empresa_nome') or '', 'os': c.get('os') or '',
                 'data_coleta': c.get('data_coleta') or '', 'tecnicos': [],
                 'tipos': [], 'itens': [], 'criado_em': c.get('criado_em') or ''}
            grupos[ch] = g
        # 'vibracao_vci'/'vibracao_vbma' são a MESMA seção do relatório
        tp = c.get('tipo') or ''
        tp_sec = 'vibracao' if tp.startswith('vibracao') else tp
        if tp_sec and tp_sec not in g['tipos']:
            g['tipos'].append(tp_sec)
        if c.get('tecnico') and c['tecnico'] not in g['tecnicos']:
            g['tecnicos'].append(c['tecnico'])
        if not g['os'] and c.get('os'):
            g['os'] = c['os']
        if not g['empresa_nome'] and c.get('empresa_nome'):
            g['empresa_nome'] = c['empresa_nome']
        if not g['data_coleta'] and c.get('data_coleta'):
            g['data_coleta'] = c['data_coleta']
        if (c.get('criado_em') or '') > (g['criado_em'] or ''):
            g['criado_em'] = c['criado_em']
        g['itens'].append({'id': c.get('id'), 'tabela': c.get('tabela'), 'tipo': tp})
    out = list(grupos.values())
    # Mesma ordem das seções do relatório de campo, para a tela bater com o PDF
    ordem = ['ruido', 'quimico', 'vibracao', 'calor']
    for g in out:
        g['tecnico'] = ' · '.join(g.pop('tecnicos')) or ''
        g['qtd_coletas'] = len(g['itens'])
        g['tipos'] = ([t for t in ordem if t in g['tipos']]
                      + [t for t in g['tipos'] if t not in ordem])
    out.sort(key=lambda x: (x.get('criado_em') or x.get('data_coleta') or ''), reverse=True)
    return out[:limit]


def coletas_da_visita(chave):
    """Todas as coletas (linhas completas) de uma visita, por tabela.

    Devolve {'ruido': [...], 'quimico': [...], 'outros': [...]} já com os filhos
    (trabalhadores / amostradores) carregados."""
    fontes = [('ruido', 'coletas_ruido'), ('quimico', 'coletas_quimico'),
              ('outros', 'coletas_outros')]
    res = {'ruido': [], 'quimico': [], 'outros': []}
    with get_db() as conn:
        for chave_res, tbl in fontes:
            try:
                rows = conn.execute(f'SELECT * FROM {tbl} ORDER BY criado_em DESC LIMIT 1000').fetchall()
            except Exception:
                rows = []
            for r in rows:
                d = row_to_dict(r)
                ch = visita_chave(d.get('demanda_id'), d.get('empresa_nome'), d.get('data_coleta'))
                if ch != chave:
                    continue
                if tbl == 'coletas_ruido':
                    d['trabalhadores'] = [row_to_dict(x) for x in conn.execute(
                        'SELECT * FROM coletas_ruido_func WHERE coleta_id=? ORDER BY seq',
                        (d['id'],)).fetchall()]
                elif tbl == 'coletas_quimico':
                    d['amostradores'] = [row_to_dict(x) for x in conn.execute(
                        'SELECT * FROM coletas_quimico_amostr WHERE coleta_id=? ORDER BY seq',
                        (d['id'],)).fetchall()]
                res[chave_res].append(d)
    return res


def _anexos_where(demanda_id, empresa_nome, data_coleta):
    """WHERE de visita p/ visita_anexos — mesma lógica da visita_chave:
    demanda_id quando existe; senão empresa + dia."""
    try:
        did = int(demanda_id)
    except (TypeError, ValueError):
        did = None
    if did:
        return 'demanda_id=?', [did]
    return ("(demanda_id IS NULL OR demanda_id=0) AND "
            "LOWER(COALESCE(empresa_nome,''))=? AND "
            "substr(COALESCE(data_coleta,''),1,10)=?",
            [(empresa_nome or '').strip().lower(), str(data_coleta or '')[:10]])


def save_visita_anexos(demanda_id, empresa_nome, data_coleta,
                       fotos=None, sig_avaliado=None, sig_empresa=None):
    """Persiste fotos e assinaturas da visita (o wizard mandava no payload e o
    save descartava — a planilha remontada saía sem elas).

    Substituição GRANULAR por categoria: fotos=None não mexe nas fotos
    (fotos=[] limpa); assinatura só é gravada se vier com conteúdo — um POST
    parcial nunca apaga o que outro já salvou."""
    try:
        did = int(demanda_id)
    except (TypeError, ValueError):
        did = None
    where, params = _anexos_where(demanda_id, empresa_nome, data_coleta)
    n = 0
    with get_db() as conn:
        if fotos is not None:
            conn.execute(f"DELETE FROM visita_anexos WHERE tipo='foto' AND {where}", params)
            seq = 0
            for f in (fotos or []):
                if isinstance(f, dict):
                    src = f.get('data') or f.get('src') or ''
                    leg = (f.get('legenda') or f.get('caption') or '').strip()
                    crg = (f.get('cargo') or f.get('funcao') or '').strip()
                else:
                    src, leg, crg = (f or ''), '', ''
                if not isinstance(src, str) or not src.strip():
                    continue
                seq += 1
                conn.execute(
                    'INSERT INTO visita_anexos '
                    '(demanda_id, empresa_nome, data_coleta, tipo, seq, legenda, cargo, data_b64) '
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (did, empresa_nome or '', str(data_coleta or '')[:10],
                     'foto', seq, leg, crg, src))
                n += 1
        for tipo, val in (('sig_avaliado', sig_avaliado), ('sig_empresa', sig_empresa)):
            if not (isinstance(val, str) and val.strip()):
                continue
            conn.execute(f"DELETE FROM visita_anexos WHERE tipo=? AND {where}",
                         [tipo] + params)
            conn.execute(
                'INSERT INTO visita_anexos '
                '(demanda_id, empresa_nome, data_coleta, tipo, seq, legenda, cargo, data_b64) '
                "VALUES (?,?,?,?,?,?,?,?)",
                (did, empresa_nome or '', str(data_coleta or '')[:10], tipo, 1, '', '', val))
            n += 1
    return n


def anexos_da_visita(chave):
    """Fotos e assinaturas persistidas de uma visita (chave = visita_chave).
    Devolve {'fotos': [{data, legenda, cargo}], 'sig_avaliado': str|None,
    'sig_empresa': str|None}."""
    res = {'fotos': [], 'sig_avaliado': None, 'sig_empresa': None}
    if not chave:
        return res
    if chave.startswith('d') and chave[1:].isdigit():
        where, params = 'demanda_id=?', [int(chave[1:])]
    elif chave.startswith('e') and '|' in chave:
        emp, dia = chave[1:].rsplit('|', 1)
        where, params = ("(demanda_id IS NULL OR demanda_id=0) AND "
                         "LOWER(COALESCE(empresa_nome,''))=? AND "
                         "substr(COALESCE(data_coleta,''),1,10)=?", [emp, dia])
    else:
        return res
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT tipo, seq, legenda, cargo, data_b64 FROM visita_anexos '
            f'WHERE {where} ORDER BY tipo, seq', params).fetchall()
    for r in rows:
        d = row_to_dict(r)
        if d.get('tipo') == 'foto':
            res['fotos'].append({'data': d.get('data_b64') or '',
                                 'legenda': d.get('legenda') or '',
                                 'cargo': d.get('cargo') or ''})
        elif d.get('tipo') in ('sig_avaliado', 'sig_empresa'):
            res[d['tipo']] = d.get('data_b64') or None
    return res


def resumo_dias(dias):
    """Média, mediana, p90 e máximo de uma lista de durações (em dias).

    Mediana e p90 em Python porque o SQLite não tem função de mediana — e média
    sozinha não serve de régua aqui: a distribuição de entrega do laboratório tem
    cauda longa (um laudo real levou 92 dias contra mediana de 16).
    """
    d = sorted(int(x) for x in dias if x is not None)
    if not d:
        return None
    n = len(d)
    return {'media_dias': round(sum(d) / n, 1), 'mediana_dias': d[n // 2],
            'p90_dias': d[min(n - 1, int(n * 0.9))], 'max_dias': d[-1], 'amostra': n}


def stats_amostradores_fluxo(presos_lab_dias=15, reserv_parado_dias=7):
    """Analytics operacional de amostradores (TASK C) — derivado dos
    timestamps reais, por isso reflete automaticamente cada mudança de status.

    Retorna:
      - por_status:    contagem por status canônico (não arquivados)
      - tempo_coleta_lab:   média de dias entre data_medicao e data_envio_lab (NOSSO
                            tempo: campo até despachar a amostra)
      - tempo_lab_resultado: ENTREGA DO LAB — envio → resultado (RA), com mediana e
                            p90, porque a distribuição tem cauda longa
      - tempo_lab_concluido: média de dias entre data_envio_lab e data_conclusao —
                            inclui a nossa demora em fechar, NÃO é o tempo do lab
      - gargalos: presos_lab (no lab há > N dias), reservados_parados
                  (reservado há > N dias), concluidos_pendentes_arquivo
    """
    if USE_PG:
        def _iso(c):  return f"{c} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'"
        def _diff(a, b): return f"({b}::date - {a}::date)"
        def _since(c): return f"(CURRENT_DATE - ({c})::date)"
    else:
        def _iso(c):  return f"{c} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"
        def _diff(a, b): return f"CAST(julianday({b}) - julianday({a}) AS INTEGER)"
        def _since(c): return f"CAST(julianday('now') - julianday({c}) AS INTEGER)"

    res = {'por_status': {}, 'tempo_coleta_lab': None, 'tempo_lab_resultado': None,
           'tempo_lab_concluido': None, 'gargalos': {}}
    with get_db() as conn:
        # contagem por status (não arquivados)
        for st in STATUS_AMOSTRADOR:
            r = conn.execute(
                "SELECT COUNT(*) c FROM amostradores WHERE status=? AND COALESCE(arquivado,0)=0",
                (st,)).fetchone()
            res['por_status'][st] = (r['c'] if r else 0)

        # tempo médio coleta → laboratório
        cond1 = (f"{_iso('data_medicao')} AND {_iso('data_envio_lab')} "
                 f"AND {_diff('data_medicao', 'data_envio_lab')} >= 0")
        r = conn.execute(
            f"SELECT AVG({_diff('data_medicao','data_envio_lab')}) m, COUNT(*) n "
            f"FROM amostradores WHERE {cond1}").fetchone()
        if r and r['n']:
            res['tempo_coleta_lab'] = {'media_dias': round(float(r['m']), 1), 'amostra': r['n']}

        # ENTREGA DO LABORATÓRIO: envio → resultado (RA). É esta a régua para cobrar o
        # lab. A de baixo (envio → conclusão) inclui a NOSSA demora em fechar o
        # amostrador e superestimava o lab em ~14 dias — e a tela do BI mostrava ela
        # com o rótulo "Lab → Resultado" (06/08/2026).
        cond3 = (f"{_iso('data_envio_lab')} AND {_iso('data_resultado')} "
                 f"AND {_diff('data_envio_lab', 'data_resultado')} >= 0")
        res['tempo_lab_resultado'] = resumo_dias(
            r['d'] for r in conn.execute(
                f"SELECT {_diff('data_envio_lab','data_resultado')} d "
                f"FROM amostradores WHERE {cond3}").fetchall())

        # tempo médio laboratório → concluído
        cond2 = (f"status='concluido' AND {_iso('data_envio_lab')} AND {_iso('data_conclusao')} "
                 f"AND {_diff('data_envio_lab', 'data_conclusao')} >= 0")
        r = conn.execute(
            f"SELECT AVG({_diff('data_envio_lab','data_conclusao')}) m, COUNT(*) n "
            f"FROM amostradores WHERE {cond2}").fetchone()
        if r and r['n']:
            res['tempo_lab_concluido'] = {'media_dias': round(float(r['m']), 1), 'amostra': r['n']}

        # GARGALOS
        # presos no laboratório há mais de N dias
        r = conn.execute(
            f"SELECT COUNT(*) c FROM amostradores WHERE status='laboratorio' "
            f"AND COALESCE(arquivado,0)=0 AND {_iso('data_envio_lab')} "
            f"AND {_since('data_envio_lab')} > ?", (presos_lab_dias,)).fetchone()
        res['gargalos']['presos_lab'] = {'qtd': (r['c'] if r else 0), 'limite_dias': presos_lab_dias}

        # reservados parados (sem baixa) há mais de N dias
        r = conn.execute(
            f"SELECT COUNT(*) c FROM amostradores WHERE status='reservado' "
            f"AND COALESCE(arquivado,0)=0 AND {_iso('atualizado_em')} "
            f"AND {_since('atualizado_em')} > ?", (reserv_parado_dias,)).fetchone()
        res['gargalos']['reservados_parados'] = {'qtd': (r['c'] if r else 0), 'limite_dias': reserv_parado_dias}

        # concluídos aguardando arquivamento (≥30 dias) — alimenta TASK D
        r = conn.execute(
            f"SELECT COUNT(*) c FROM amostradores WHERE status='concluido' "
            f"AND COALESCE(arquivado,0)=0 AND {_iso('data_conclusao')} "
            f"AND {_since('data_conclusao')} >= 30").fetchone()
        res['gargalos']['concluidos_para_arquivar'] = {'qtd': (r['c'] if r else 0)}

    return res


# ── Operational Demands ────────────────────────────────────────────────

def list_operational_demands(filtros=None):
    f = filtros or {}
    try:
        limit = min(int(f.get('limit', 200)), 1000)
    except (ValueError, TypeError):
        limit = 200

    sql = f"""
        SELECT d.*,
               e.nome AS empresa_nome,
               e.cnpj AS empresa_cnpj,
               e.pendente AS empresa_pendente,
               COALESCE(u.display_name, d.responsavel) AS responsavel_nome,
               COALESCE(mm.total_medicoes, 0) AS total_medicoes,
               COALESCE(mm.realizadas, 0) AS realizadas,
               {_ds("COALESCE(d.criado_em_ms, d.criado_em)")} AS dias_aberta,
               {_du("d.prazo")} AS dias_para_prazo,
               CASE
                 WHEN LOWER(COALESCE(d.planner_bucket,'')) LIKE '%entregue%'
                   OR LOWER(COALESCE(d.planner_bucket,'')) LIKE '%conclu%'
                 THEN 'concluida'
                 WHEN d.status = 'em_andamento' THEN 'em_andamento'
                 ELSE 'aberta'
               END AS operational_status
        FROM demandas d
        JOIN empresas e ON e.id = d.empresa_id
        LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
        LEFT JOIN (
            SELECT demanda_id,
                   COUNT(*) AS total_medicoes,
                   SUM(CASE WHEN status='realizado' THEN 1 ELSE 0 END) AS realizadas
            FROM medicoes GROUP BY demanda_id
        ) mm ON mm.demanda_id = d.id
        WHERE d.tipo_demanda NOT IN ('interna', 'administrativa')
          AND d.empresa_id > 0
          AND d.origem = 'planner'
          AND UPPER(d.titulo) NOT LIKE '%PROCESSO ANTIGO%'
    """
    params = []
    if f.get('status'):
        sql += ' AND d.status=?'; params.append(f['status'])
    if f.get('empresa'):
        sql += ' AND LOWER(e.nome) LIKE LOWER(?)'; params.append(f'%{f["empresa"]}%')
    if f.get('os'):
        sql += ' AND d.numero_os LIKE ?'; params.append(f'%{f["os"]}%')
    if f.get('tipo'):
        sql += ' AND d.tipo_demanda=?'; params.append(f['tipo'])
    sql += f' ORDER BY d.criado_em DESC LIMIT {limit}'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def list_contatos_empresa(empresa_id):
    """Retorna histórico de contatos de uma empresa, do mais recente ao mais antigo."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM contatos_empresa WHERE empresa_id=? ORDER BY feito_em DESC LIMIT 50',
            (empresa_id,)
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def list_operational_por_empresa(filtros=None):
    sql = f"""
        SELECT e.id AS empresa_id,
               e.nome AS empresa_nome,
               e.cnpj,
               e.email,
               e.telefone,
               e.contato,
               e.pendente AS empresa_pendente,
               COUNT(DISTINCT d.id) AS total_demandas,
               COUNT(DISTINCT CASE WHEN d.status='concluida' THEN d.id END) AS demandas_concluidas,
               COUNT(DISTINCT CASE WHEN d.status!='concluida' THEN d.id END) AS demandas_pendentes,
               ROUND(COUNT(DISTINCT CASE WHEN d.status='concluida' THEN d.id END) * 100.0
                     / NULLIF(COUNT(DISTINCT d.id), 0), 1) AS progresso_medio,
               {_gcd("NULLIF(d.numero_os,'')")} AS numeros_os,
               MIN(CASE
                     WHEN d.status != 'concluida'
                       AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%entregue%'
                       AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%conclu%'
                     THEN NULLIF(d.prazo,'')
                   END) AS prazo_mais_proximo,
               COALESCE(MAX(u.display_name), MAX(d.responsavel)) AS responsavel,
               COUNT(DISTINCT d.tipo_demanda) AS tipos_count,
               {_gcd("d.tipo_demanda")} AS tipos,
               SUM(CASE WHEN d.status!='concluida'
                          AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%entregue%'
                          AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%conclu%'
                          AND d.prazo IS NOT NULL AND d.prazo != ''
                          AND {_lab_expire_cond("d.prazo", "0")}
                   THEN 1 ELSE 0 END) AS demandas_atrasadas,
               (SELECT resultado FROM contatos_empresa WHERE empresa_id=e.id ORDER BY feito_em DESC LIMIT 1) AS ultimo_contato_resultado,
               (SELECT obs FROM contatos_empresa WHERE empresa_id=e.id ORDER BY feito_em DESC LIMIT 1) AS ultimo_contato_obs,
               (SELECT proximo_contato FROM contatos_empresa WHERE empresa_id=e.id ORDER BY feito_em DESC LIMIT 1) AS proximo_contato,
               (SELECT feito_em FROM contatos_empresa WHERE empresa_id=e.id ORDER BY feito_em DESC LIMIT 1) AS ultimo_contato_em,
               (SELECT feito_por FROM contatos_empresa WHERE empresa_id=e.id ORDER BY feito_em DESC LIMIT 1) AS ultimo_contato_por
        FROM empresas e
        JOIN demandas d ON d.empresa_id = e.id
        LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
        WHERE d.tipo_demanda NOT IN ('interna', 'administrativa')
          AND d.empresa_id > 0
          AND d.origem = 'planner'
          AND UPPER(d.titulo) NOT LIKE '%PROCESSO ANTIGO%'
    """
    params = []
    f = filtros or {}
    if f.get('status') == 'pendente':
        sql += " AND d.status != 'concluida'"
    elif f.get('status') == 'concluida':
        sql += " AND d.status = 'concluida'"
    elif f.get('status') == 'em_andamento':
        sql += " AND d.status = 'em_andamento'"
    elif f.get('status') == 'aberta':
        sql += " AND d.status = 'aberta'"
    if f.get('empresa'):
        sql += ' AND LOWER(e.nome) LIKE LOWER(?)'; params.append(f'%{f["empresa"]}%')
    if f.get('bucket'):
        sql += ' AND LOWER(d.planner_bucket) LIKE LOWER(?)'; params.append(f'%{f["bucket"]}%')
    if f.get('tipo'):
        sql += ' AND d.tipo_demanda = ?'; params.append(f['tipo'])
    if f.get('os'):
        sql += ' AND d.numero_os LIKE ?'; params.append(f'%{f["os"]}%')
    ordem = f.get('ordem', 'empresa')
    if ordem == 'prazo':
        sql += ' GROUP BY e.id, e.nome ORDER BY prazo_mais_proximo ASC NULLS LAST LIMIT 500'
    elif ordem == 'atrasadas':
        sql += ' GROUP BY e.id, e.nome ORDER BY demandas_atrasadas DESC LIMIT 500'
    elif ordem == 'recente':
        # Empresas com a demanda (OS) mais nova primeiro — prioriza data de criação no Planner
        sql += (" GROUP BY e.id, e.nome"
                " ORDER BY MAX(COALESCE(NULLIF(d.criado_em_ms,''), d.criado_em)) DESC NULLS LAST"
                " LIMIT 500")
    else:
        sql += ' GROUP BY e.id, e.nome ORDER BY empresa_nome ASC LIMIT 500'
    with get_db() as conn:
        rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            if r.get('numeros_os'):
                os_list = [x.strip() for x in (r['numeros_os'] or '').split(',') if x.strip()]
                r['numeros_os'] = ','.join(dict.fromkeys(os_list))
        return rows


def upsert_empresa(cnpj, nome, **extra):
    cnpj = (cnpj or '').strip()
    nome = (nome or '').strip()
    if not nome:
        return None
    with get_db() as conn:
        if cnpj:
            r = conn.execute('SELECT id FROM empresas WHERE cnpj = ?', (cnpj,)).fetchone()
            if r:
                return r['id']
        r = conn.execute(
            'SELECT id FROM empresas WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))', (nome,)
        ).fetchone()
        if r:
            return r['id']
        cur = conn.execute(
            'INSERT INTO empresas (cnpj, nome, unidade, contato, telefone, email, cidade, uf) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (cnpj or None, nome, extra.get('unidade'), extra.get('contato'),
             extra.get('telefone'), extra.get('email'), extra.get('cidade'), extra.get('uf'))
        )
        return cur.lastrowid


def dados_cadastro_empresa(nome, cnpj=None):
    """CNAE, descrição do CNAE e grau de risco do CADASTRO da empresa.

    O PGR não coletava esses 3 campos em nenhum lugar, então o documento saía com
    os valores da empresa de referência do template (CNAE 43.99-1-03 / "Obras de
    alvenaria" / grau 03) para TODO cliente. Decisão do Matheus em 28/07/2026:
    puxar do cadastro, que é a fonte de verdade.

    Casa por CNPJ e, sem CNPJ, por nome exato — MESMA precedência do
    `enriquecer_empresa` (que faz o caminho inverso: laudo → cadastro), para os
    dois concordarem sobre qual empresa é qual.

    Devolve sempre o dict, com string vazia no que não achou:
      {id, cnae, descricao_cnae, grau_risco}
    """
    nome = str(nome or '').strip()
    cnpj = str(cnpj or '').strip()
    vazio = {'id': None, 'cnae': '', 'descricao_cnae': '', 'grau_risco': ''}
    if not (nome or cnpj):
        return vazio
    with get_db() as conn:
        row = None
        if cnpj:
            row = conn.execute('SELECT * FROM empresas WHERE cnpj = ?', (cnpj,)).fetchone()
        if not row and nome:
            row = conn.execute(
                'SELECT * FROM empresas WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?)) '
                'ORDER BY COALESCE(pendente, 0) ASC, id ASC LIMIT 1', (nome,)).fetchone()
        if not row:
            return vazio
        d = row_to_dict(row)
        return {'id': d.get('id'),
                'cnae': (d.get('cnae') or '').strip(),
                'descricao_cnae': (d.get('descricao_cnae') or '').strip(),
                'grau_risco': (d.get('grau_risco') or '').strip()}


def enriquecer_empresa(dados):
    """Backfill: preenche campos VAZIOS de uma empresa com o que o técnico
    digitou ao gerar um laudo. Casa por CNPJ (se já houver) ou por nome exato;
    NUNCA cria empresa nova e NUNCA sobrescreve um valor já preenchido.
    CNPJ só é gravado se nenhuma OUTRA empresa já o usa (evita violar o UNIQUE).

    Campos defaultados nos formulários (cidade='Belo Horizonte', uf='MG',
    grau_risco='3') ficam de FORA de propósito — salvar default poluiria
    empresas de outras cidades. Retorna {ok, id, atualizados, cnpj_conflito}.
    """
    dados = dados or {}
    nome = str(dados.get('nome') or dados.get('razaoSocial') or '').strip()
    cnpj = str(dados.get('cnpj') or '').strip()
    # Só campos sem default enganoso no form
    campos = ['cnpj', 'contato', 'telefone', 'email',
              'cep', 'endereco', 'numero', 'bairro', 'cnae', 'descricao_cnae']
    with get_db() as conn:
        row = None
        if cnpj:
            row = conn.execute('SELECT * FROM empresas WHERE cnpj = ?', (cnpj,)).fetchone()
        if not row and nome:
            row = conn.execute(
                'SELECT * FROM empresas WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?)) '
                'ORDER BY COALESCE(pendente, 0) ASC, id ASC LIMIT 1', (nome,)).fetchone()
        if not row:
            return {'ok': False, 'motivo': 'empresa nao encontrada'}
        emp = row_to_dict(row)
        eid = emp['id']
        sets, params, atualizados = [], [], []
        cnpj_conflito = False
        for c in campos:
            novo = dados.get(c)
            novo = str(novo).strip() if novo is not None else ''
            if not novo:
                continue
            if emp.get(c) not in (None, '', 0, '0'):
                continue  # já preenchido — não sobrescreve
            if c == 'cnpj':
                dup = conn.execute('SELECT id FROM empresas WHERE cnpj = ? AND id <> ?',
                                   (novo, eid)).fetchone()
                if dup:
                    cnpj_conflito = True
                    continue
            sets.append(f'{c} = ?'); params.append(novo); atualizados.append(c)
        if sets:
            params.append(eid)
            conn.execute(f'UPDATE empresas SET {", ".join(sets)} WHERE id = ?', params)
        return {'ok': True, 'id': eid, 'atualizados': atualizados, 'cnpj_conflito': cnpj_conflito}


# ── Pipeline stats ─────────────────────────────────────────────────────

def list_raw_tasks(filtros=None, limit=200):
    f = filtros or {}
    sql = 'SELECT * FROM planner_raw_tasks WHERE 1=1'
    params = []
    if f.get('status'):
        sql += ' AND sync_status=?'; params.append(f['status'])
    if f.get('bucket'):
        sql += ' AND LOWER(planner_bucket) LIKE LOWER(?)'; params.append(f'%{f["bucket"]}%')
    if f.get('grupo'):
        sql += ' AND LOWER(planner_group_nome) LIKE LOWER(?)'; params.append(f'%{f["grupo"]}%')
    if f.get('titulo'):
        sql += ' AND LOWER(titulo) LIKE LOWER(?)'; params.append(f'%{f["titulo"]}%')
    if f.get('planner_id'):
        sql += ' AND planner_task_id=?'; params.append(f['planner_id'])
    sql += f' ORDER BY synced_at DESC LIMIT {int(limit)}'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def stats_raw_pipeline():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT sync_status, COUNT(*) AS qtd,
                   COUNT(DISTINCT planner_bucket) AS buckets_distintos
            FROM planner_raw_tasks
            GROUP BY sync_status
        ''').fetchall()
        buckets = conn.execute('''
            SELECT planner_bucket, sync_status, COUNT(*) AS qtd
            FROM planner_raw_tasks
            GROUP BY planner_bucket, sync_status
            ORDER BY qtd DESC LIMIT 50
        ''').fetchall()
        return {
            'por_status': [row_to_dict(r) for r in rows],
            'por_bucket': [row_to_dict(r) for r in buckets],
        }


# ── Coletas ────────────────────────────────────────────────────────────

def save_coleta_outros(data):
    import json as _json
    cid = data.get('id')
    campos = ['tipo', 'empresa_id', 'empresa_nome', 'demanda_id', 'numero_os',
              'avaliador', 'tecnico_login', 'data_coleta', 'acompanhante', 'hora_inicio', 'hora_termino',
              'unidade', 'cidade', 'observacao', 'status']
    vals = {c: data.get(c) for c in campos}
    extras = {k: v for k, v in data.items() if k not in campos + ['id', 'dados_json']}
    vals['dados_json'] = _json.dumps(extras, ensure_ascii=False) if extras else None
    with get_db() as conn:
        if cid:
            sets = ', '.join(k + '=?' for k in vals) + ', atualizado_em=CURRENT_TIMESTAMP'
            conn.execute('UPDATE coletas_outros SET ' + sets + ' WHERE id=?',
                         list(vals.values()) + [cid])
        else:
            cols = ', '.join(vals.keys())
            phs  = ', '.join(['?'] * len(vals))
            cur  = conn.execute('INSERT INTO coletas_outros (' + cols + ') VALUES (' + phs + ')',
                                list(vals.values()))
            cid  = cur.lastrowid
    return cid


def list_coletas_outros(filtros=None):
    filtros = filtros or {}
    with get_db() as conn:
        conds, params = ['1=1'], []
        if filtros.get('empresa_id'):
            conds.append('empresa_id=?'); params.append(filtros['empresa_id'])
        if filtros.get('tipo'):
            conds.append('tipo=?'); params.append(filtros['tipo'])
        if filtros.get('demanda_id'):
            conds.append('demanda_id=?'); params.append(filtros['demanda_id'])
        rows = conn.execute(
            f"SELECT * FROM coletas_outros WHERE {' AND '.join(conds)} ORDER BY criado_em DESC LIMIT 200",
            params
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_coleta_outros(cid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM coletas_outros WHERE id=?', (cid,)).fetchone()
        return row_to_dict(row) if row else None


def calcular_metricas_demanda(demanda_id):
    """Calcula e persiste lead_time, delay, retrabalho para uma demanda."""
    with get_db() as conn:
        d = conn.execute('SELECT * FROM demandas WHERE id=?', (demanda_id,)).fetchone()
        if not d:
            return None
        d = row_to_dict(d)

        # Lead time: dias entre criado_em e conclusão (ou hoje)
        try:
            from datetime import date, datetime
            criado = d.get('criado_em') or ''
            if criado:
                criado_d = datetime.fromisoformat(criado[:10]).date()
                if d.get('status') == 'concluida' and d.get('data_conclusao'):
                    fim_d = datetime.fromisoformat(d['data_conclusao'][:10]).date()
                else:
                    fim_d = date.today()
                lead_time = (fim_d - criado_d).days
            else:
                lead_time = None
        except Exception:
            lead_time = None

        # Delay: dias em atraso (prazo - hoje, negativo = atrasado)
        try:
            delay = None
            if d.get('prazo'):
                prazo_d = datetime.fromisoformat(d['prazo'][:10]).date()
                delay = (date.today() - prazo_d).days  # positivo = atrasado
        except Exception:
            delay = None

        # Retrabalho e total de visitas
        stats = conn.execute(
            'SELECT COUNT(*) AS total, SUM(retrabalho) AS ret FROM visitas_tecnicas WHERE demanda_id=?',
            (demanda_id,)
        ).fetchone()
        stats = row_to_dict(stats) if stats else {}
        visitas_total = stats.get('total') or 0
        retrabalho = int(stats.get('ret') or 0)

        # Upsert na tabela
        conn.execute('''
            INSERT INTO metricas_operacionais (demanda_id, lead_time_dias, delay_dias, retrabalho, visitas_total, calculado_em)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(demanda_id) DO UPDATE SET
              lead_time_dias=excluded.lead_time_dias,
              delay_dias=excluded.delay_dias,
              retrabalho=excluded.retrabalho,
              visitas_total=excluded.visitas_total,
              calculado_em=CURRENT_TIMESTAMP
        ''', (demanda_id, lead_time, delay, retrabalho, visitas_total))

        return {
            'demanda_id': demanda_id,
            'lead_time_dias': lead_time,
            'delay_dias': delay,
            'retrabalho': retrabalho,
            'visitas_total': visitas_total,
        }


def calcular_metricas_lote():
    """Recalcula métricas para todas as demandas."""
    with get_db() as conn:
        ids = [r['id'] for r in conn.execute('SELECT id FROM demandas').fetchall()]
    resultados = []
    for did in ids:
        try:
            m = calcular_metricas_demanda(did)
            if m:
                resultados.append(m)
        except Exception:
            pass
    return {'total': len(resultados)}


# ── Planejamentos ──────────────────────────────────────────────────────

def criar_planejamento(data: dict) -> int:
    import json as _json
    campos = ['demanda_id', 'empresa_id', 'numero_os', 'tecnico', 'data_prevista',
              'agentes_previstos', 'qtd_dosim_prevista', 'qtd_bombas_previstas',
              'equipamentos_json', 'observacao', 'status', 'dias_estimados', 'cnpj',
              'checklist_prevista', 'divergencias_json']
    vals = {}
    for c in campos:
        v = data.get(c)
        if isinstance(v, (dict, list)):
            v = _json.dumps(v, ensure_ascii=False)
        if v is not None:
            vals[c] = v
    vals.setdefault('status', 'rascunho')
    cols = ', '.join(vals.keys())
    phs  = ', '.join(['?'] * len(vals))
    with get_db() as conn:
        cur = conn.execute(f'INSERT INTO planejamentos ({cols}) VALUES ({phs})', list(vals.values()))
        return cur.lastrowid


def atualizar_planejamento(pid: int, data: dict) -> bool:
    """Atualiza campos editáveis de um planejamento existente."""
    import json as _json
    campos = ['demanda_id', 'empresa_id', 'numero_os', 'tecnico', 'data_prevista',
              'agentes_previstos', 'qtd_dosim_prevista', 'qtd_bombas_previstas',
              'equipamentos_json', 'observacao', 'status', 'dias_estimados', 'cnpj',
              'checklist_prevista', 'divergencias_json']
    vals = {}
    for c in campos:
        if c not in data:
            continue
        v = data.get(c)
        if isinstance(v, (dict, list)):
            v = _json.dumps(v, ensure_ascii=False)
        vals[c] = v
    if not vals:
        return False
    sets = ', '.join(f'{c}=?' for c in vals.keys())
    params = list(vals.values()) + [pid]
    with get_db() as conn:
        conn.execute(
            f'UPDATE planejamentos SET {sets}, atualizado_em=CURRENT_TIMESTAMP WHERE id=?',
            params)
        return True


def get_planejamento(pid: int):
    with get_db() as conn:
        r = conn.execute('''
            SELECT p.*, e.nome AS empresa_nome, d.titulo AS demanda_titulo
            FROM planejamentos p
            LEFT JOIN empresas e ON e.id = p.empresa_id
            LEFT JOIN demandas d ON d.id = p.demanda_id
            WHERE p.id=?
        ''', (pid,)).fetchone()
        return row_to_dict(r)


def list_planejamentos(filtros=None) -> list:
    f = filtros or {}
    sql = '''
        SELECT p.*, e.nome AS empresa_nome, d.titulo AS demanda_titulo
        FROM planejamentos p
        LEFT JOIN empresas e ON e.id = p.empresa_id
        LEFT JOIN demandas d ON d.id = p.demanda_id
        WHERE 1=1
    '''
    params = []
    if f.get('tecnico'):
        sql += ' AND p.tecnico LIKE ?'; params.append(f'%{f["tecnico"]}%')
    if f.get('status'):
        sql += ' AND p.status=?'; params.append(f['status'])
    if f.get('empresa_id'):
        sql += ' AND p.empresa_id=?'; params.append(f['empresa_id'])
    if f.get('demanda_id'):
        sql += ' AND p.demanda_id=?'; params.append(f['demanda_id'])
    sql += ' ORDER BY p.data_prevista DESC, p.criado_em DESC LIMIT 500'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def update_planejamento_status(pid: int, status: str) -> bool:
    with get_db() as conn:
        conn.execute(
            'UPDATE planejamentos SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?',
            (status, pid))
        return True


# ── Visitas Técnicas ───────────────────────────────────────────────────

def criar_visita(data: dict) -> int:
    campos = ['planejamento_id', 'demanda_id', 'empresa_id', 'tecnico',
              'data_visita', 'hora_inicio', 'hora_termino',
              'tipo_visita', 'resultado', 'retrabalho', 'justificativa', 'observacao_geral',
              'acompanhante', 'cargo_acompanhante', 'numero_os', 'client_uuid']
    vals = {c: data.get(c) for c in campos if data.get(c) is not None}
    vals.setdefault('tipo_visita', 'medicao')
    vals.setdefault('resultado', 'pendente')
    cols = ', '.join(vals.keys())
    phs  = ', '.join(['?'] * len(vals))
    with get_db() as conn:
        cur = conn.execute(f'INSERT INTO visitas_tecnicas ({cols}) VALUES ({phs})', list(vals.values()))
        pid = data.get('planejamento_id')
        if pid:
            conn.execute(
                "UPDATE planejamentos SET status='em_execucao', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                (pid,))
        return cur.lastrowid


def get_visita(vid: int):
    with get_db() as conn:
        r = conn.execute('''
            SELECT v.*, e.nome AS empresa_nome,
                   COALESCE(v.numero_os, p.numero_os) AS numero_os,
                   p.agentes_previstos, p.data_prevista
            FROM visitas_tecnicas v
            LEFT JOIN empresas e ON e.id = v.empresa_id
            LEFT JOIN planejamentos p ON p.id = v.planejamento_id
            WHERE v.id=?
        ''', (vid,)).fetchone()
        return row_to_dict(r)


def list_visitas(filtros=None) -> list:
    f = filtros or {}
    sql = '''
        SELECT v.*, e.nome AS empresa_nome,
               COALESCE(v.numero_os, p.numero_os) AS numero_os
        FROM visitas_tecnicas v
        LEFT JOIN empresas e ON e.id = v.empresa_id
        LEFT JOIN planejamentos p ON p.id = v.planejamento_id
        WHERE 1=1
    '''
    params = []
    if f.get('tecnico'):
        sql += ' AND v.tecnico LIKE ?'; params.append(f'%{f["tecnico"]}%')
    if f.get('demanda_id'):
        sql += ' AND v.demanda_id=?'; params.append(f['demanda_id'])
    if f.get('planejamento_id'):
        sql += ' AND v.planejamento_id=?'; params.append(f['planejamento_id'])
    sql += ' ORDER BY v.data_visita DESC LIMIT 500'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def concluir_visita(vid: int, data: dict) -> bool:
    import json as _json
    resultado = data.get('resultado', 'concluido')
    with get_db() as conn:
        conn.execute('''
            UPDATE visitas_tecnicas
            SET resultado=?, justificativa=?, hora_termino=COALESCE(hora_termino,?),
                acompanhante=?, cargo_acompanhante=?,
                imprevisto_tipo=COALESCE(?, imprevisto_tipo),
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (resultado, data.get('justificativa'), data.get('hora_termino'),
              data.get('acompanhante'), data.get('cargo_acompanhante'),
              data.get('imprevisto_tipo'), vid))

        row = conn.execute('SELECT planejamento_id FROM visitas_tecnicas WHERE id=?', (vid,)).fetchone()
        plan_id = row['planejamento_id'] if row else None

        def _j(v):
            return _json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v

        conn.execute('''
            INSERT INTO execucao_campo
                (visita_id, planejamento_id, agentes_executados, agentes_nao_executados,
                 agentes_adicionados, justificativa_causa, cobravel, observacao,
                 acompanhante, cargo_acompanhante, dosimetros_usados, bombas_usadas, trabalhadores_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (vid, plan_id,
              _j(data.get('agentes_executados')),
              _j(data.get('agentes_nao_executados')),
              _j(data.get('agentes_adicionados')),
              # O mobile envia a chave 'justificativa'; o desktop pode mandar
              # 'justificativa_causa'. Sem o fallback, toda visita de campo
              # gravava causa NULL e o relatório "não executados" mostrava "—".
              data.get('justificativa_causa') or data.get('justificativa'),
              int(data.get('cobravel', 0)),
              data.get('observacao'),
              data.get('acompanhante'),
              data.get('cargo_acompanhante'),
              data.get('dosimetros_usados'),
              data.get('bombas_usadas'),
              _j(data.get('trabalhadores_json'))))

        if plan_id and resultado == 'concluido':
            conn.execute(
                "UPDATE planejamentos SET status='concluido', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                (plan_id,))
    return True


# ── Coletas Ruído ──────────────────────────────────────────────────────

def list_coletas_ruido(filtros=None):
    f = filtros or {}
    sql = (
        'SELECT cr.*, COUNT(crf.id) AS total_func '
        'FROM coletas_ruido cr '
        'LEFT JOIN coletas_ruido_func crf ON crf.coleta_id = cr.id '
        'WHERE 1=1'
    )
    params = []
    if f.get('empresa'):
        sql += ' AND LOWER(cr.empresa_nome) LIKE LOWER(?)'; params.append('%' + f['empresa'] + '%')
    if f.get('status'):
        sql += ' AND cr.status = ?'; params.append(f['status'])
    sql += ' GROUP BY cr.id ORDER BY cr.atualizado_em DESC LIMIT 200'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def get_coleta_ruido(cid):
    with get_db() as conn:
        c = conn.execute('SELECT * FROM coletas_ruido WHERE id=?', (cid,)).fetchone()
        if not c:
            return None
        c = row_to_dict(c)
        funcs = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM coletas_ruido_func WHERE coleta_id=? ORDER BY seq', (cid,)).fetchall()]
        c['funcionarios']  = funcs
        c['trabalhadores'] = funcs
        return c


def save_coleta_ruido(data):
    cid = data.get('id')
    campos = ['empresa_id', 'empresa_nome', 'demanda_id', 'acompanhante',
              'cargo_acompanhante', 'tecnico', 'tecnico_login', 'data_coleta', 'hora_inicio',
              'hora_termino', 'calibrador', 'calibracao_inicial', 'calibracao_final',
              'desvio_calibracao', 'status_calibracao', 'unidade', 'cidade',
              'resp_empresa', 'os', 'observacao', 'status']
    vals = {c: data.get(c) for c in campos}
    ci = vals.get('calibracao_inicial')
    cf = vals.get('calibracao_final')
    if ci is not None and cf is not None:
        try:
            ci_f = float(str(ci).replace(',', '.'))
            cf_f = float(str(cf).replace(',', '.'))
            desvio = round(cf_f - ci_f, 2)
            vals['desvio_calibracao'] = desvio
            vals['status_calibracao'] = 'divergente' if abs(desvio) > 0.5 else 'conforme'
        except Exception:
            pass
    with get_db() as conn:
        if cid:
            sets = ', '.join(k + '=?' for k in vals) + ', atualizado_em=CURRENT_TIMESTAMP'
            conn.execute('UPDATE coletas_ruido SET ' + sets + ' WHERE id=?',
                         list(vals.values()) + [cid])
        else:
            cols = ', '.join(vals.keys())
            phs  = ', '.join(['?'] * len(vals))
            cur  = conn.execute('INSERT INTO coletas_ruido (' + cols + ') VALUES (' + phs + ')',
                                list(vals.values()))
            cid  = cur.lastrowid
        funcs = data.get('trabalhadores') or data.get('funcionarios')
        if funcs is not None:
            conn.execute('DELETE FROM coletas_ruido_func WHERE coleta_id=?', (cid,))
            for i, func in enumerate(funcs, 1):
                conn.execute(
                    'INSERT INTO coletas_ruido_func '
                    '(coleta_id,seq,nome,cargo,setor,almoco,serie_dosimetro,pausa,almoco_fim) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    (cid, i, func.get('nome', ''), func.get('cargo', ''),
                     func.get('setor', ''),
                     1 if (func.get('almoco') or func.get('pausa')) else 0,
                     # o wizard manda o nº do dosímetro como 'sn'
                     func.get('serie_dosimetro') or func.get('dosimetro') or func.get('sn') or '',
                     # 'almoco' legado pode ser flag (0/1) — só aproveita se for hh:mm
                     func.get('pausa') or (func.get('almoco') if isinstance(func.get('almoco'), str) else '') or '',
                     func.get('almoco_fim') or ''))
    return cid


# ── Coletas Químico ────────────────────────────────────────────────────

def list_coletas_quimico(filtros=None):
    f = filtros or {}
    sql = (
        'SELECT cq.*, COUNT(cqa.id) AS total_amostradores '
        'FROM coletas_quimico cq '
        'LEFT JOIN coletas_quimico_amostr cqa ON cqa.coleta_id = cq.id '
        'WHERE 1=1'
    )
    params = []
    if f.get('empresa'):
        sql += ' AND LOWER(cq.empresa_nome) LIKE LOWER(?)'; params.append('%' + f['empresa'] + '%')
    if f.get('status'):
        sql += ' AND cq.status = ?'; params.append(f['status'])
    sql += ' GROUP BY cq.id ORDER BY cq.atualizado_em DESC LIMIT 200'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def get_coleta_quimico(cid):
    with get_db() as conn:
        c = conn.execute('SELECT * FROM coletas_quimico WHERE id=?', (cid,)).fetchone()
        if not c:
            return None
        c = row_to_dict(c)
        c['amostradores'] = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM coletas_quimico_amostr WHERE coleta_id=? ORDER BY seq', (cid,)).fetchall()]
        return c


def save_coleta_quimico(data):
    cid = data.get('id')
    campos = ['empresa_id', 'empresa_nome', 'demanda_id', 'responsavel_coleta',
              'tecnico_login',
              'cidade', 'unidade', 'data_coleta', 'dia_semana', 'turno',
              'nome_funcionario', 'jornada', 'funcao', 'setor', 'local_atividade',
              'atividade', 'ventilacao', 'ambiente', 'condicoes_meteo',
              'temperatura', 'umidade', 'outras_condicoes', 'substancias',
              'fracao', 'tempo_exposto', 'bomba', 'id_bomba', 'data_cal_bomba',
              'id_calibrador', 'acessorios', 'epis', 'epc', 'observacao', 'status']
    vals = {c: data.get(c) for c in campos}
    with get_db() as conn:
        if cid:
            sets = ', '.join(k + '=?' for k in vals) + ', atualizado_em=CURRENT_TIMESTAMP'
            conn.execute('UPDATE coletas_quimico SET ' + sets + ' WHERE id=?',
                         list(vals.values()) + [cid])
        else:
            cols = ', '.join(vals.keys())
            phs  = ', '.join(['?'] * len(vals))
            cur  = conn.execute('INSERT INTO coletas_quimico (' + cols + ') VALUES (' + phs + ')',
                                list(vals.values()))
            cid  = cur.lastrowid
        if 'amostradores' in data:
            conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
            for i, am in enumerate(data['amostradores'], 1):
                vi  = float(am.get('vazao_inicial') or 0)
                vf  = float(am.get('vazao_final') or 0)
                vm  = (vi + vf) / 2 if vi and vf else 0
                t   = float(am.get('tempo_min') or 0)
                vol = round(vm * t, 3) if vm and t else 0
                dv  = round(abs(vi - vf) / vi * 100, 2) if vi else 0
                conn.execute(
                    'INSERT INTO coletas_quimico_amostr '
                    '(coleta_id,seq,id_amostrador,tipo_amostrador,substancia,bomba,'
                    'vazao_inicial,vazao_final,vazao_media,hora_inicio,hora_final,'
                    'intervalos,tempo_min,volume_L,variacao_vazao,status_variacao) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (cid, i, am.get('id_amostrador', ''), am.get('tipo_amostrador', ''),
                     am.get('substancia', ''), am.get('bomba', ''), vi, vf, round(vm, 3),
                     am.get('hora_inicio', ''), am.get('hora_final', ''),
                     am.get('intervalos', ''), t, vol, dv,
                     'divergente' if dv > 5 else 'conforme'))
    return cid
