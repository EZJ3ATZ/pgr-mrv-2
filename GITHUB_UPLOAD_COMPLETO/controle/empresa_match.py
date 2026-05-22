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
_RE_OS_NUM = re.compile(r'^(\d{4,8})\s*[-–]')          # "6482868 - Nome"
_RE_CNPJ   = re.compile(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}')


# ── Normalização ──────────────────────────────────────────────────────

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

def extrair_campos(titulo: str) -> dict:
    """
    Extrai OS, CNPJ e nome da empresa do título de uma tarefa Planner.

    Formatos reconhecidos:
      "6482868 - Associação Atlética Banco do Brasil"
      "OS 12345 - Empresa X"
      "45.678.901/0001-23 Empresa Y"
      "Empresa Z Ltda - Filial SP"
    """
    resultado = {'os': None, 'cnpj': None, 'nome': titulo.strip()}

    # CNPJ
    m = _RE_CNPJ.search(titulo)
    if m:
        resultado['cnpj'] = re.sub(r'\D', '', m.group())

    # OS — formato "NNNNN - Nome" no início
    m = _RE_OS_NUM.match(titulo.strip())
    if m:
        resultado['os']   = m.group(1)
        resultado['nome'] = titulo.strip()[m.end():].strip(' -–')
    else:
        # OS — formato "OS NNNNN" em qualquer posição
        m = _RE_OS.search(titulo)
        if m:
            resultado['os'] = m.group(1)

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

    cur = conn.execute(
        '''INSERT INTO empresas (nome, cnpj, pendente, criado_em)
           VALUES (?, ?, 1, CURRENT_TIMESTAMP)''',
        (nome_raw, cnpj_raw or None)
    )
    log.info('[match] Empresa pendente criada: "%s"', nome_raw)
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
