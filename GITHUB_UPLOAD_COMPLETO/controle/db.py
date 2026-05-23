# -*- coding: utf-8 -*-
"""SQLite database para Controle de Medicoes e Amostradores."""
import os
import sqlite3
from datetime import datetime
from contextlib import contextmanager

# Pasta /data e dela tira o controle.db (volume persistente do Railway)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('CONTROLE_DATA_DIR', os.path.join(BASE_DIR, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'controle.db')


SCHEMA = """
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
    -- Campos do Planner
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
    -- SLA / contato cliente
    contato_feito       INTEGER DEFAULT 0,
    contato_feito_em    TEXT,
    contato_feito_por   TEXT,
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
    -- Controle de vencimento (laboratorio cobra apos N dias)
    data_envio_lab  TEXT,
    dias_validade   INTEGER DEFAULT 45,
    lote            TEXT,
    observacao_venc TEXT,
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
CREATE INDEX IF NOT EXISTS idx_amostr_codigo ON amostradores(codigo);
CREATE INDEX IF NOT EXISTS idx_amostr_status ON amostradores(status);
CREATE INDEX IF NOT EXISTS idx_amostr_tipo   ON amostradores(tipo);

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
CREATE INDEX IF NOT EXISTS idx_med_demanda ON medicoes(demanda_id);
CREATE INDEX IF NOT EXISTS idx_med_status  ON medicoes(status);
CREATE INDEX IF NOT EXISTS idx_med_agente  ON medicoes(agente);
CREATE INDEX IF NOT EXISTS idx_dem_status  ON demandas(status);
CREATE INDEX IF NOT EXISTS idx_dem_empresa ON demandas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_dem_prazo   ON demandas(prazo);
CREATE INDEX IF NOT EXISTS idx_emp_nome    ON empresas(nome);

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
    tipo            TEXT NOT NULL,  -- 'amostradores', 'medicoes', 'planner'
    arquivo_nome    TEXT,
    registros_novos INTEGER DEFAULT 0,
    registros_atu   INTEGER DEFAULT 0,
    usuario         TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Planilhas Digitais de Campo ────────────────────────────────────────

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
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
CREATE INDEX IF NOT EXISTS idx_col_ruido_empresa ON coletas_ruido(empresa_id);
CREATE INDEX IF NOT EXISTS idx_col_ruido_status  ON coletas_ruido(status);

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
CREATE INDEX IF NOT EXISTS idx_col_ruido_func ON coletas_ruido_func(coleta_id);

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
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
CREATE INDEX IF NOT EXISTS idx_col_quim_empresa ON coletas_quimico(empresa_id);
CREATE INDEX IF NOT EXISTS idx_col_quim_status  ON coletas_quimico(status);

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
CREATE INDEX IF NOT EXISTS idx_col_quim_amostr ON coletas_quimico_amostr(coleta_id);

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
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
CREATE INDEX IF NOT EXISTS idx_col_outros_tipo    ON coletas_outros(tipo);
CREATE INDEX IF NOT EXISTS idx_col_outros_empresa ON coletas_outros(empresa_id);

-- ── Microsoft Graph / Planner ──────────────────────────────────────────
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

-- ── Pipeline Planner ────────────────────────────────────────────────────
-- Staging: TODOS os tasks brutos do Planner (nunca alimenta frontend)
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
    -- Pipeline state
    sync_status         TEXT DEFAULT 'raw',   -- raw | ignored | processed
    ignored_reason      TEXT,                  -- motivo do ignore (bucket, tipo, etc.)
    synced_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_planner_task ON planner_raw_tasks(planner_task_id);
CREATE INDEX IF NOT EXISTS idx_raw_bucket        ON planner_raw_tasks(planner_bucket);
CREATE INDEX IF NOT EXISTS idx_raw_sync_status   ON planner_raw_tasks(sync_status);
CREATE INDEX IF NOT EXISTS idx_raw_synced_at     ON planner_raw_tasks(synced_at);

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
CREATE INDEX IF NOT EXISTS idx_eventos_tipo     ON eventos(tipo);
CREATE INDEX IF NOT EXISTS idx_eventos_ref      ON eventos(ref_id, ref_tipo);
CREATE INDEX IF NOT EXISTS idx_eventos_criado   ON eventos(criado_em);

-- ── Planejamento de Medição ────────────────────────────────────────────
-- Criado ANTES da visita. Representa a intenção operacional do técnico.
-- Dados vêm automaticamente do Planner/OS + confirmação do técnico.
CREATE TABLE IF NOT EXISTS planejamentos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id           INTEGER,
    empresa_id           INTEGER NOT NULL,
    numero_os            TEXT,
    tecnico              TEXT NOT NULL,
    data_prevista        TEXT,               -- previsão da visita
    agentes_previstos    TEXT,               -- JSON: [{tipo,agente,qtd,metodo}]
    qtd_dosim_prevista   INTEGER DEFAULT 0,  -- dosímetros necessários (calculado)
    qtd_bombas_previstas INTEGER DEFAULT 0,  -- bombas necessárias (calculado)
    equipamentos_json    TEXT,               -- lista sugerida de equipamentos
    observacao           TEXT,
    status               TEXT DEFAULT 'rascunho',
    -- rascunho | confirmado | em_execucao | concluido | cancelado
    criado_em            TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em        TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
CREATE INDEX IF NOT EXISTS idx_plan_demanda  ON planejamentos(demanda_id);
CREATE INDEX IF NOT EXISTS idx_plan_tecnico  ON planejamentos(tecnico);
CREATE INDEX IF NOT EXISTS idx_plan_status   ON planejamentos(status);
CREATE INDEX IF NOT EXISTS idx_plan_data     ON planejamentos(data_prevista);

-- ── Visita Técnica ─────────────────────────────────────────────────────
-- Uma OS pode gerar várias visitas. Uma visita pode ser parcial.
CREATE TABLE IF NOT EXISTS visitas_tecnicas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    planejamento_id INTEGER,               -- FK para o planejamento que originou
    demanda_id      INTEGER,
    empresa_id      INTEGER,
    tecnico         TEXT NOT NULL,
    data_visita     TEXT NOT NULL,
    hora_inicio     TEXT,
    hora_termino    TEXT,
    tipo_visita     TEXT DEFAULT 'medicao', -- medicao | acompanhamento | retrabalho | outro
    resultado       TEXT DEFAULT 'pendente',-- pendente | concluido | parcial | cancelado
    retrabalho      INTEGER DEFAULT 0,      -- 1 se for revisita
    justificativa   TEXT,                   -- motivo de não-conclusão
    observacao_geral TEXT,
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id),
    FOREIGN KEY (demanda_id) REFERENCES demandas(id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
CREATE INDEX IF NOT EXISTS idx_visita_tecnico      ON visitas_tecnicas(tecnico);
CREATE INDEX IF NOT EXISTS idx_visita_demanda      ON visitas_tecnicas(demanda_id);
CREATE INDEX IF NOT EXISTS idx_visita_data         ON visitas_tecnicas(data_visita);
CREATE INDEX IF NOT EXISTS idx_visita_planejamento ON visitas_tecnicas(planejamento_id);

-- ── Execução em Campo ──────────────────────────────────────────────────
-- Registra o delta: o que foi planejado vs o que foi executado de verdade.
-- Alimenta BI, produtividade, financeiro (cobrança de revisita).
CREATE TABLE IF NOT EXISTS execucao_campo (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    visita_id              INTEGER NOT NULL,
    planejamento_id        INTEGER,
    agentes_executados     TEXT,   -- JSON [{tipo,agente,qtd_feita}]
    agentes_nao_executados TEXT,   -- JSON [{tipo,agente,qtd_prevista}]
    agentes_adicionados    TEXT,   -- JSON [{tipo,agente,qtd}] — novos não planejados
    justificativa_causa    TEXT,   -- cliente | funcionario_ausente | setor_interditado
                                   -- clima | equipamento | tempo | tecnico | outro
    cobravel               INTEGER DEFAULT 0,  -- 1 = próxima visita pode ser cobrada
    observacao             TEXT,
    criado_em              TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (visita_id)       REFERENCES visitas_tecnicas(id),
    FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id)
);
CREATE INDEX IF NOT EXISTS idx_exec_visita       ON execucao_campo(visita_id);
CREATE INDEX IF NOT EXISTS idx_exec_planejamento ON execucao_campo(planejamento_id);

CREATE TABLE IF NOT EXISTS metricas_operacionais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id      INTEGER,
    tecnico         TEXT,
    lead_time_dias  INTEGER,   -- criado_em_ms → concluido_em_ms
    delay_dias      INTEGER,   -- prazo → concluido_em_ms (negativo = adiantado)
    retrabalho      INTEGER DEFAULT 0,
    visitas_total   INTEGER DEFAULT 0,
    calculado_em    TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (demanda_id) REFERENCES demandas(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_metricas_demanda ON metricas_operacionais(demanda_id);
"""


def registrar_sync(tipo, arquivo_nome, novos=0, atualizados=0, usuario='Matheus'):
    """Registra uma sincronizacao no log."""
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


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=10000')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


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


def init_db():
    """Cria tabelas se nao existirem. Idempotente.
    Se o banco estiver vazio, faz auto-seed importando as planilhas
    em controle/seed/ (garante que dados sobrevivem a redeploy).
    """
    is_new = not os.path.exists(DB_PATH)
    with get_db() as conn:
        conn.executescript(SCHEMA)
        # Migration: adicionar colunas novas se nao existirem
        _migrate(conn)
        # Verificar se ja tem dados
        count = conn.execute('SELECT COUNT(*) c FROM amostradores').fetchone()['c']
    if count == 0:
        _auto_seed()


def _migrate(conn):
    """Adiciona colunas novas a tabelas existentes (idempotente)."""
    # ── demandas ──
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(demandas)').fetchall()]
    novas_demandas = {
        'nome_tarefa':     'TEXT',
        'data_conclusao':  'TEXT',
        'responsavel':     'TEXT',
        'status_planner':  'TEXT',
        'progresso':       'INTEGER DEFAULT 0',
        'checklist':       'TEXT',
        'checklist_prog':  'TEXT',
        'bucket':          'TEXT',
        'etiquetas':       'TEXT',
        'descricao':       'TEXT',
        'cnpj':            'TEXT',
        'tem_comentarios': 'INTEGER DEFAULT 0',
        'contato_feito':       'INTEGER DEFAULT 0',
        'contato_feito_em':    'TEXT',
        'contato_feito_por':   'TEXT',
    }
    for col, tipo in novas_demandas.items():
        if col not in cols:
            try:
                conn.execute(f'ALTER TABLE demandas ADD COLUMN {col} {tipo}')
            except Exception as e:
                print(f'[migrate] demandas.{col}: {e}')

    # ── coletas_quimico_amostr: bomba por amostrador ──
    cols_cqa = [r['name'] for r in conn.execute('PRAGMA table_info(coletas_quimico_amostr)').fetchall()]
    if 'bomba' not in cols_cqa:
        try:
            conn.execute('ALTER TABLE coletas_quimico_amostr ADD COLUMN bomba TEXT')
        except Exception as e:
            print(f'[migrate] coletas_quimico_amostr.bomba: {e}')

    # ── coletas_ruido: novos campos (calibrador, unidade, cidade, resp_empresa, os) ──
    cols_cr = [r['name'] for r in conn.execute('PRAGMA table_info(coletas_ruido)').fetchall()]
    for col_name, col_def in [('calibrador','TEXT'), ('unidade','TEXT'), ('cidade','TEXT'),
                               ('resp_empresa','TEXT'), ('os','TEXT')]:
        if col_name not in cols_cr:
            try:
                conn.execute(f'ALTER TABLE coletas_ruido ADD COLUMN {col_name} {col_def}')
            except Exception as e:
                print(f'[migrate] coletas_ruido.{col_name}: {e}')

    # ── amostradores: controle de vencimento no laboratorio ──
    cols_am = [r['name'] for r in conn.execute('PRAGMA table_info(amostradores)').fetchall()]
    novas_amostr = {
        'data_envio_lab':    'TEXT',
        'dias_validade':     'INTEGER DEFAULT 45',
        'lote':              'TEXT',
        'observacao_venc':   'TEXT',
        # Certificados de calibração
        'cert_numero':       'TEXT',   # número do certificado
        'cert_validade':     'TEXT',   # data de validade (YYYY-MM-DD)
        'cert_laboratorio':  'TEXT',   # laboratório emissor
        'cert_arquivo':      'TEXT',   # caminho/URL do arquivo (futuro)
    }
    for col, tipo in novas_amostr.items():
        if col not in cols_am:
            try:
                conn.execute(f'ALTER TABLE amostradores ADD COLUMN {col} {tipo}')
            except Exception as e:
                print(f'[migrate] amostradores.{col}: {e}')

    # ── empresas: flag pendente (importadas do Planner sem match) ────
    cols_emp = [r['name'] for r in conn.execute('PRAGMA table_info(empresas)').fetchall()]
    if 'pendente' not in cols_emp:
        try:
            conn.execute('ALTER TABLE empresas ADD COLUMN pendente INTEGER DEFAULT 0')
        except Exception as e:
            print(f'[migrate] empresas.pendente: {e}')

    # ── demandas: colunas Microsoft Graph / Planner ──────────────────
    cols_dem = [r['name'] for r in conn.execute('PRAGMA table_info(demandas)').fetchall()]
    novas_demandas_graph = {
        'planner_task_id':    'TEXT',   # UNIQUE via índice abaixo (SQLite não suporta ADD COLUMN UNIQUE)
        'planner_plan_id':    'TEXT',
        'planner_plan_nome':  'TEXT',
        'planner_bucket_id':  'TEXT',
        'planner_bucket':     'TEXT',
        'planner_group_id':   'TEXT',
        'planner_group_nome': 'TEXT',
        'titulo':             'TEXT',
        'prioridade':         'TEXT DEFAULT \'media\'',
        'percent_complete':   'INTEGER DEFAULT 0',
        'criado_em_ms':       'TEXT',
        'concluido_em_ms':    'TEXT',
        'ms_assignee_id':     'TEXT',
        'ms_assignees_json':  'TEXT',
        'etiquetas_json':     'TEXT',
        'origem':                  'TEXT DEFAULT \'manual\'',
        'atualizado_em':           'TEXT DEFAULT CURRENT_TIMESTAMP',
        # Matching empresa
        'empresa_match_score':     'REAL',
        'empresa_match_metodo':    'TEXT',
        # Classificação da demanda
        'tipo_demanda':            "TEXT DEFAULT 'operacional'",
    }
    for col, tipo in novas_demandas_graph.items():
        if col not in cols_dem:
            try:
                conn.execute(f'ALTER TABLE demandas ADD COLUMN {col} {tipo}')
            except Exception as e:
                print(f'[migrate] demandas.{col}: {e}')

    # Índice único Planner task id — parcial (ignora NULLs), idempotente
    try:
        conn.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_dem_planner_task
            ON demandas(planner_task_id)
            WHERE planner_task_id IS NOT NULL
        ''')
    except Exception:
        pass

    # ── planner_raw_tasks (staging) ──────────────────────────────────
    # Criada via SCHEMA, mas garante migração se banco pré-existente
    raw_cols_needed = {
        'planner_plan_id':    'TEXT',
        'planner_plan_nome':  'TEXT',
        'planner_bucket_id':  'TEXT',
        'planner_bucket':     'TEXT',
        'planner_group_id':   'TEXT',
        'planner_group_nome': 'TEXT',
        'titulo':             'TEXT',
        'descricao':          'TEXT',
        'checklist_json':     'TEXT',
        'raw_json':           'TEXT',
        'percent_complete':   'INTEGER DEFAULT 0',
        'prazo':              'TEXT',
        'criado_em_ms':       'TEXT',
        'concluido_em_ms':    'TEXT',
        'ms_assignee_id':     'TEXT',
        'ms_assignees_json':  'TEXT',
        'etiquetas_json':     'TEXT',
        'sync_status':        "TEXT DEFAULT 'raw'",
        'ignored_reason':     'TEXT',
        'synced_at':          'TEXT DEFAULT CURRENT_TIMESTAMP',
        'processed_at':       'TEXT',
    }
    try:
        existing_raw = [r['name'] for r in conn.execute('PRAGMA table_info(planner_raw_tasks)').fetchall()]
        for col, tipo in raw_cols_needed.items():
            if col not in existing_raw:
                try:
                    conn.execute(f'ALTER TABLE planner_raw_tasks ADD COLUMN {col} {tipo}')
                except Exception:
                    pass
    except Exception:
        pass  # tabela não existe ainda — SCHEMA vai criá-la

    # ── visitas_tecnicas: colunas novas (planejamento_id, resultado, atualizado_em) ──
    cols_vt = [r['name'] for r in conn.execute('PRAGMA table_info(visitas_tecnicas)').fetchall()]
    novas_vt = {
        'planejamento_id':  'INTEGER',
        'resultado':        "TEXT DEFAULT 'pendente'",
        'observacao_geral': 'TEXT',
        'atualizado_em':    'TEXT DEFAULT CURRENT_TIMESTAMP',
    }
    for col, tipo in novas_vt.items():
        if col not in cols_vt:
            try:
                conn.execute(f'ALTER TABLE visitas_tecnicas ADD COLUMN {col} {tipo}')
            except Exception as e:
                print(f'[migrate] visitas_tecnicas.{col}: {e}')

    # ── coletas_*: adicionar visita_id e planejamento_id ────────────
    for tbl in ('coletas_ruido', 'coletas_quimico', 'coletas_outros'):
        try:
            cols_c = [r['name'] for r in conn.execute(f'PRAGMA table_info({tbl})').fetchall()]
            for col in ('visita_id', 'planejamento_id'):
                if col not in cols_c:
                    conn.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} INTEGER')
        except Exception as e:
            print(f'[migrate] {tbl} visita_id/planejamento_id: {e}')

    # ── Reclassifica demandas existentes pelo bucket operacional ─────
    # Corrige registros que foram inseridos antes da regra bucket→status.
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

    # ── VIEW operational_demands ─────────────────────────────────────
    try:
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

    amostr_path = os.path.join(seed_dir, 'amostradores.xlsx')
    med_path    = os.path.join(seed_dir, 'medicoes.xlsx')

    if os.path.exists(amostr_path):
        try:
            with open(amostr_path, 'rb') as f:
                res = importar_amostradores(f.read())
            print(f'[controle] seed amostradores: {res}')
        except Exception as e:
            print(f'[controle] seed amostradores erro: {e}')

    if os.path.exists(med_path):
        try:
            with open(med_path, 'rb') as f:
                res = importar_medicoes(f.read())
            print(f'[controle] seed medicoes: {res}')
        except Exception as e:
            print(f'[controle] seed medicoes erro: {e}')


# ── Helpers de CRUD comuns ────────────────────────────────────────────

def row_to_dict(row):
    return dict(row) if row else None


def list_amostradores(filtros=None):
    sql = """
        SELECT a.*, e.nome AS empresa_nome,
               CAST(julianday('now') - julianday(
                 COALESCE(NULLIF(a.data_medicao,''), NULLIF(a.data_entrada,''), a.atualizado_em)
               ) AS INTEGER) AS tempo_parado
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


def list_demandas(filtros=None):
    sql = """
        SELECT d.*, e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
               (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id = d.id) AS total_medicoes,
               (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id = d.id AND m.status='realizado') AS realizadas,
               (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id = d.id AND m.status='pendente') AS pendentes,
               (SELECT GROUP_CONCAT(m.agente, ' | ') FROM medicoes m WHERE m.demanda_id = d.id AND m.status!='realizado' LIMIT 5) AS agentes_pendentes,
               CAST(julianday('now') - julianday(COALESCE(d.criado_em_ms, d.criado_em)) AS INTEGER) AS dias_aberta,
               CAST(julianday(d.prazo) - julianday('now') AS INTEGER) AS dias_para_prazo,
               (SELECT MAX(b.criado_em) FROM baixas b
                 JOIN medicoes m ON m.id = b.medicao_id WHERE m.demanda_id = d.id) AS ultima_baixa
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
        # Atrasada = prazo vencido e nao concluida
        sql += (" AND d.status != 'concluida'"
                " AND d.prazo IS NOT NULL AND d.prazo != ''"
                " AND julianday(d.prazo) < julianday('now')")
    # Ordenar conforme filtro
    ordem = f.get('ordem', 'prazo')
    if ordem == 'empresa':
        sql += """ ORDER BY e.nome ASC, d.criado_em ASC LIMIT 2000"""
    elif ordem == 'data_criacao':
        sql += """ ORDER BY d.criado_em ASC LIMIT 2000"""
    else:  # prazo (padrão)
        sql += """ ORDER BY
            CASE WHEN d.status='concluida' THEN 1 ELSE 0 END,
            CASE WHEN d.prazo IS NULL OR d.prazo = '' THEN 1 ELSE 0 END,
            d.prazo ASC,
            d.criado_em ASC LIMIT 2000"""
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def list_demandas_por_empresa(filtros=None):
    """Agrupa demandas por empresa com progresso total.
    Usa JOIN direto (sem subqueries correlacionadas) para performance maxima.
    """
    sql = """
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
               SUM(CASE WHEN d.status!='concluida'
                          AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%entregue%'
                          AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%conclu%'
                          AND d.prazo IS NOT NULL AND d.prazo != ''
                          AND julianday(d.prazo) < julianday('now')
                   THEN 1 ELSE 0 END) AS demandas_atrasadas,
               GROUP_CONCAT(DISTINCT d.numero_os) AS numeros_os
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
        'nome':  'empresa_nome ASC',
        'data':  'demanda_mais_antiga ASC',
        'pend':  'demandas_pendentes DESC, demanda_mais_antiga ASC',
    }.get(ordem, 'empresa_nome ASC')
    sql += f" GROUP BY e.id, e.nome ORDER BY {order_clause} LIMIT 500"
    with get_db() as conn:
        rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            if r.get('demanda_mais_antiga'):
                from datetime import datetime as _dt
                try:
                    dt = _dt.fromisoformat(r['demanda_mais_antiga'].replace(' ', 'T').split('.')[0])
                    r['dias_aberta'] = (_dt.now() - dt).days
                except:
                    r['dias_aberta'] = 0
            else:
                r['dias_aberta'] = 0
        return rows


def mesclar_empresas_duplicatas():
    """Consolida empresas com mesmo nome (case-insensitive) em uma só.
    Mantém o id menor e redireciona todas as demandas/amostradores.
    Retorna quantas foram mescladas.
    """
    mescladas = 0
    with get_db() as conn:
        # Grupos de empresas com mesmo nome
        grupos = conn.execute("""
            SELECT LOWER(TRIM(nome)) AS nome_key, MIN(id) AS id_principal,
                   COUNT(*) AS qtd
            FROM empresas
            GROUP BY LOWER(TRIM(nome))
            HAVING COUNT(*) > 1
        """).fetchall()

        for g in grupos:
            id_princ = g['id_principal']
            # Buscar ids duplicados (todos menos o principal)
            dups = [r['id'] for r in conn.execute(
                "SELECT id FROM empresas WHERE LOWER(TRIM(nome)) = ? AND id != ?",
                (g['nome_key'], id_princ)).fetchall()]
            for dup_id in dups:
                conn.execute("UPDATE demandas SET empresa_id=? WHERE empresa_id=?",
                             (id_princ, dup_id))
                conn.execute("UPDATE amostradores SET empresa_id=? WHERE empresa_id=?",
                             (id_princ, dup_id))
                conn.execute("DELETE FROM empresas WHERE id=?", (dup_id,))
                mescladas += 1
    return mescladas


def get_empresa_demandas(empresa_id):
    """Retorna empresa + TODAS as demandas de empresas com o mesmo nome (consolida duplicatas).
    Isso resolve o caso em que importações diferentes criaram registros duplicados."""
    with get_db() as conn:
        emp = conn.execute('SELECT * FROM empresas WHERE id=?', (empresa_id,)).fetchone()
        if not emp: return None
        emp = row_to_dict(emp)
        # Buscar todos os IDs de empresas com o mesmo nome (case-insensitive)
        nome_key = (emp.get('nome') or '').strip().lower()
        ids_iguais = [r['id'] for r in conn.execute(
            "SELECT id FROM empresas WHERE LOWER(TRIM(nome))=?", (nome_key,)).fetchall()]
        if not ids_iguais:
            ids_iguais = [empresa_id]
        ph = ','.join('?' * len(ids_iguais))
        dems = [row_to_dict(r) for r in conn.execute(f"""
            SELECT d.*,
                   e.nome AS empresa_nome,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id) AS total_medicoes,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id AND m.status='realizado') AS realizadas,
                   CAST(julianday('now') - julianday(d.criado_em) AS INTEGER) AS dias_aberta,
                   COALESCE(d.responsavel, u.display_name) AS responsavel_efetivo,
                   COALESCE(d.nome_tarefa, d.titulo) AS tarefa_display
            FROM demandas d
            JOIN empresas e ON e.id = d.empresa_id
            LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
            WHERE d.empresa_id IN ({ph})
            ORDER BY d.status ASC, d.criado_em DESC
        """, ids_iguais).fetchall()]
        # Incluir medicoes pendentes de cada demanda
        for d in dems:
            d['medicoes_pendentes'] = [row_to_dict(r) for r in conn.execute(
                "SELECT id, agente, tipo_amostrador, qtd_pontos_feita, qtd_pontos_prevista FROM medicoes "
                "WHERE demanda_id=? AND status!='realizado' ORDER BY agente",
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
        if not d: return None
        d = row_to_dict(d)
        meds = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM medicoes WHERE demanda_id = ? ORDER BY id',
            (demanda_id,)).fetchall()]
        # Incluir baixas (com vazao) de cada medicao
        for m in meds:
            m['baixas'] = [row_to_dict(r) for r in conn.execute("""
                SELECT b.avaliador, b.bomba, b.vazao_calibrada,
                       b.volume_recomendado, b.tempo_calculado_min,
                       b.tempo_calculado_max, b.data_medicao
                FROM baixas b WHERE b.medicao_id = ?
                ORDER BY b.id DESC""", (m['id'],)).fetchall()]
        d['medicoes'] = meds
        return d


def upsert_raw_task(conn, task_id: str, data: dict) -> tuple:
    """
    Insere ou atualiza task bruta no staging planner_raw_tasks.
    Retorna (id, 'created'|'updated').
    """
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
            data.get('etiquetas_json'),
            task_id,
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
    """Atualiza status de pipeline do raw task."""
    if status == 'ignored':
        conn.execute(
            "UPDATE planner_raw_tasks SET sync_status='ignored', ignored_reason=? WHERE id=?",
            (ignored_reason, raw_id)
        )
    elif status == 'processed':
        conn.execute(
            "UPDATE planner_raw_tasks SET sync_status='processed', "
            "ignored_reason=NULL, processed_at=CURRENT_TIMESTAMP WHERE id=?",
            (raw_id,)
        )


def save_coleta_outros(data):
    """Salva coleta genérica (calor, vibração, etc.) em coletas_outros."""
    import json as _json
    cid = data.get('id')
    campos = ['tipo','empresa_id','empresa_nome','demanda_id','numero_os',
              'avaliador','data_coleta','acompanhante','hora_inicio','hora_termino',
              'unidade','cidade','observacao','status']
    vals = {c: data.get(c) for c in campos if c != 'dados_json'}
    # Salva campos extras como JSON
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


# ── Planejamentos ────────────────────────────────────────────────────

def criar_planejamento(data: dict) -> int:
    """Cria um planejamento de medição. Retorna o id criado."""
    import json as _json
    campos = [
        'demanda_id', 'empresa_id', 'numero_os', 'tecnico', 'data_prevista',
        'agentes_previstos', 'qtd_dosim_prevista', 'qtd_bombas_previstas',
        'equipamentos_json', 'observacao', 'status',
    ]
    vals = {}
    for c in campos:
        v = data.get(c)
        if isinstance(v, (dict, list)):
            v = _json.dumps(v, ensure_ascii=False)
        vals[c] = v
    vals.setdefault('status', 'rascunho')
    cols = ', '.join(vals.keys())
    phs  = ', '.join(['?'] * len(vals))
    with get_db() as conn:
        cur = conn.execute(
            f'INSERT INTO planejamentos ({cols}) VALUES ({phs})',
            list(vals.values())
        )
        return cur.lastrowid


def get_planejamento(pid: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute('SELECT * FROM planejamentos WHERE id=?', (pid,)).fetchone()
        return row_to_dict(r)


def list_planejamentos(filtros=None) -> list:
    f = filtros or {}
    sql = '''
        SELECT p.*, e.nome AS empresa_nome,
               d.titulo AS demanda_titulo
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
            (status, pid)
        )
        return True


# ── Visitas Técnicas ──────────────────────────────────────────────────

def criar_visita(data: dict) -> int:
    """Cria registro de visita técnica. Retorna o id criado."""
    campos = [
        'planejamento_id', 'demanda_id', 'empresa_id', 'tecnico',
        'data_visita', 'hora_inicio', 'hora_termino',
        'tipo_visita', 'resultado', 'retrabalho', 'justificativa', 'observacao_geral',
    ]
    vals = {c: data.get(c) for c in campos if data.get(c) is not None}
    vals.setdefault('tipo_visita', 'medicao')
    vals.setdefault('resultado', 'pendente')
    cols = ', '.join(vals.keys())
    phs  = ', '.join(['?'] * len(vals))
    with get_db() as conn:
        cur = conn.execute(
            f'INSERT INTO visitas_tecnicas ({cols}) VALUES ({phs})',
            list(vals.values())
        )
        # Atualizar status do planejamento para em_execucao
        pid = data.get('planejamento_id')
        if pid:
            conn.execute(
                "UPDATE planejamentos SET status='em_execucao', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                (pid,)
            )
        return cur.lastrowid


def get_visita(vid: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute('''
            SELECT v.*,
                   e.nome AS empresa_nome,
                   p.numero_os, p.agentes_previstos, p.data_prevista
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
    """
    Finaliza uma visita: atualiza resultado, cria registro execucao_campo.
    data: {resultado, justificativa, agentes_executados, agentes_nao_executados,
           agentes_adicionados, justificativa_causa, cobravel, observacao}
    """
    import json as _json
    resultado = data.get('resultado', 'concluido')
    with get_db() as conn:
        conn.execute('''
            UPDATE visitas_tecnicas
            SET resultado=?, justificativa=?, hora_termino=COALESCE(hora_termino,?),
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (resultado, data.get('justificativa'), data.get('hora_termino'), vid))

        # Buscar planejamento_id da visita
        row = conn.execute(
            'SELECT planejamento_id FROM visitas_tecnicas WHERE id=?', (vid,)
        ).fetchone()
        plan_id = row['planejamento_id'] if row else None

        # Criar execucao_campo
        def _j(v):
            return _json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v

        conn.execute('''
            INSERT INTO execucao_campo
                (visita_id, planejamento_id, agentes_executados, agentes_nao_executados,
                 agentes_adicionados, justificativa_causa, cobravel, observacao)
            VALUES (?,?,?,?,?,?,?,?)
        ''', (
            vid, plan_id,
            _j(data.get('agentes_executados')),
            _j(data.get('agentes_nao_executados')),
            _j(data.get('agentes_adicionados')),
            data.get('justificativa_causa'),
            int(data.get('cobravel', 0)),
            data.get('observacao'),
        ))

        # Marcar planejamento como concluido se resultado = concluido
        if plan_id and resultado == 'concluido':
            conn.execute(
                "UPDATE planejamentos SET status='concluido', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                (plan_id,)
            )
    return True


def list_raw_tasks(filtros=None, limit=200):
    """Lista raw tasks com filtros: status, bucket, grupo."""
    f = filtros or {}
    sql = 'SELECT * FROM planner_raw_tasks WHERE 1=1'
    params = []
    if f.get('status'):
        sql += ' AND sync_status=?'; params.append(f['status'])
    if f.get('bucket'):
        sql += ' AND LOWER(planner_bucket) LIKE LOWER(?)'; params.append(f'%{f["bucket"]}%')
    if f.get('grupo'):
        sql += ' AND LOWER(planner_group_nome) LIKE LOWER(?)'; params.append(f'%{f["grupo"]}%')
    sql += f' ORDER BY synced_at DESC LIMIT {int(limit)}'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def stats_raw_pipeline():
    """Contagem por status do pipeline de raw tasks."""
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


def list_operational_demands(filtros=None):
    """Lê demandas operacionais — JOIN único em vez de N+1 subqueries."""
    f = filtros or {}
    try:
        limit = min(int(f.get('limit', 200)), 1000)
    except (ValueError, TypeError):
        limit = 200
    sql = """
        SELECT d.*,
               e.nome AS empresa_nome,
               e.cnpj AS empresa_cnpj,
               e.pendente AS empresa_pendente,
               COALESCE(u.display_name, d.responsavel) AS responsavel_nome,
               COALESCE(mm.total_medicoes, 0) AS total_medicoes,
               COALESCE(mm.realizadas, 0) AS realizadas,
               CAST(julianday('now') - julianday(COALESCE(d.criado_em_ms, d.criado_em)) AS INTEGER) AS dias_aberta,
               CAST(julianday(d.prazo) - julianday('now') AS INTEGER) AS dias_para_prazo,
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
    """Agrupa operational_demands por empresa para a tela principal."""
    sql = """
        SELECT e.id AS empresa_id,
               e.nome AS empresa_nome,
               e.cnpj,
               e.pendente AS empresa_pendente,
               COUNT(DISTINCT d.id) AS total_demandas,
               COUNT(DISTINCT CASE WHEN d.status='concluida' THEN d.id END) AS demandas_concluidas,
               COUNT(DISTINCT CASE WHEN d.status!='concluida' THEN d.id END) AS demandas_pendentes,
               ROUND(COUNT(DISTINCT CASE WHEN d.status='concluida' THEN d.id END) * 100.0
                     / NULLIF(COUNT(DISTINCT d.id), 0), 1) AS progresso_medio,
               GROUP_CONCAT(DISTINCT NULLIF(d.numero_os,'')) AS numeros_os,
               MIN(CASE
                     WHEN d.status != 'concluida'
                       AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%entregue%'
                       AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%conclu%'
                     THEN NULLIF(d.prazo,'')
                   END) AS prazo_mais_proximo,
               COALESCE(MAX(u.display_name), MAX(d.responsavel)) AS responsavel,
               COUNT(DISTINCT d.tipo_demanda) AS tipos_count,
               GROUP_CONCAT(DISTINCT d.tipo_demanda) AS tipos,
               SUM(CASE WHEN d.status!='concluida'
                          AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%entregue%'
                          AND LOWER(COALESCE(d.planner_bucket,'')) NOT LIKE '%conclu%'
                          AND d.prazo IS NOT NULL AND d.prazo != ''
                          AND julianday(d.prazo) < julianday('now')
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
                # Filtra nulls e duplicatas
                os_list = [x.strip() for x in (r['numeros_os'] or '').split(',') if x.strip()]
                r['numeros_os'] = ','.join(dict.fromkeys(os_list))
        return rows


def upsert_empresa(cnpj, nome, **extra):
    """Insere ou atualiza empresa por CNPJ (ou nome quando sem CNPJ). Retorna id."""
    cnpj = (cnpj or '').strip()
    nome = (nome or '').strip()
    if not nome: return None
    with get_db() as conn:
        if cnpj:
            r = conn.execute('SELECT id FROM empresas WHERE cnpj = ?', (cnpj,)).fetchone()
            if r:
                return r['id']
        # Sem CNPJ (ou CNPJ não encontrado): busca por nome case-insensitive
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


def list_amostradores_vencendo(dias_alerta=7):
    """Lista amostradores com data_envio_lab definida, calculando dias para vencer.
    Retorna ordenados pelo mais urgente.
    """
    sql = """
        SELECT a.*, e.nome AS empresa_nome,
               CAST(julianday(a.data_envio_lab) + COALESCE(a.dias_validade,45)
                    - julianday('now') AS INTEGER) AS dias_para_vencer,
               CAST(julianday('now') - julianday(a.data_envio_lab) AS INTEGER) AS dias_no_lab
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
    """Conta amostradores em 4 faixas: vencido, urgente (<=3d), alerta (<=7d), ok."""
    sql = """
        SELECT
          SUM(CASE WHEN julianday('now') > julianday(data_envio_lab) + COALESCE(dias_validade,45) THEN 1 ELSE 0 END) AS vencidos,
          SUM(CASE WHEN julianday('now') BETWEEN julianday(data_envio_lab) + COALESCE(dias_validade,45) - 3
                                AND julianday(data_envio_lab) + COALESCE(dias_validade,45) THEN 1 ELSE 0 END) AS urgente,
          SUM(CASE WHEN julianday('now') BETWEEN julianday(data_envio_lab) + COALESCE(dias_validade,45) - 7
                                AND julianday(data_envio_lab) + COALESCE(dias_validade,45) - 4 THEN 1 ELSE 0 END) AS alerta,
          COUNT(*) AS total_no_lab
        FROM amostradores
        WHERE data_envio_lab IS NOT NULL AND data_envio_lab != ''
          AND status != 'Devolvido'
    """
    with get_db() as conn:
        r = conn.execute(sql).fetchone()
        return {
            'vencidos': r['vencidos'] or 0,
            'urgente':  r['urgente']  or 0,
            'alerta':   r['alerta']   or 0,
            'total_no_lab': r['total_no_lab'] or 0,
        }


def stats_dashboard():
    """Estatisticas rapidas para a tela principal — consulta unica otimizada."""
    with get_db() as conn:
        r = conn.execute("""
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
              (SELECT COUNT(*) FROM amostradores
               WHERE data_envio_lab IS NOT NULL AND data_envio_lab != '' AND status != 'Devolvido') AS venc_total_no_lab,
              (SELECT COUNT(*) FROM amostradores
               WHERE data_envio_lab IS NOT NULL AND data_envio_lab != '' AND status != 'Devolvido'
                 AND julianday('now') > julianday(data_envio_lab) + COALESCE(dias_validade,45)) AS venc_vencidos,
              (SELECT COUNT(*) FROM amostradores
               WHERE data_envio_lab IS NOT NULL AND data_envio_lab != '' AND status != 'Devolvido'
                 AND julianday('now') BETWEEN julianday(data_envio_lab) + COALESCE(dias_validade,45) - 3
                                          AND julianday(data_envio_lab) + COALESCE(dias_validade,45)) AS venc_urgente,
              (SELECT COUNT(*) FROM amostradores
               WHERE data_envio_lab IS NOT NULL AND data_envio_lab != '' AND status != 'Devolvido'
                 AND julianday('now') BETWEEN julianday(data_envio_lab) + COALESCE(dias_validade,45) - 7
                                          AND julianday(data_envio_lab) + COALESCE(dias_validade,45) - 4) AS venc_alerta
        """).fetchone()
        return dict(r)

# ── Coletas de Campo ──────────────────────────────────────────────────

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
        if not c: return None
        c = row_to_dict(c)
        funcs = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM coletas_ruido_func WHERE coleta_id=? ORDER BY seq', (cid,)).fetchall()]
        c['funcionarios']  = funcs
        c['trabalhadores'] = funcs   # alias para compatibilidade com wizard/relatório
        return c


def save_coleta_ruido(data):
    cid = data.get('id')
    campos = ['empresa_id','empresa_nome','demanda_id','acompanhante',
              'cargo_acompanhante','tecnico','data_coleta','hora_inicio',
              'hora_termino','calibrador','calibracao_inicial','calibracao_final',
              'desvio_calibracao','status_calibracao','unidade','cidade',
              'resp_empresa','os','observacao','status']
    vals = {c: data.get(c) for c in campos}
    ci = vals.get('calibracao_inicial')
    cf = vals.get('calibracao_final')
    if ci is not None and cf is not None:
        try:
            ci_f = float(str(ci).replace(',','.'))
            cf_f = float(str(cf).replace(',','.'))
            desvio = round(cf_f - ci_f, 2)
            vals['desvio_calibracao'] = desvio
            vals['status_calibracao'] = 'divergente' if abs(desvio) > 0.5 else 'conforme'
        except: pass
    with get_db() as conn:
        if cid:
            sets = ', '.join(k + '=?' for k in vals) + ', atualizado_em=CURRENT_TIMESTAMP'
            conn.execute('UPDATE coletas_ruido SET ' + sets + ' WHERE id=?', list(vals.values()) + [cid])
        else:
            cols = ', '.join(vals.keys())
            phs  = ', '.join(['?'] * len(vals))
            cur  = conn.execute('INSERT INTO coletas_ruido (' + cols + ') VALUES (' + phs + ')', list(vals.values()))
            cid  = cur.lastrowid
        # Aceita tanto 'funcionarios' (interno) quanto 'trabalhadores' (wizard)
        funcs = data.get('trabalhadores') or data.get('funcionarios')
        if funcs is not None:
            conn.execute('DELETE FROM coletas_ruido_func WHERE coleta_id=?', (cid,))
            for i, func in enumerate(funcs, 1):
                conn.execute(
                    'INSERT INTO coletas_ruido_func (coleta_id,seq,nome,cargo,setor,almoco,serie_dosimetro) VALUES (?,?,?,?,?,?,?)',
                    (cid, i, func.get('nome',''), func.get('cargo',''), func.get('setor',''),
                     1 if func.get('almoco') else 0,
                     func.get('serie_dosimetro', func.get('dosimetro',''))))
    return cid


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
        if not c: return None
        c = row_to_dict(c)
        c['amostradores'] = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM coletas_quimico_amostr WHERE coleta_id=? ORDER BY seq', (cid,)).fetchall()]
        return c


def save_coleta_quimico(data):
    cid = data.get('id')
    campos = ['empresa_id','empresa_nome','demanda_id','responsavel_coleta',
              'cidade','unidade','data_coleta','dia_semana','turno',
              'nome_funcionario','jornada','funcao','setor','local_atividade',
              'atividade','ventilacao','ambiente','condicoes_meteo',
              'temperatura','umidade','outras_condicoes','substancias',
              'fracao','tempo_exposto','bomba','id_bomba','data_cal_bomba',
              'id_calibrador','acessorios','epis','epc','observacao','status']
    vals = {c: data.get(c) for c in campos}
    with get_db() as conn:
        if cid:
            sets = ', '.join(k + '=?' for k in vals) + ', atualizado_em=CURRENT_TIMESTAMP'
            conn.execute('UPDATE coletas_quimico SET ' + sets + ' WHERE id=?', list(vals.values()) + [cid])
        else:
            cols = ', '.join(vals.keys())
            phs  = ', '.join(['?'] * len(vals))
            cur  = conn.execute('INSERT INTO coletas_quimico (' + cols + ') VALUES (' + phs + ')', list(vals.values()))
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
                    (cid, i, am.get('id_amostrador',''), am.get('tipo_amostrador',''),
                     am.get('substancia',''), am.get('bomba',''), vi, vf, round(vm,3),
                     am.get('hora_inicio',''), am.get('hora_final',''),
                     am.get('intervalos',''), t, vol, dv,
                     'divergente' if dv > 5 else 'conforme'))
    return cid
