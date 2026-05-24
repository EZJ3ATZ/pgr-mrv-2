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
        """Dias desde uma data (days since)."""
        return f"(CURRENT_DATE - ({col})::date)"

    def _du(col):
        """Dias até uma data (days until)."""
        return f"(({col})::date - CURRENT_DATE)"

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


# ── Wrapper PostgreSQL (faz psycopg2 se comportar como sqlite3) ────────

class _PGCursor:
    """Cursor psycopg2 com interface compatível com sqlite3.Row."""

    def __init__(self, pg_conn):
        self._pg_conn = pg_conn
        self._cur = None
        self._lastrowid = None

    def execute(self, sql, params=None):
        self._cur = self._pg_conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor)
        sql = sql.replace('?', '%s')
        is_insert = sql.strip().upper().startswith('INSERT')
        if is_insert and 'RETURNING' not in sql.upper():
            sql = sql.rstrip(' \n;') + ' RETURNING id'
        self._cur.execute(sql, params or ())
        if is_insert:
            row = self._cur.fetchone()
            self._lastrowid = int(row['id']) if row and row.get('id') is not None else None
        return self

    def executemany(self, sql, params_list):
        self._cur = self._pg_conn.cursor()
        sql = sql.replace('?', '%s')
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
    """Conexão psycopg2 com interface compatível com sqlite3.Connection."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

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
        self._conn.rollback()

    def close(self):
        self._conn.close()


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
    status          TEXT DEFAULT 'Estoque',
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
    amostrador_id       INTEGER NOT NULL,
    avaliador           TEXT,
    bomba               TEXT,
    vazao_calibrada     REAL,
    volume_recomendado  REAL,
    tempo_calculado_min REAL,
    tempo_calculado_max REAL,
    data_medicao        TEXT,
    observacao          TEXT,
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
    FOREIGN KEY (coleta_id) REFERENCES coletas_ruido(id) ON DELETE CASCADE
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
CREATE INDEX IF NOT EXISTS idx_col_outros_tipo    ON coletas_outros(tipo);
CREATE INDEX IF NOT EXISTS idx_col_outros_empresa ON coletas_outros(empresa_id);
CREATE INDEX IF NOT EXISTS idx_raw_planner_task  ON planner_raw_tasks(planner_task_id);
CREATE INDEX IF NOT EXISTS idx_raw_bucket        ON planner_raw_tasks(planner_bucket);
CREATE INDEX IF NOT EXISTS idx_raw_sync_status   ON planner_raw_tasks(sync_status);
CREATE INDEX IF NOT EXISTS idx_raw_synced_at     ON planner_raw_tasks(synced_at);
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


def _connect_pg():
    conn = psycopg2.connect(_DATABASE_URL)
    conn.autocommit = False
    return _PGConn(conn)


def _connect():
    return _connect_pg() if USE_PG else _connect_sqlite()


@contextmanager
def get_db():
    conn = _connect()
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
        cols = _get_table_cols(conn, table)
        if col not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')
    except Exception as e:
        print(f'[migrate] {table}.{col}: {e}')


# ── init_db ────────────────────────────────────────────────────────────

def init_db():
    """Cria tabelas se não existirem. Idempotente.
    Se o banco estiver vazio, faz auto-seed a partir de controle/seed/.
    """
    schema = SCHEMA_PG if USE_PG else SCHEMA_SQLITE
    with get_db() as conn:
        conn.executescript(schema)
        conn.executescript(SCHEMA_INDEXES)
        _migrate(conn)
        count = conn.execute('SELECT COUNT(*) AS c FROM amostradores').fetchone()['c']
    if count == 0:
        _auto_seed()


def _migrate(conn):
    """Garante que todas as colunas e índices existam. Idempotente."""

    # ── empresas ──
    _add_col(conn, 'empresas', 'pendente', 'INTEGER DEFAULT 0')

    # ── demandas: campos Planner ──
    demandas_extra = {
        'nome_tarefa': 'TEXT', 'data_conclusao': 'TEXT', 'responsavel': 'TEXT',
        'status_planner': 'TEXT', 'progresso': 'INTEGER DEFAULT 0',
        'checklist': 'TEXT', 'checklist_prog': 'TEXT', 'bucket': 'TEXT',
        'etiquetas': 'TEXT', 'descricao': 'TEXT', 'cnpj': 'TEXT',
        'tem_comentarios': 'INTEGER DEFAULT 0',
        'contato_feito': 'INTEGER DEFAULT 0', 'contato_feito_em': 'TEXT',
        'contato_feito_por': 'TEXT', 'planner_task_id': 'TEXT',
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

    # ── amostradores ──
    amostr_extra = {
        'data_envio_lab': 'TEXT', 'dias_validade': 'INTEGER DEFAULT 45',
        'lote': 'TEXT', 'observacao_venc': 'TEXT',
        'cert_numero': 'TEXT', 'cert_validade': 'TEXT',
        'cert_laboratorio': 'TEXT', 'cert_arquivo': 'TEXT',
    }
    for col, tipo in amostr_extra.items():
        _add_col(conn, 'amostradores', col, tipo)

    # ── coletas_ruido ──
    for col, tipo in [('calibrador', 'TEXT'), ('unidade', 'TEXT'),
                      ('cidade', 'TEXT'), ('resp_empresa', 'TEXT'),
                      ('os', 'TEXT'), ('visita_id', 'INTEGER'),
                      ('planejamento_id', 'INTEGER')]:
        _add_col(conn, 'coletas_ruido', col, tipo)

    # ── coletas_quimico_amostr ──
    _add_col(conn, 'coletas_quimico_amostr', 'bomba', 'TEXT')

    # ── coletas_quimico / coletas_outros ──
    for tbl in ('coletas_quimico', 'coletas_outros'):
        _add_col(conn, tbl, 'visita_id', 'INTEGER')
        _add_col(conn, tbl, 'planejamento_id', 'INTEGER')

    # ── visitas_tecnicas ──
    for col, tipo in [('planejamento_id', 'INTEGER'),
                      ("resultado", "TEXT DEFAULT 'pendente'"),
                      ('observacao_geral', 'TEXT'),
                      ('atualizado_em', 'TEXT DEFAULT CURRENT_TIMESTAMP')]:
        _add_col(conn, 'visitas_tecnicas', col, tipo)

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
                  AND d.origem = 'planner';
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


# ── Amostradores ───────────────────────────────────────────────────────

def list_amostradores(filtros=None):
    sql = f"""
        SELECT a.*, e.nome AS empresa_nome,
               {_ds("COALESCE(NULLIF(a.data_medicao,''), NULLIF(a.data_entrada,''), a.atualizado_em)")} AS tempo_parado
        FROM amostradores a
        LEFT JOIN empresas e ON e.id = a.empresa_id
        WHERE 1=1
    """
    params = []
    f = filtros or {}
    if f.get('status'):
        sql += ' AND a.status = ?'; params.append(f['status'])
    if f.get('tipo'):
        sql += ' AND a.tipo = ?'; params.append(f['tipo'])
    if f.get('codigo'):
        sql += ' AND a.codigo LIKE ?'; params.append(f'%{f["codigo"]}%')
    if f.get('empresa'):
        sql += ' AND e.nome LIKE ?'; params.append(f'%{f["empresa"]}%')
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
        sql += ' AND e.nome LIKE ?'; params.append(f'%{f["empresa"]}%')
    if f.get('os'):
        sql += ' AND d.numero_os LIKE ?'; params.append(f'%{f["os"]}%')
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
                conn.execute("UPDATE demandas SET empresa_id=? WHERE empresa_id=?", (id_princ, dup_id))
                conn.execute("UPDATE amostradores SET empresa_id=? WHERE empresa_id=?", (id_princ, dup_id))
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
            d['medicoes_pendentes'] = [row_to_dict(r) for r in conn.execute(
                "SELECT id, agente, tipo_amostrador, qtd_pontos_feita, qtd_pontos_prevista "
                "FROM medicoes WHERE demanda_id=? AND status!='realizado' ORDER BY agente",
                (d['id'],)).fetchall()]
        emp['demandas'] = dems
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
    lab = 'a.data_envio_lab'
    val = 'COALESCE(a.dias_validade,45)'
    sql = f"""
        SELECT a.*, e.nome AS empresa_nome,
               {_lab_days_left(lab, val)} AS dias_para_vencer,
               {_lab_days_in(lab)} AS dias_no_lab
        FROM amostradores a
        LEFT JOIN empresas e ON e.id = a.empresa_id
        WHERE a.data_envio_lab IS NOT NULL
          AND a.data_envio_lab != ''
          AND a.status != 'Devolvido'
        ORDER BY dias_para_vencer ASC
        LIMIT 500
    """
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql).fetchall()]


def contar_vencendo():
    lab = 'data_envio_lab'
    val = 'COALESCE(dias_validade,45)'
    if USE_PG:
        sql = f"""
            SELECT
              SUM(CASE WHEN CURRENT_DATE > ({lab})::date + {val} THEN 1 ELSE 0 END) AS vencidos,
              SUM(CASE WHEN CURRENT_DATE BETWEEN ({lab})::date + {val} - 3
                                            AND ({lab})::date + {val} THEN 1 ELSE 0 END) AS urgente,
              SUM(CASE WHEN CURRENT_DATE BETWEEN ({lab})::date + {val} - 7
                                            AND ({lab})::date + {val} - 4 THEN 1 ELSE 0 END) AS alerta,
              COUNT(*) AS total_no_lab
            FROM amostradores
            WHERE data_envio_lab IS NOT NULL AND data_envio_lab != ''
              AND status != 'Devolvido'
        """
    else:
        sql = f"""
            SELECT
              SUM(CASE WHEN julianday('now') > julianday({lab}) + {val} THEN 1 ELSE 0 END) AS vencidos,
              SUM(CASE WHEN julianday('now') BETWEEN julianday({lab}) + {val} - 3
                                               AND julianday({lab}) + {val} THEN 1 ELSE 0 END) AS urgente,
              SUM(CASE WHEN julianday('now') BETWEEN julianday({lab}) + {val} - 7
                                               AND julianday({lab}) + {val} - 4 THEN 1 ELSE 0 END) AS alerta,
              COUNT(*) AS total_no_lab
            FROM amostradores
            WHERE data_envio_lab IS NOT NULL AND data_envio_lab != ''
              AND status != 'Devolvido'
        """
    with get_db() as conn:
        r = conn.execute(sql).fetchone()
        return {
            'vencidos':     int(r['vencidos'] or 0),
            'urgente':      int(r['urgente']  or 0),
            'alerta':       int(r['alerta']   or 0),
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

    base_filter = f"data_envio_lab IS NOT NULL AND data_envio_lab != '' AND status != 'Devolvido'"
    sql = f"""
        SELECT
          (SELECT COUNT(*) FROM amostradores) AS total_amostradores,
          (SELECT COUNT(*) FROM amostradores WHERE status='Estoque') AS estoque,
          (SELECT COUNT(*) FROM amostradores WHERE status='Laboratorio') AS laboratorio,
          (SELECT COUNT(*) FROM amostradores WHERE status='Reservado') AS reservados,
          (SELECT COUNT(*) FROM amostradores WHERE status='Devolvido') AS devolvidos,
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


def list_operational_por_empresa(filtros=None):
    sql = f"""
        SELECT e.id AS empresa_id,
               e.nome AS empresa_nome,
               e.cnpj,
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
                   THEN 1 ELSE 0 END) AS demandas_atrasadas
        FROM empresas e
        JOIN demandas d ON d.empresa_id = e.id
        LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
        WHERE d.tipo_demanda NOT IN ('interna', 'administrativa')
          AND d.empresa_id > 0
          AND d.origem = 'planner'
    """
    params = []
    f = filtros or {}
    if f.get('status') == 'pendente':
        sql += " AND d.status != 'concluida'"
    elif f.get('status') == 'concluida':
        sql += " AND d.status = 'concluida'"
    if f.get('empresa'):
        sql += ' AND LOWER(e.nome) LIKE LOWER(?)'; params.append(f'%{f["empresa"]}%')
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
              'avaliador', 'data_coleta', 'acompanhante', 'hora_inicio', 'hora_termino',
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


# ── Planejamentos ──────────────────────────────────────────────────────

def criar_planejamento(data: dict) -> int:
    import json as _json
    campos = ['demanda_id', 'empresa_id', 'numero_os', 'tecnico', 'data_prevista',
              'agentes_previstos', 'qtd_dosim_prevista', 'qtd_bombas_previstas',
              'equipamentos_json', 'observacao', 'status']
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


def get_planejamento(pid: int):
    with get_db() as conn:
        r = conn.execute('SELECT * FROM planejamentos WHERE id=?', (pid,)).fetchone()
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
              'tipo_visita', 'resultado', 'retrabalho', 'justificativa', 'observacao_geral']
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
            SELECT v.*, e.nome AS empresa_nome, p.numero_os, p.agentes_previstos, p.data_prevista
            FROM visitas_tecnicas v
            LEFT JOIN empresas e ON e.id = v.empresa_id
            LEFT JOIN planejamentos p ON p.id = v.planejamento_id
            WHERE v.id=?
        ''', (vid,)).fetchone()
        return row_to_dict(r)


def list_visitas(filtros=None) -> list:
    f = filtros or {}
    sql = '''
        SELECT v.*, e.nome AS empresa_nome, p.numero_os
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
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (resultado, data.get('justificativa'), data.get('hora_termino'), vid))

        row = conn.execute('SELECT planejamento_id FROM visitas_tecnicas WHERE id=?', (vid,)).fetchone()
        plan_id = row['planejamento_id'] if row else None

        def _j(v):
            return _json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v

        conn.execute('''
            INSERT INTO execucao_campo
                (visita_id, planejamento_id, agentes_executados, agentes_nao_executados,
                 agentes_adicionados, justificativa_causa, cobravel, observacao)
            VALUES (?,?,?,?,?,?,?,?)
        ''', (vid, plan_id,
              _j(data.get('agentes_executados')),
              _j(data.get('agentes_nao_executados')),
              _j(data.get('agentes_adicionados')),
              data.get('justificativa_causa'),
              int(data.get('cobravel', 0)),
              data.get('observacao')))

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
              'cargo_acompanhante', 'tecnico', 'data_coleta', 'hora_inicio',
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
                    '(coleta_id,seq,nome,cargo,setor,almoco,serie_dosimetro) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (cid, i, func.get('nome', ''), func.get('cargo', ''),
                     func.get('setor', ''), 1 if func.get('almoco') else 0,
                     func.get('serie_dosimetro', func.get('dosimetro', ''))))
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
