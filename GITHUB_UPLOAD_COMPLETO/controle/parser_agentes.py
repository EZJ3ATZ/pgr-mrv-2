# -*- coding: utf-8 -*-
"""
Parser de agentes de medição a partir de texto livre de OS do Planner.

Extrai pares (quantidade, agente) de descrições como:
  "04 medições de ruído"
  "1 Ruído"
  "Ruído (1 ponto)"
  "vibração de corpo inteiro 1 ponto"
  "5 medições de Ruído, 3 de Vibração Mãos e Braços"

Retorna lista de dicts: [{tipo, qtd, texto}, ...]
"""

import re
import unicodedata

# ── Palavras que indicam que a linha NÃO é uma medição ──────────────
_IGNORAR_LINHA = re.compile(
    r'\b(AET|treinamento|NR[-\s]?\d+|LIP\b|LTCAT|PCMSO|PPRA|PGR\b|'
    r'laudo|faturamento|proposta|visita|'
    r'cargos?\b|fun[çc][oõ][ens]+|email|contato|cliente|obs\b|observa)',
    re.IGNORECASE
)

# ── Padrões de extração ──────────────────────────────────────────────

# A: "04 medições de ruído" / "3 pontos de Vibração" / "2 avaliações de calor"
_PAT_A = re.compile(
    r'(\d+)\s+(?:medi[çc][oõ]es?|avalia[çc][oõ]es?|pontos?)\s+de\s+([A-Za-zÀ-ÿ][^\n,;.→\(\)]{1,60})',
    re.IGNORECASE
)

# B: "1 Ruído" / "03 Vibração de Corpo Inteiro" — número no início de linha/segmento
_PAT_B = re.compile(
    r'(?:^|[\n,;])\s*(\d+)\s+([A-Za-zÀ-ÿ][^\n,;.→\(\)\d]{2,60}?)(?=[,;.\n\r→\(\)]|$)',
    re.IGNORECASE | re.MULTILINE
)

# C: "Ruído (1 ponto)" / "Ácido Fluorídrico (1 ponto)"
_PAT_C = re.compile(
    r'([A-Za-zÀ-ÿ][^\n\(\),;.]{2,60}?)\s*\(\s*(\d+)\s*pontos?\s*\)',
    re.IGNORECASE
)

# D: "ruído 8 pontos" / "vibração de corpo inteiro 1 ponto"
_PAT_D = re.compile(
    r'([A-Za-zÀ-ÿ][^\n,;.→\d]{4,60}?)\s+(\d+)\s+pontos?',
    re.IGNORECASE
)

# E: "e N de X" — continuação inline: "3 de ruído e 3 de calor"
_PAT_E = re.compile(
    r'\be\s+(\d+)\s+de\s+([A-Za-zÀ-ÿ][^\n,;.→\(\)]{1,40})',
    re.IGNORECASE
)

# F: "medição/avaliação de X" sem número explícito (qtd implícita = 1)
#    ex: "medição de ruído", "avaliação de calor", "realizar medição de silica"
_PAT_F = re.compile(
    r'(?:medi[çc][aã]o|avalia[çc][aã]o)\s+de\s+([A-Za-zÀ-ÿ][^\n,;.→\(\)\d]{2,60})',
    re.IGNORECASE
)


def _sem_acento(txt: str) -> str:
    return unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode().lower()


def _normalizar_tipo(texto: str) -> str:
    n = _sem_acento(texto)
    if re.search(r'ruido|dosimetri', n):
        return 'ruido'
    if re.search(r'corpo inteiro|vci\b', n):
        return 'vibracao_vci'
    if re.search(r'maos e bracos|maos-e-bracos|vbma\b|vmb\b|localiz|extremidade', n):
        return 'vibracao_vbma'
    if re.search(r'vibra', n):
        return 'vibracao'  # subtipo ambíguo
    if re.search(r'calor|ibutg|temperatura', n):
        return 'calor'
    if re.search(r'silica|poeira mineral|particulado', n):
        return 'particulado'
    if re.search(r'poeira', n):
        return 'particulado'
    return 'quimico'


def _linha_ignoravel(linha: str) -> bool:
    """True se a linha claramente não descreve uma medição de agente."""
    if _IGNORAR_LINHA.search(linha):
        return True
    # linhas muito curtas ou só espaços
    if len(linha.strip()) < 3:
        return True
    return False


def _adicionar(resultados: list, qtd_str: str, texto: str):
    """Adiciona entrada normalizada à lista, evitando duplicatas."""
    texto = texto.strip(' ,;:-–→').strip()
    if not texto or len(texto) < 2:
        return
    if _linha_ignoravel(texto):
        return
    try:
        qtd = int(qtd_str)
    except (ValueError, TypeError):
        qtd = 1
    if qtd <= 0 or qtd > 999:
        return

    tipo = _normalizar_tipo(texto)
    # evita duplicata exata
    for r in resultados:
        if r['tipo'] == tipo and abs(r['qtd'] - qtd) == 0 and _sem_acento(r['texto']) == _sem_acento(texto):
            return
    resultados.append({'tipo': tipo, 'qtd': qtd, 'texto': texto.strip()})


def extrair_agentes(descricao: str) -> list:
    """
    Extrai lista de agentes de medição de uma descrição livre.

    Returns:
        [{'tipo': str, 'qtd': int, 'texto': str}, ...]

    Tipos possíveis:
        ruido, vibracao_vci, vibracao_vbma, vibracao, calor, particulado, quimico
    """
    if not descricao:
        return []

    resultados = []

    # Padrão A — "N medições/pontos de X"
    for m in _PAT_A.finditer(descricao):
        _adicionar(resultados, m.group(1), m.group(2))

    # Padrão E — continuação "e N de X"
    for m in _PAT_E.finditer(descricao):
        _adicionar(resultados, m.group(1), m.group(2))

    # Padrão C — "X (N ponto)"
    for m in _PAT_C.finditer(descricao):
        texto = m.group(1)
        if not _linha_ignoravel(texto):
            _adicionar(resultados, m.group(2), texto)

    # Padrão D — "X N pontos"
    for m in _PAT_D.finditer(descricao):
        texto = m.group(1)
        if not _linha_ignoravel(texto):
            _adicionar(resultados, m.group(2), texto)

    # Padrão F — "medição/avaliação de X" sem número (qtd=1 implícita)
    for m in _PAT_F.finditer(descricao):
        texto = m.group(1)
        if not _linha_ignoravel(texto):
            _adicionar(resultados, '1', texto)

    # Padrão B — "N X" no início de linha (só se ainda não extraiu nada por A/E/F)
    if not resultados:
        for m in _PAT_B.finditer(descricao):
            texto = m.group(2)
            if not _linha_ignoravel(texto):
                _adicionar(resultados, m.group(1), texto)

    return resultados


def resumo_agentes(agentes: list) -> dict:
    """
    Agrupa agentes extraídos por tipo, somando quantidades.

    Returns dict com chaves ruido, vibracao_vci, vibracao_vbma,
    vibracao, calor, particulado, quimicos (lista de {texto, qtd}).
    """
    resumo = {
        'ruido': 0,
        'vibracao_vci': 0,
        'vibracao_vbma': 0,
        'vibracao': 0,
        'calor': 0,
        'particulado': 0,
        'quimicos': [],
    }
    for a in agentes:
        t = a['tipo']
        if t in resumo and isinstance(resumo[t], int):
            resumo[t] += a['qtd']
        elif t == 'quimico':
            resumo['quimicos'].append({'texto': a['texto'], 'qtd': a['qtd']})
    return resumo
