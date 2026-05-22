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

    # ── amostradores: controle de vencimento no laboratorio ──
    cols_am = [r['name'] for r in conn.execute('PRAGMA table_info(amostradores)').fetchall()]
    novas_amostr = {
        'data_envio_lab':  'TEXT',    # quando foi enviado pro laboratorio
        'dias_validade':   'INTEGER DEFAULT 45',  # padrao 45 dias antes da cobranca
        'lote':            'TEXT',    # lote/serie do amostrador
        'observacao_venc': 'TEXT',
    }
    for col, tipo in novas_amostr.items():
        if col not in cols_am:
            try:
                conn.execute(f'ALTER TABLE amostradores ADD COLUMN {col} {tipo}')
            except Exception as e:
                print(f'[migrate] amostradores.{col}: {e}')


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
               CAST(julianday('now') - julianday(d.criado_em) AS INTEGER) AS dias_aberta,
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
    Usa LOWER(TRIM(nome)) para consolidar empresas duplicadas importadas com nomes identicos.
    """
    sql = """
        SELECT MIN(e.id) AS empresa_id,
               e.nome AS empresa_nome,
               MAX(NULLIF(e.cnpj,'')) AS cnpj,
               COUNT(d.id) AS total_demandas,
               SUM(CASE WHEN d.status='concluida' THEN 1 ELSE 0 END) AS demandas_concluidas,
               SUM(CASE WHEN d.status!='concluida' THEN 1 ELSE 0 END) AS demandas_pendentes,
               COALESCE(SUM(d.progresso), 0) / NULLIF(COUNT(d.id),0) AS progresso_medio,
               (SELECT COUNT(*) FROM medicoes m JOIN demandas d2 ON d2.id=m.demanda_id
                 WHERE d2.empresa_id IN (
                     SELECT id FROM empresas e2
                     WHERE LOWER(TRIM(e2.nome)) = LOWER(TRIM(e.nome))
                 )) AS total_medicoes,
               (SELECT COUNT(*) FROM medicoes m JOIN demandas d2 ON d2.id=m.demanda_id
                 WHERE d2.empresa_id IN (
                     SELECT id FROM empresas e2
                     WHERE LOWER(TRIM(e2.nome)) = LOWER(TRIM(e.nome))
                 ) AND m.status='realizado') AS medicoes_realizadas,
               MIN(d.criado_em) AS demanda_mais_antiga,
               MIN(NULLIF(d.prazo,'')) AS prazo_mais_proximo,
               MAX(d.responsavel) AS responsavel,
               MAX(d.contato_feito) AS contato_feito,
               SUM(CASE WHEN d.status!='concluida'
                          AND d.prazo IS NOT NULL AND d.prazo != ''
                          AND julianday(d.prazo) < julianday('now')
                   THEN 1 ELSE 0 END) AS demandas_atrasadas
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
        sql += ' AND LOWER(e.nome) LIKE LOWER(?)'; params.append(f'%{f["empresa"]}%')
    ordem = f.get('ordem', 'nome')
    order_clause = {
        'nome':  'empresa_nome ASC',
        'data':  'demanda_mais_antiga ASC',
        'pend':  'demandas_pendentes DESC, demanda_mais_antiga ASC',
    }.get(ordem, 'empresa_nome ASC')
    sql += f" GROUP BY LOWER(TRIM(e.nome)) ORDER BY {order_clause} LIMIT 1000"
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
                   CAST(julianday('now') - julianday(d.criado_em) AS INTEGER) AS dias_aberta
            FROM demandas d
            JOIN empresas e ON e.id = d.empresa_id
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
        stats['empresas_ativas'] = conn.execute(
            "SELECT COUNT(DISTINCT empresa_id) AS c FROM demandas WHERE status!='concluida'").fetchone()['c']
        # Vencimento de amostradores no laboratorio
        venc = contar_vencendo()
        stats.update({
            'venc_vencidos':     venc['vencidos'],
            'venc_urgente':      venc['urgente'],
            'venc_alerta':       venc['alerta'],
            'venc_total_no_lab': venc['total_no_lab'],
        })
        return stats
