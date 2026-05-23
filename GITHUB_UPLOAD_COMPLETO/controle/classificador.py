# -*- coding: utf-8 -*-
"""
Classificador de tarefas Planner.

Tipos:
  - medicao      → medição de campo real: tem OS + atribuída a Wesley/Matheus/Helbert
                   ou bucket claramente de medição
  - laudo        → documento técnico: AET, ergonomia, LTCAT, etc.
  - operacional  → demanda de cliente sem tipo específico (OS sem técnico de campo,
                   sufixo societário, bucket operacional)
  - interna      → gestão interna (treinamentos, reuniões, POPs...)
  - administrativa → sem indicadores claros

Critérios (ordem de prioridade):
  1. Bucket interno              → interna
  2. Palavra-chave de laudo (AET/ergonomia/LTCAT…) → laudo
  3. OS + técnico de medição     → medicao
  4. OS sem técnico de campo     → operacional
  5. Sufixo societário           → operacional
  6. Palavras-chave internas     → interna
  7. Técnico de medição + bucket operacional → medicao
  8. Bucket operacional          → operacional
  9. Sem bucket (seed)           → operacional
 10. Demais                      → administrativa
"""

import re
import unicodedata

# ── Patterns ─────────────────────────────────────────────────────────

# OS: 4-8 dígitos no início ou após "MEDIÇÕES"
_RE_OS_INICIO  = re.compile(r'^(\d{4,8})\s*[-–,]')
_RE_OS_ESPACO  = re.compile(r'^(\d{4,8})\s+[A-Za-zÀ-ú\(]')  # "6429857 Santa Cecilia..."
_RE_OS_MED     = re.compile(r'MEDI[CÇ][ÕO]ES?\s*[-–\s]\s*(\d{4,8})', re.IGNORECASE)
_RE_OS_VIRGULA = re.compile(r'^(\d{4,8})\s*,')   # "57937, Nome Empresa"

# Sufixos societários
_RE_SUFIXO = re.compile(
    r'\b(ltda|s\.?a\.?|eireli|me|epp|ss|scp|s\.s)\b',
    re.IGNORECASE
)

# Laudos/documentos técnicos — NÃO são medições de campo
_RE_LAUDO = re.compile(
    r'\baet\b'             # Análise Ergonômica do Trabalho
    r'|ergon[oô]m'         # ergonomia, ergonômico
    r'|\bltcat\b'          # Laudo Técnico das Condições do Ambiente de Trabalho
    r'|\bppra\b'           # Programa de Prevenção de Riscos Ambientais
    r'|laudo\s+de\s+insalubr'
    r'|laudo\s+de\s+periculosidade'
    r'|avali[aá][cç][aã]o\s+(ergon|de\s+posto)',
    re.IGNORECASE
)

# Nomes dos técnicos de medição de campo
_NOMES_MEDICAO = {'wesley', 'matheus', 'helbert'}

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

# Buckets operacionais (medições/demandas de clientes)
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
    'validação', 'validacao',
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


def _e_tecnico_medicao(assignee: str) -> bool:
    """Verifica se o responsável é um técnico de medição de campo."""
    a = _normalizar(assignee or '')
    return any(nome in a for nome in _NOMES_MEDICAO)


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
    m = _RE_OS_INICIO.match(t) or _RE_OS_VIRGULA.match(t) or _RE_OS_ESPACO.match(t)
    if m:
        return m.group(1)
    m = _RE_OS_MED.search(t)
    if m:
        return m.group(1)
    return None


def classificar(titulo: str, bucket: str = '', descricao: str = '',
                assignee: str = '') -> str:
    """
    Classifica a tarefa em:
      'medicao' | 'laudo' | 'operacional' | 'interna' | 'administrativa'

    Args:
        titulo:    título da tarefa no Planner
        bucket:    bucket/coluna da tarefa
        descricao: descrição da tarefa (opcional)
        assignee:  nome do responsável (display_name do ms_users)

    Returns:
        str: classificação
    """
    t = _normalizar(titulo   or '')
    b = _normalizar(bucket   or '')

    # 1. Bucket claramente interno
    if any(bi in b for bi in _BUCKETS_INTERNOS):
        return 'interna'

    # 2. Laudo/documento técnico (AET, ergonomia…) → laudo
    if _RE_LAUDO.search(titulo or ''):
        return 'laudo'

    # 3. OS no título
    if extrair_os(titulo):
        # Técnico de medição atribuído → medição de campo real
        if _e_tecnico_medicao(assignee):
            return 'medicao'
        # OS sem técnico de campo → operacional genérico
        return 'operacional'

    # 4. Sufixo societário → operacional (é cliente)
    if _RE_SUFIXO.search(titulo or ''):
        return 'operacional'

    # 5. Palavras internas no título
    for p in _PALAVRAS_INTERNAS:
        if p in t:
            return 'interna'

    # 6. Técnico de medição + bucket operacional → medição
    if _e_tecnico_medicao(assignee) and any(bo in b for bo in _BUCKETS_OPERACIONAIS):
        return 'medicao'

    # 7. Bucket operacional → operacional
    if any(bo in b for bo in _BUCKETS_OPERACIONAIS):
        return 'operacional'

    # 8. Sem bucket (seed) → operacional (já tem empresa vinculada)
    if not bucket:
        return 'operacional'

    # Sem indicadores claros
    return 'administrativa'


def reclassificar_lote(conn) -> dict:
    """
    Reclassifica TODAS as demandas do Planner (origem='planner').
    Usa assignee (display_name do ms_users) para distinguir medição de campo.
    Extrai OS do título e preenche numero_os quando vazio.

    Returns: estatísticas por tipo
    """
    rows = conn.execute("""
        SELECT d.id, d.titulo, d.planner_bucket, d.descricao,
               COALESCE(u.display_name, '') AS assignee_name
        FROM demandas d
        LEFT JOIN ms_users u ON u.ms_id = d.ms_assignee_id
        WHERE d.origem = 'planner'
    """).fetchall()

    stats = {
        'total':         len(rows),
        'medicao':       0,
        'laudo':         0,
        'operacional':   0,
        'interna':       0,
        'administrativa': 0,
        'os_extraida':   0,
    }

    for row in rows:
        tid      = row['id']
        titulo   = row['titulo'] or ''
        bucket   = row['planner_bucket'] or ''
        desc     = row['descricao'] or ''
        assignee = row['assignee_name'] or ''

        tipo = classificar(titulo, bucket, desc, assignee)
        os   = extrair_os(titulo)

        conn.execute(
            'UPDATE demandas SET tipo_demanda=?, numero_os=COALESCE(numero_os, ?) WHERE id=?',
            (tipo, os, tid)
        )
        stats[tipo] = stats.get(tipo, 0) + 1
        if os:
            stats['os_extraida'] += 1

    return stats
