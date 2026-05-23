# -*- coding: utf-8 -*-
"""
Motor de matching: vincula título de tarefa Planner → empresa existente.

Hierarquia de tentativas:
  1. CNPJ exato (extraído do título)
  2. Número de OS → demanda anterior já vinculada
  3. Nome fuzzy (SequenceMatcher, threshold configurável)
  4. Se não encontrar → cria empresa "pendente" para validação manual
"""

import re
import unicodedata
import logging
from difflib import SequenceMatcher

log = logging.getLogger(__name__)

# Sufixos societários a ignorar no matching de nome
_SUFIXOS = {
    'ltda', 'sa', 'ssa', 'eireli', 'me', 'epp', 'ss', 'scp',
    'filial', 'matriz', 'holding', 'grupo', 'industria', 'industrias',
    'ind', 'com', 'comercio', 'servicos', 'servico', 'solucoes',
    'engenharia', 'construtora', 'construcoes', 'construcao',
    'associacao', 'fundacao', 'sindicato', 'cooperativa',
}

# Regex para extrair número de OS do título
_RE_OS     = re.compile(r'(?:^|(?:os|o\.s\.?)\s*)(\d{4,8})', re.IGNORECASE)
_RE_CNPJ   = re.compile(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}')

# Padrões de OS no início do título (número puro, sem letras coladas)
_RE_OS_DASH  = re.compile(r'^(\d{4,8})\s*[-–]\s*(.+)', re.DOTALL)   # "6077430 - Empresa"
_RE_OS_COMMA = re.compile(r'^(\d{4,8})\s*,\s*(.+)', re.DOTALL)       # "54394, Empresa, ..."
_RE_OS_SPACE = re.compile(r'^(\d{5,8})\s+([A-Za-zÀ-ÿ].+)', re.DOTALL)  # "6466572 Empresa" (min 5 dig)

# Sufixos de status a remover do nome da empresa
_RE_SUFIXO_DIAS   = re.compile(r',?\s*\d+\s+DIAS?\b.*$', re.IGNORECASE | re.DOTALL)
_RE_SUFIXO_ANTIGO = re.compile(r'[-–,]?\s*PROCESSO\s+ANTIGO.*$', re.IGNORECASE | re.DOTALL)

# Separadores de label: " - " ou "- " com texto maiúsculo/acrônimo antes
_RE_SEP_LABEL = re.compile(r'\s*[-–]\s*')


# ── Normalização ──────────────────────────────────────────────────────

def _strip_prefixo_label(titulo: str) -> str:
    """
    Remove prefixo de tipo/serviço de títulos Planner.
    Handles: "AET/MEDIÇÕES - ", "LTCAT/PCA/PPR/AET/MEDIÇÕES - ", "LIP e MEDIÇÕES- ", "LTCAT/TREINAMENTO / MEDIÇÃO- "
    Heurística: prefixo ≤50 chars com "/" OU 2+ palavras todas-maiúsculas, seguido de " - ".
    """
    t = titulo.strip()
    for sep in [' - ', ' – ', '- ', '– ']:
        idx = t.find(sep)
        if idx <= 0 or idx > 50:
            continue
        prefixo = t[:idx].strip()
        resto = t[idx + len(sep):].strip()
        if not resto:
            continue
        if '/' in prefixo:
            return resto
        # Palavras significativas do prefixo (ignora conectores "e", "ou", "de")
        palavras = [p for p in re.split(r'[\s/]+', prefixo)
                    if len(p) > 1 and p.lower() not in ('e', 'ou', 'de')]
        if len(palavras) >= 2 and all(p == p.upper() for p in palavras):
            return resto
    return t


def _sem_acento(txt: str) -> str:
    return unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode().lower()


def normalizar_nome(nome: str) -> str:
    """Remove acentos, pontuação, sufixos societários para comparação."""
    if not nome:
        return ''
    txt = _sem_acento(nome)
    txt = re.sub(r'[^\w\s]', ' ', txt)          # remove pontuação
    palavras = [p for p in txt.split() if p and p not in _SUFIXOS and len(p) > 1]
    return ' '.join(palavras)


def similaridade(a: str, b: str) -> float:
    na, nb = normalizar_nome(a), normalizar_nome(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


# ── Extração de campos do título ──────────────────────────────────────

def _limpar_nome_empresa(raw: str) -> str:
    """
    Limpa nome extraído de título Planner com formatos variados.
    Remove sufixos de status ("516 DIAS", "PROCESSO ANTIGO"),
    duplicatas separadas por vírgula e separadores finais.
    """
    if not raw:
        return ''
    raw = _RE_SUFIXO_DIAS.sub('', raw).strip()
    raw = _RE_SUFIXO_ANTIGO.sub('', raw).strip()
    # Se tem vírgula → pega só a primeira parte (remove repetições e filiais no título)
    if ',' in raw:
        primeira = raw.split(',')[0].strip()
        if primeira:
            raw = primeira
    return raw.strip(' ,;-–')


def extrair_campos(titulo: str) -> dict:
    """
    Extrai OS, CNPJ e nome da empresa do título de uma tarefa Planner.

    Formatos reconhecidos (sem depender de padrão fixo):
      "6077430 - Empresa"                           → OS + dash
      "54394, Empresa, Empresa, 516 DIAS - ANTIGO"  → OS + vírgula
      "6466572 Empresa"                             → OS + espaço (5+ dígitos)
      "OS 12345 - Empresa"                          → prefixo "OS"
      "AET/MEDIÇÕES - Empresa"                      → prefixo de setor
      "2A Engenharia" / "3 Corações"                → NÃO confunde com OS
    """
    resultado = {'os': None, 'cnpj': None, 'nome': titulo.strip()}
    t = titulo.strip()

    # CNPJ
    m = _RE_CNPJ.search(t)
    if m:
        resultado['cnpj'] = re.sub(r'\D', '', m.group())

    # Tenta detectar OS no início: dash → vírgula → espaço (nessa ordem de confiança)
    os_num = nome_raw = None
    for pat in (_RE_OS_DASH, _RE_OS_COMMA, _RE_OS_SPACE):
        m = pat.match(t)
        if m:
            os_num   = m.group(1)
            nome_raw = m.group(2)
            break

    if os_num:
        resultado['os']   = os_num
        nome_limpo = _limpar_nome_empresa(nome_raw)
        # Também remove prefixo de label após OS (ex: "6122998- AET/MEDIÇÕES - Cinafe")
        nome_limpo = _strip_prefixo_label(nome_limpo)
        resultado['nome'] = nome_limpo if nome_limpo else t
    else:
        # Fallback: "OS NNNN" em qualquer posição do título
        m = _RE_OS.search(t)
        if m:
            resultado['os'] = m.group(1)
        # Remove prefixo de setor/label tipo "AET/MEDIÇÕES - ", "LTCAT/PCA - ", "LIP e MEDIÇÕES- "
        nome_sem_prefixo = _strip_prefixo_label(t)
        if nome_sem_prefixo != t:
            resultado['nome'] = nome_sem_prefixo

    return resultado


# ── Matching principal ────────────────────────────────────────────────

def encontrar_empresa(conn, titulo: str, threshold: float = 0.72) -> tuple:
    """
    Tenta vincular o título da tarefa a uma empresa existente.

    Returns:
        (empresa_id: int|None, score: float, metodo: str|None)
    """
    campos = extrair_campos(titulo)
    empresas = conn.execute(
        'SELECT id, cnpj, nome FROM empresas WHERE id > 0 AND (pendente IS NULL OR pendente = 0)'
    ).fetchall()

    # 1. CNPJ exato
    if campos['cnpj']:
        for e in empresas:
            if e['cnpj'] and re.sub(r'\D', '', str(e['cnpj'])) == campos['cnpj']:
                log.debug('[match] CNPJ exato → empresa %s', e['id'])
                return e['id'], 1.0, 'cnpj'

    # 2. OS → busca demanda anterior já vinculada
    if campos['os']:
        row = conn.execute(
            'SELECT empresa_id FROM demandas WHERE numero_os=? AND empresa_id > 0 LIMIT 1',
            (campos['os'],)
        ).fetchone()
        if row:
            log.debug('[match] OS %s → empresa %s', campos['os'], row['empresa_id'])
            return row['empresa_id'], 0.95, 'os'

    # 3. Fuzzy por nome
    nome_tarefa = campos['nome'] or titulo
    melhor_id, melhor_score = None, 0.0
    for e in empresas:
        s = similaridade(nome_tarefa, e['nome'])
        if s > melhor_score:
            melhor_score = s
            melhor_id    = e['id']

    if melhor_score >= threshold:
        log.debug('[match] Fuzzy %.2f → empresa %s', melhor_score, melhor_id)
        return melhor_id, melhor_score, 'nome_fuzzy'

    return None, melhor_score, None


def obter_ou_criar_pendente(conn, titulo: str, campos: dict) -> int:
    """
    Se não encontrou empresa, cria/reutiliza empresa pendente.
    Returns: empresa_id (sempre > 0)
    """
    nome_raw  = (campos.get('nome') or titulo)[:200]
    cnpj_raw  = campos.get('cnpj') or ''

    # Verifica se já existe pendente com o mesmo nome normalizado
    pendentes = conn.execute(
        'SELECT id, nome FROM empresas WHERE pendente = 1'
    ).fetchall()
    for p in pendentes:
        if similaridade(p['nome'], nome_raw) > 0.90:
            return p['id']

    # Se tem CNPJ, verifica se já existe empresa com esse CNPJ
    if cnpj_raw:
        existing = conn.execute('SELECT id FROM empresas WHERE cnpj = ?', (cnpj_raw,)).fetchone()
        if existing:
            return existing['id']

    try:
        cur = conn.execute(
            '''INSERT INTO empresas (nome, cnpj, pendente, criado_em)
               VALUES (?, ?, 1, CURRENT_TIMESTAMP)''',
            (nome_raw, cnpj_raw or None)
        )
        log.info('[match] Empresa pendente criada: "%s"', nome_raw)
        return cur.lastrowid
    except Exception:
        # CNPJ duplicado ou outra constraint — reutiliza existente por nome
        existing = conn.execute(
            'SELECT id FROM empresas WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))', (nome_raw,)
        ).fetchone()
        if existing:
            return existing['id']
        # Último recurso: inserir sem CNPJ
        cur = conn.execute(
            'INSERT INTO empresas (nome, pendente, criado_em) VALUES (?, 1, CURRENT_TIMESTAMP)',
            (nome_raw,)
        )
        return cur.lastrowid


# ── Matching em lote ──────────────────────────────────────────────────

def match_todas_demandas(conn, threshold: float = 0.72) -> dict:
    """
    Percorre todas as demandas com empresa_id=0 e tenta vincular.
    Chamado ao final do sync Planner.

    Returns: estatísticas
    """
    pendentes = conn.execute(
        'SELECT id, titulo FROM demandas WHERE empresa_id = 0 OR empresa_id IS NULL'
    ).fetchall()

    stats = {'vinculadas': 0, 'pendentes_criadas': 0, 'total': len(pendentes)}

    for row in pendentes:
        did    = row['id']
        titulo = row['titulo'] or ''
        if not titulo:
            continue

        empresa_id, score, metodo = encontrar_empresa(conn, titulo, threshold)

        if empresa_id:
            conn.execute(
                '''UPDATE demandas
                   SET empresa_id=?, empresa_match_score=?, empresa_match_metodo=?
                   WHERE id=?''',
                (empresa_id, round(score, 3), metodo, did)
            )
            stats['vinculadas'] += 1
        else:
            campos     = extrair_campos(titulo)
            empresa_id = obter_ou_criar_pendente(conn, titulo, campos)
            conn.execute(
                '''UPDATE demandas
                   SET empresa_id=?, empresa_match_score=?, empresa_match_metodo=?
                   WHERE id=?''',
                (empresa_id, round(score, 3), 'pendente', did)
            )
            stats['pendentes_criadas'] += 1

    log.info('[match_lote] %s', stats)
    return stats
