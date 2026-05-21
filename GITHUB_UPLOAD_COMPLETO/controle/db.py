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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(demandas)').fetchall()]
    novas = {
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
    for col, tipo in novas.items():
        if col not in cols:
            try:
                conn.execute(f'ALTER TABLE demandas ADD COLUMN {col} {tipo}')
            except Exception as e:
                print(f'[migrate] {col}: {e}')


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
        SELECT a.*, e.nome AS empresa_nome
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
               CAST(julianday('now') - julianday(d.criado_em) AS INTEGER) AS dias_aberta,
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
        sql += " AND d.status != 'concluida' AND julianday('now') - julianday(d.criado_em) > 7"
    # Ordenar: pendentes mais antigas primeiro
    sql += """ ORDER BY
        CASE WHEN d.status='concluida' THEN 1 ELSE 0 END,
        d.criado_em ASC LIMIT 2000"""
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def list_demandas_por_empresa(filtros=None):
    """Agrupa demandas por empresa com progresso total."""
    sql = """
        SELECT e.id AS empresa_id, e.nome AS empresa_nome, e.cnpj,
               COUNT(d.id) AS total_demandas,
               SUM(CASE WHEN d.status='concluida' THEN 1 ELSE 0 END) AS demandas_concluidas,
               SUM(CASE WHEN d.status!='concluida' THEN 1 ELSE 0 END) AS demandas_pendentes,
               COALESCE(SUM(d.progresso), 0) / NULLIF(COUNT(d.id),0) AS progresso_medio,
               (SELECT COUNT(*) FROM medicoes m JOIN demandas d2 ON d2.id=m.demanda_id
                 WHERE d2.empresa_id = e.id) AS total_medicoes,
               (SELECT COUNT(*) FROM medicoes m JOIN demandas d2 ON d2.id=m.demanda_id
                 WHERE d2.empresa_id = e.id AND m.status='realizado') AS medicoes_realizadas,
               MIN(d.criado_em) AS demanda_mais_antiga,
               MAX(d.prazo) AS prazo_mais_distante,
               MAX(d.responsavel) AS responsavel,
               MAX(d.contato_feito) AS contato_feito
        FROM empresas e
        JOIN demandas d ON d.empresa_id = e.id
        WHERE 1=1
    """
    params = []
    f = filtros or {}
    if f.get('status') == 'pendente':
        sql += " AND d.status != 'concluida'"
    elif f.get('status') == 'concluida':
        sql += " AND d.status = 'concluida'"
    if f.get('empresa'):
        sql += ' AND e.nome LIKE ?'; params.append(f'%{f["empresa"]}%')
    sql += """ GROUP BY e.id, e.nome, e.cnpj
        ORDER BY demandas_pendentes DESC, demanda_mais_antiga ASC
        LIMIT 1000"""
    with get_db() as conn:
        rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        # adicionar dias_aberta da mais antiga
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


def get_empresa_demandas(empresa_id):
    """Retorna empresa + todas suas demandas + medicoes."""
    with get_db() as conn:
        emp = conn.execute('SELECT * FROM empresas WHERE id=?', (empresa_id,)).fetchone()
        if not emp: return None
        emp = row_to_dict(emp)
        dems = [row_to_dict(r) for r in conn.execute("""
            SELECT d.*,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id) AS total_medicoes,
                   (SELECT COUNT(*) FROM medicoes m WHERE m.demanda_id=d.id AND m.status='realizado') AS realizadas,
                   CAST(julianday('now') - julianday(d.criado_em) AS INTEGER) AS dias_aberta
            FROM demandas d WHERE d.empresa_id=?
            ORDER BY d.criado_em DESC
        """, (empresa_id,)).fetchall()]
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
        d['medicoes'] = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM medicoes WHERE demanda_id = ? ORDER BY id',
            (demanda_id,)).fetchall()]
        return d


def upsert_empresa(cnpj, nome, **extra):
    """Insere ou atualiza empresa por CNPJ. Retorna id."""
    cnpj = (cnpj or '').strip()
    nome = (nome or '').strip()
    if not nome: return None
    with get_db() as conn:
        if cnpj:
            r = conn.execute('SELECT id FROM empresas WHERE cnpj = ?', (cnpj,)).fetchone()
            if r:
                return r['id']
        cur = conn.execute(
            'INSERT INTO empresas (cnpj, nome, unidade, contato, telefone, email, cidade, uf) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (cnpj or None, nome, extra.get('unidade'), extra.get('contato'),
             extra.get('telefone'), extra.get('email'), extra.get('cidade'), extra.get('uf'))
        )
        return cur.lastrowid


def stats_dashboard():
    """Estatisticas rapidas para a tela principal."""
    with get_db() as conn:
        stats = {}
        stats['total_amostradores'] = conn.execute(
            'SELECT COUNT(*) AS c FROM amostradores').fetchone()['c']
        stats['estoque'] = conn.execute(
            "SELECT COUNT(*) AS c FROM amostradores WHERE status='Estoque'").fetchone()['c']
        stats['laboratorio'] = conn.execute(
            "SELECT COUNT(*) AS c FROM amostradores WHERE status='Laboratorio'").fetchone()['c']
        stats['reservados'] = conn.execute(
            "SELECT COUNT(*) AS c FROM amostradores WHERE status='Reservado'").fetchone()['c']
        stats['demandas_pendentes'] = conn.execute(
            "SELECT COUNT(*) AS c FROM demandas WHERE status!='concluida'").fetchone()['c']
        stats['demandas_concluidas'] = conn.execute(
            "SELECT COUNT(*) AS c FROM demandas WHERE status='concluida'").fetchone()['c']
        stats['medicoes_pendentes'] = conn.execute(
            "SELECT COUNT(*) AS c FROM medicoes WHERE status='pendente'").fetchone()['c']
        return stats
