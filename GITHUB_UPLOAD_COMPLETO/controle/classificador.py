# -*- coding: utf-8 -*-
"""
Classificador de tarefas Planner.

Diferencia automaticamente:
  - operacional  → demanda real de cliente (OS, empresa, medição)
  - interna      → gestão interna da equipe (treinamentos, reuniões, POPs...)
  - administrativa → sem sinais claros de nenhum dos dois

Critérios (ordem de prioridade):
  1. Bucket indica claramente interno  → interna
  2. Número de OS no título            → operacional
  3. Sufixo societário (LTDA/S.A/...)  → operacional
  4. Palavras-chave internas           → interna
  5. Bucket operacional                → operacional
  6. Sem indicadores                   → administrativa
"""

import re
import unicodedata

# ── Patterns ─────────────────────────────────────────────────────────

# OS: 5-8 dígitos no início ou após "MEDIÇÕES -"
_RE_OS_INICIO = re.compile(r'^(\d{4,8})\s*[-–,]')
_RE_OS_MED    = re.compile(r'MEDI[CÇ][ÕO]ES?\s*[-–]\s*(\d{4,8})', re.IGNORECASE)
_RE_OS_VIRGULA= re.compile(r'^(\d{4,8})\s*,')           # "57937, Nome Empresa"

# Sufixos societários
_RE_SUFIXO = re.compile(
    r'\b(ltda|s\.?a\.?|eireli|me|epp|ss|scp|s\.s)\b',
    re.IGNORECASE
)

# Buckets que indicam tarefas INTERNAS
_BUCKETS_INTERNOS = {
    'treinamento', 'treinamentos',
    'tarefas administrativas', 'administrativo',
    'reunião gestores', 'reuniao gestores',
    'kickoff',
    'materiais',
    'projetos principais',
    'lista de pendências', 'lista de pendencias',
    'email respondido', 'emails respondidos',
    'emails pendentes', 'email pendente',
    'tarefas pendentes',
    'avançar',
}

# Buckets operacionais (medições de clientes)
_BUCKETS_OPERACIONAIS = {
    'medições', 'medicoes',
    'verde', 'amarela', 'vermelho', 'laranja',
    '🔴 em andamento',
    'entregas técnicas', 'entregas tecnicas',
    'entregue / concluído', 'entregue',
    'concluído ✅', 'concluído', 'concluido',
    'clientes da base',
    'demandas para análise', 'demandas para analise',
    'em andamento',
    'novas demandas', 'engenharia - novas demandas',
    'renovação', 'renovacao',
    'inclusão de funcionários',
    'ajustes pontuais',
    'correção',
    'pcmso',
}

# Palavras que indicam tarefa INTERNA
_PALAVRAS_INTERNAS = {
    'reunião', 'reuniao', 'semanal', 'quinzenal', 'mensal',
    'treinamento', 'treinamentos', 'nr-20', 'nr-35', 'nr-33', 'nr-12',
    'auditoria nos pops', 'auditoria de pops', 'auditoria pop',
    'onboard', 'onboarding',
    'automatização', 'automatizacao', 'automatizar',
    'gravação', 'gravacao', 'gravar',
    'trilha', 'trilhas',
    'formulário', 'formulario', 'forms', 'form',
    'implantação', 'implantacao', 'implantar',
    'suporte de tv', 'suporte tv',
    'validação', 'validacao',  # sem empresa = interna
    'definição', 'definicao', 'definir sla',
    'organizar mutirão', 'mutirão', 'mutirao',
    'kickoff',
    'newsletter',
    'precificação', 'precificacao', 'precificar',
    'financeiro', 'financeira',
    'folha de pagamento', 'férias', 'ferias',
    'contratação', 'contratacao',
    'compras',
    'abho',
    'atribuir tarefas', 'atribuição',
    'carta de capacidade',
    'ações do plano de ação', 'plano de ação interno',
    'confeccionar carta',
    'histórico de revisões', 'historico de revisoes',
    'criação do pop', 'criacao do pop',
    'atualização do onboard', 'ajuste do onboard',
    'inventário de equipamento',
    'roteiro / checklist',
    'capacidade das máquinas',
    'auditar e atualizar excel',
}


def _normalizar(txt: str) -> str:
    """Lowercase sem acentos."""
    return unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode().lower()


def extrair_os(titulo: str) -> str | None:
    """
    Extrai número de OS do título da tarefa Planner.

    Formatos suportados:
      "6482868 - Nome empresa"          → "6482868"
      "57937, Nome empresa"             → "57937"
      "MEDIÇÕES - 6098824 - Nome"       → "6098824"
    """
    if not titulo:
        return None
    t = titulo.strip()
    m = _RE_OS_INICIO.match(t) or _RE_OS_VIRGULA.match(t)
    if m:
        return m.group(1)
    m = _RE_OS_MED.search(t)
    if m:
        return m.group(1)
    return None


def classificar(titulo: str, bucket: str = '', descricao: str = '') -> str:
    """
    Classifica a tarefa em: 'operacional' | 'interna' | 'administrativa'.

    Args:
        titulo:    título da tarefa no Planner
        bucket:    bucket/coluna da tarefa
        descricao: descrição da tarefa (opcional)

    Returns:
        str: classificação
    """
    t   = _normalizar(titulo   or '')
    b   = _normalizar(bucket   or '')
    d   = _normalizar(descricao or '')

    # 1. Bucket claramente interno
    if any(bi in b for bi in _BUCKETS_INTERNOS):
        return 'interna'

    # 2. OS no título → operacional (critério mais forte)
    if extrair_os(titulo):
        return 'operacional'

    # 3. Sufixo societário no título → operacional
    if _RE_SUFIXO.search(titulo or ''):
        return 'operacional'

    # 4. Palavras internas no título (substring)
    for p in _PALAVRAS_INTERNAS:
        if p in t:
            return 'interna'

    # 5. Bucket operacional → operacional
    if any(bo in b for bo in _BUCKETS_OPERACIONAIS):
        # ainda pode ser interna se não tem empresa identificável
        # mas sem indicadores de interno, considera operacional
        return 'operacional'

    # 6. Bucket vazio (384 do seed) → não é Planner, mantém como está
    if not bucket:
        return 'operacional'  # vem do seed, já tem empresa

    # Sem indicadores claros
    return 'administrativa'


def reclassificar_lote(conn) -> dict:
    """
    Reclassifica TODAS as demandas do Planner (origem='planner').
    Também extrai OS do título e preenche numero_os quando vazio.

    Returns: estatísticas
    """
    rows = conn.execute(
        "SELECT id, titulo, planner_bucket, descricao FROM demandas WHERE origem='planner'"
    ).fetchall()

    stats = {
        'total':         len(rows),
        'operacional':   0,
        'interna':       0,
        'administrativa':0,
        'os_extraida':   0,
    }

    for row in rows:
        tid    = row['id']
        titulo = row['titulo'] or ''
        bucket = row['planner_bucket'] or ''
        desc   = row['descricao'] or ''

        tipo = classificar(titulo, bucket, desc)
        os   = extrair_os(titulo)

        conn.execute(
            'UPDATE demandas SET tipo_demanda=?, numero_os=COALESCE(numero_os, ?) WHERE id=?',
            (tipo, os, tid)
        )
        stats[tipo] += 1
        if os:
            stats['os_extraida'] += 1

    return stats
