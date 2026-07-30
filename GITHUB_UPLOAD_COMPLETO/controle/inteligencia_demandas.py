# -*- coding: utf-8 -*-
"""
Motor Operacional Inteligente de Interpretação de Demandas SST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Substitui o parser_agentes.py frágil por um motor com:
  - Dicionário operacional SST completo (agentes, aliases, variações)
  - Score de confiança (0–100%) por campo extraído e por fonte
  - Parser multi-fonte: título → checklist → descrição → chat → bucket
  - Detecção de inconsistências e conflitos entre fontes
  - Fila de revisão humana (score < threshold → needs_review = True)
  - Log rastreável de cada extração com origem e trecho
  - Suporte ao novo sistema de chat do Planner (Teams) e ao antigo

REGRA CENTRAL: Se a confiança for baixa → NÃO assuma → marque para revisão.
"""

from __future__ import annotations
import re
import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from collections import Counter


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. DICIONÁRIO OPERACIONAL SST
# Cada chave é o nome canônico; a lista contém todas as variações conhecidas.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AGENTES_SST: Dict[str, List[str]] = {
    # ── FÍSICOS ──────────────────────────────────────────────────────────
    'Ruído Ocupacional': [
        'ruido', 'ruído', 'ruido ocupacional', 'ruído ocupacional',
        'dosimetria', 'dosimetria de ruido', 'dosimetria de ruído',
        'dose ruido', 'dose ruído', 'medicao ruido', 'medição ruído',
        'avaliacao de ruido', 'avaliação de ruído', 'avaliacao ruido',
        'nr15 ruido', 'nr-15 ruido', 'nho01', 'nho-01', 'nho 01',
        'nivel de ruido', 'nivel de pressao sonora', 'pressao sonora',
        'avaliacao acustica', 'avaliação acústica', 'dba',
    ],
    'Calor (IBUTG)': [
        'calor', 'ibutg', 'estresse termico', 'estresse térmico',
        'calor ibutg', 'temperatura', 'nr15 calor', 'nr-15 calor',
        'avaliacao calor', 'avaliação de calor', 'termico',
        'condicoes termicas', 'condições térmicas', 'avaliacao termica',
        'avaliação térmica', 'heat stress',
    ],
    'Vibração de Corpo Inteiro (VCI)': [
        'vibracao corpo inteiro', 'vibração corpo inteiro',
        'vci', 'vibracao vci', 'vibração vci', 'iso 2631',
        'vibracao de corpo inteiro', 'vibração de corpo inteiro',
        'vibracao total', 'vibração total',
    ],
    'Vibração de Mão-Braço (VMB)': [
        'vibracao mao braco', 'vibração mão braço', 'vmb', 'vibracao vmb',
        'vibração vmb', 'iso 5349', 'vibração de mão braço',
        'vibracao mao-braco', 'vibracao de mao e braco',
    ],
    'Vibração (geral)': [
        'vibracao', 'vibração', 'vibracao mecanica', 'vibração mecânica',
    ],
    'Iluminamento': [
        'iluminamento', 'iluminancia', 'iluminação', 'iluminacao',
        'luximetria', 'lux', 'nivel de iluminamento', 'avaliacao luminosa',
        'avaliação luminosa',
    ],
    'Frio': [
        'frio', 'exposicao ao frio', 'exposição ao frio', 'ambiente frio',
        'nr15 frio', 'nr-15 frio', 'temperatura fria', 'camara fria',
        'câmara fria',
    ],
    'Radiações Ionizantes': [
        'radiacao ionizante', 'radiação ionizante', 'radioatividade',
        'raio x', 'raios x', 'rx', 'gamma', 'gama', 'beta',
        'radiacoes ionizantes', 'radiações ionizantes',
    ],
    'Radiações Não Ionizantes': [
        'radiacao nao ionizante', 'radiação não ionizante', 'rni',
        'uv', 'ultravioleta', 'infravermelho', 'iv', 'laser',
        'campo eletromagnetico', 'campo eletromagnético',
        'microondas', 'radiofrequencia', 'radiofrequência',
    ],
    'Pressão Hiperbárica': [
        'hiperbarico', 'hiperbárico', 'pressao hiperbarica',
        'pressão hiperbárica', 'mergulho', 'trabalho hiperbarico',
        'camara hiperbarica',
    ],
    'Umidade': [
        'umidade', 'umidade relativa', 'ur', 'umidade do ar',
    ],
    # ── QUÍMICOS ─────────────────────────────────────────────────────────
    'Sílica Cristalina': [
        'silica', 'sílica', 'silica cristalina', 'sílica cristalina',
        'poeira silica', 'poeira sílica', 'silica respiravel',
        'sílica respirável', 'quartzo', 'poeira quartzo',
        'fracao respiravel silica', 'fração respirável sílica',
        'cristobalita', 'tridimita',
    ],
    'Poeira Total': [
        'poeira total', 'poeira', 'particulados', 'material particulado',
        'mp10', 'mp2.5', 'mp 10', 'mp 2.5',
        'fracao inalavel', 'fração inalável', 'poeira inorganica',
        'poeira inorgânica', 'poeira mineral',
    ],
    'Benzeno': [
        'benzeno', 'benzene', 'benzol', 'nr15 benzeno', 'nr-15 benzeno',
        'ppra benzeno', 'programa benzeno', 'hidrocarboneto aromatico',
    ],
    'Tolueno': [
        'tolueno', 'toluene', 'metilbenzeno',
    ],
    'Xileno': [
        'xileno', 'xylene', 'dimetilbenzeno', 'xilenos',
    ],
    'Hexano': [
        'hexano', 'n-hexano', 'hexane', 'n hexano',
    ],
    'MEK (Butanona)': [
        'mek', 'butanona', 'metil etil cetona', 'methyl ethyl ketone',
        '2-butanona',
    ],
    'Acetona': [
        'acetona', 'propanona', 'dimetilcetona',
    ],
    'Álcool': [
        'alcool', 'álcool', 'etanol', 'metanol', 'isopropanol', 'ipa',
        'alcool etilico', 'álcool etílico',
    ],
    'Gases e Vapores (geral)': [
        'gases', 'vapores', 'gases e vapores', 'compostos organicos volateis',
        'cov', 'voc', 'solventes organicos', 'hidrocarbonetos',
        'solventes', 'vapores organicos',
    ],
    'Fumos Metálicos': [
        'fumos metalicos', 'fumos metálicos', 'fumos de solda',
        'fumo metalico', 'fumos de soldagem', 'particulas metalicas',
        'neblinas metalicas', 'neblinas metálicas', 'aerossois metalicos',
        'aerossóis metálicos',
    ],
    'Óxido de Ferro': [
        'oxido de ferro', 'óxido de ferro', 'fe2o3', 'oxido ferrico',
        'óxido férrico', 'fumos de ferro', 'oxido de ferro (fe2o3)',
    ],
    'Óxido de Zinco': [
        'oxido de zinco', 'óxido de zinco', 'zno', 'fumos de zinco',
        'oxido de zinco (zno)',
    ],
    'Chumbo': [
        'chumbo', 'pb', 'exposicao a chumbo', 'exposição ao chumbo',
        'compostos de chumbo',
    ],
    'Cromo': [
        'cromo', 'cromo hexavalente', 'cr6', 'cromo vi', 'cromo trivalente',
        'cromatos',
    ],
    'Manganês': [
        'manganes', 'manganês', 'mn', 'fumos de manganes', 'fumos de manganês',
        'dioxido de manganes', 'dióxido de manganês',
    ],
    'Níquel': [
        'niquel', 'níquel', 'ni', 'compostos de niquel', 'compostos de níquel',
        'carbonila de niquel',
    ],
    'Cádmio': [
        'cadmio', 'cádmio', 'cd', 'compostos de cadmio',
    ],
    'Mercúrio': [
        'mercurio', 'mercúrio', 'hg', 'vapores de mercurio', 'vapores de mercúrio',
    ],
    'Arsênio': [
        'arsenio', 'arsênio', 'as', 'arsenico', 'compostos de arsenio',
    ],
    'Amônia': [
        'amonia', 'amônia', 'nh3', 'amoniaco', 'amoníaco',
    ],
    'Monóxido de Carbono': [
        'monoxido de carbono', 'monóxido de carbono', 'co',
        'intoxicacao co',
    ],
    'Dióxido de Carbono': [
        'dioxido de carbono', 'dióxido de carbono', 'co2',
        'gas carbonico', 'gás carbônico',
    ],
    'Dióxido de Enxofre': [
        'dioxido de enxofre', 'dióxido de enxofre', 'so2', 'anidrido sulfuroso',
    ],
    'Dióxido de Nitrogênio': [
        'dioxido de nitrogenio', 'dióxido de nitrogênio', 'no2',
        'oxidos de nitrogenio', 'óxidos de nitrogênio', 'nox',
    ],
    'Ácido Clorídrico': [
        'acido cloridrico', 'ácido clorídrico', 'hcl',
        'cloreto de hidrogenio', 'clorídrico',
    ],
    'Ácido Sulfúrico': [
        'acido sulfurico', 'ácido sulfúrico', 'h2so4',
        'neblina acida', 'neblina ácida',
    ],
    'Ácido Nítrico': [
        'acido nitrico', 'ácido nítrico', 'hno3', 'nitrico', 'nítrico',
    ],
    'Ácido Fluorídrico': [
        'acido fluoridrico', 'ácido fluorídrico', 'hf', 'fluoridrico',
        'fluorídrico', 'fluoreto de hidrogenio',
    ],
    'Ácido Fosfórico': [
        'acido fosforico', 'ácido fosfórico', 'h3po4', 'fosforico',
    ],
    'Ácido Acético': [
        'acido acetico', 'ácido acético', 'acetico', 'acético',
    ],
    'Cianetos': [
        'cianeto', 'cianetos', 'sais de cianeto',
        'cianeto de sodio', 'cianeto de potassio', 'acido cianidrico',
        'ácido cianídrico', 'hcn',
    ],
    'Prata': [
        'prata', 'compostos de prata', 'nitrato de prata',
    ],
    'Cobre': [
        'cobre', 'fumos de cobre', 'compostos de cobre',
    ],
    'Alumínio': [
        'aluminio', 'alumínio', 'fumos de aluminio',
        'poeira de aluminio',
    ],
    'Estanho': [
        'estanho', 'compostos de estanho',
    ],
    'Cobalto': [
        'cobalto', 'compostos de cobalto',
    ],
    'Cloro': [
        'cloro', 'cl2', 'gas cloro', 'gás cloro',
    ],
    'Isocianatos (MDI/TDI)': [
        'isocianato', 'isocianatos', 'mdi', 'tdi', 'hdi',
    ],
    'Sulfeto de Hidrogênio': [
        'sulfeto de hidrogenio', 'sulfeto de hidrogênio', 'h2s',
        'gas sulfidrico', 'gás sulfídrico', 'acido sulfidrico',
    ],
    'Agrotóxicos / Pesticidas': [
        'agrotoxicos', 'agrotóxicos', 'pesticidas', 'praguicidas',
        'defensivos agricolas', 'defensivos agrícolas', 'herbicidas',
        'fungicidas', 'inseticidas',
    ],
    'Soda Cáustica (NaOH)': [
        'soda caustica', 'soda cáustica', 'naoh', 'hidroxido de sodio',
        'hidróxido de sódio',
    ],
    'Formaldeído': [
        'formaldeido', 'formaldeído', 'formaldehyde', 'formol', 'hcho',
    ],
    # ── ERGONÔMICO / PSICOSSOCIAL ─────────────────────────────────────────
    'Ergonomia': [
        'ergonomia', 'ergonomico', 'ergonômico', 'avaliacao ergonomica',
        'avaliação ergonômica', 'ler', 'dort', 'postura', 'biomecanica',
        'biomecânica', 'levantamento de carga', 'nr17', 'nr-17',
        'posto de trabalho', 'fator ergonomico',
    ],
    'Biológico': [
        'biologico', 'biológico', 'agentes biologicos', 'agentes biológicos',
        'microrganismos', 'bacterias', 'bactérias', 'fungos', 'virus', 'vírus',
        'parasitas', 'exposicao biologica', 'exposição biológica',
    ],
    # ── DOCUMENTOS E PROGRAMAS ───────────────────────────────────────────
    'PCMSO': [
        'pcmso', 'programa de controle medico', 'controle medico de saude',
        'medicina ocupacional', 'exames medicos', 'aso', 'admissional',
        'periodico', 'demissional',
    ],
    'PGR': [
        'pgr', 'programa de gerenciamento de riscos',
        'gerenciamento de riscos', 'inventario de riscos',
    ],
    'LTCAT': [
        'ltcat', 'laudo tecnico das condicoes ambientais',
        'laudo técnico das condições ambientais',
        'laudo condicoes ambientais', 'laudo condições ambientais',
    ],
    'PPP': [
        'ppp', 'perfil profissiografico previdenciario',
        'perfil profissiográfico previdenciário',
    ],
    'PPRA': [
        'ppra', 'programa de prevencao de riscos ambientais',
    ],
    'Laudo de Insalubridade': [
        'laudo insalubridade', 'insalubridade', 'adicional insalubridade',
        'nr15 laudo', 'laudo nr15',
    ],
    'Laudo de Periculosidade': [
        'laudo periculosidade', 'periculosidade', 'adicional periculosidade',
        'nr16 laudo', 'laudo nr16',
    ],
}

# ── Classificação por tipo ───────────────────────────────────────────────
_FISICOS = {
    'Ruído Ocupacional', 'Calor (IBUTG)', 'Vibração de Corpo Inteiro (VCI)',
    'Vibração de Mão-Braço (VMB)', 'Vibração (geral)', 'Iluminamento',
    'Frio', 'Radiações Ionizantes', 'Radiações Não Ionizantes',
    'Pressão Hiperbárica', 'Umidade',
}
_QUIMICOS = {k for k in AGENTES_SST if k not in _FISICOS and k not in
             {'Ergonomia', 'Biológico', 'PCMSO', 'PGR', 'LTCAT', 'PPP', 'PPRA',
              'Laudo de Insalubridade', 'Laudo de Periculosidade'}}

def _tipo_agente(canonical: str) -> str:
    if canonical in _FISICOS:       return 'fisico'
    if canonical in _QUIMICOS:      return 'quimico'
    if canonical == 'Ergonomia':    return 'ergonomico'
    if canonical == 'Biológico':    return 'biologico'
    return 'documento'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. ÍNDICE REVERSO  alias → canonical  (construído uma vez na importação)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _strip_accents(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )

def _norm(s: str) -> str:
    """Normaliza para comparação: sem acento, lowercase, strip."""
    return _strip_accents((s or '').lower().strip())

_ALIAS_INDEX: Dict[str, str] = {}
for _canonical, _aliases in AGENTES_SST.items():
    _ALIAS_INDEX[_norm(_canonical)] = _canonical
    for _a in _aliases:
        _ALIAS_INDEX[_norm(_a)] = _canonical

# Ordenar por comprimento decrescente para dar prioridade a frases mais longas
_ALIAS_SORTED: List[Tuple[str, str]] = sorted(
    _ALIAS_INDEX.items(), key=lambda x: -len(x[0])
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. HELPERS NUMÉRICOS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_NUMERAIS = {
    'um': 1, 'uma': 1, 'dois': 2, 'duas': 2,
    'tres': 3, 'três': 3, 'quatro': 4, 'cinco': 5,
    'seis': 6, 'sete': 7, 'oito': 8, 'nove': 9, 'dez': 10,
}

def _parse_int(s: str) -> Optional[int]:
    """'quatro', '04', '4', '4x' → int ou None."""
    s = _norm(s).replace('x', '').replace('×', '').strip()
    if s.isdigit():
        return int(s)
    return _NUMERAIS.get(s)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. DATACLASSES DE RESULTADO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class FonteInfo:
    """Rastreamento de onde uma informação foi extraída."""
    campo: str          # 'titulo' | 'descricao' | 'checklist' | 'chat' | 'bucket' | 'etiqueta'
    trecho: str         # trecho original (máx 120 chars)
    confianca: float    # 0.0 – 1.0


@dataclass
class AgenteExtraido:
    canonical: str
    quantidade: int
    tipo: str
    fontes: List[FonteInfo] = field(default_factory=list)
    confianca: float = 0.0

    def to_dict(self) -> dict:
        return {
            'canonical': self.canonical,
            'quantidade': self.quantidade,
            'tipo': self.tipo,
            'confianca': round(self.confianca, 3),
            'fontes': [{'campo': f.campo, 'trecho': f.trecho[:80],
                        'confianca': round(f.confianca, 3)} for f in self.fontes[:3]],
        }


@dataclass
class ExtractionResult:
    """Resultado completo da análise inteligente de uma tarefa do Planner."""
    task_id: str = ''
    titulo: str = ''

    # OS
    numero_os: Optional[str] = None
    numero_os_confianca: float = 0.0
    numero_os_fontes: List[FonteInfo] = field(default_factory=list)

    # Empresa (preenchida por empresa_match, mas score registrado aqui)
    empresa_nome: Optional[str] = None
    empresa_confianca: float = 0.0
    empresa_fontes: List[FonteInfo] = field(default_factory=list)

    # Agentes
    agentes: List[AgenteExtraido] = field(default_factory=list)

    # Dias de visita
    dias_visita: Optional[int] = None
    dias_visita_confianca: float = 0.0

    # Tipo da demanda
    tipo_demanda: str = ''
    tipo_confianca: float = 0.0

    # Qualidade
    needs_review: bool = False
    inconsistencias: List[str] = field(default_factory=list)
    conflitos: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    score_geral: float = 0.0

    # Rastreabilidade
    extraido_em: str = ''
    fontes_lidas: List[str] = field(default_factory=list)

    def calcular_score(self):
        scores, pesos = [], []
        if self.numero_os:
            scores.append(self.numero_os_confianca); pesos.append(2.0)
        if self.empresa_nome:
            scores.append(self.empresa_confianca); pesos.append(3.0)
        if self.agentes:
            media = sum(a.confianca for a in self.agentes) / len(self.agentes)
            scores.append(media); pesos.append(2.0)
        self.score_geral = (
            sum(s * p for s, p in zip(scores, pesos)) / sum(pesos)
            if scores else 0.0
        )
        self.needs_review = (
            self.score_geral < 0.60
            or self.numero_os_confianca < 0.50
            or (self.empresa_confianca < 0.55 and self.empresa_nome is not None)
            or len(self.conflitos) > 0
        )
        self.extraido_em = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'titulo': self.titulo,
            'numero_os': self.numero_os,
            'numero_os_confianca': round(self.numero_os_confianca, 3),
            'empresa_nome': self.empresa_nome,
            'empresa_confianca': round(self.empresa_confianca, 3),
            'agentes': [a.to_dict() for a in self.agentes],
            'dias_visita': self.dias_visita,
            'score_geral': round(self.score_geral, 3),
            'needs_review': self.needs_review,
            'inconsistencias': self.inconsistencias,
            'conflitos': self.conflitos,
            'warnings': self.warnings,
            'fontes_lidas': self.fontes_lidas,
            'extraido_em': self.extraido_em,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. EXTRAÇÃO DE AGENTES (DICIONÁRIO + CONTEXTO)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Padrão "realizar N avaliações de X"
_RE_REALIZAR = re.compile(
    r'realizar\s+(\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco)\s+(?:avalia[cç][aã][oe]s?\s+(?:de\s+)?)?(.{3,60})',
    re.I,
)
# Unidades de contagem que aparecem entre o número e o agente:
# "7 MEDIÇÕES de Ruído", "3 pontos de Calor", "2 coletas de Benzeno".
# "medição/medições" faltava aqui e é a palavra mais usada nas OS reais —
# sem ela "7 medições de Ruído" era lido como quantidade 1.
_UNIDADE_QTD = (
    r'medi[cç][oõ]es|medi[cç][aã]o|pontos?|amostras?|coletas?|'
    r'avalia[cç][oõ]es|avalia[cç][aã]o|dosimetrias?|monitoramentos?'
)

# Padrão "N pontos de X" / "N coletas de X" / "N medições de X"
_RE_PONTOS = re.compile(
    r'(\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco)\s*(?:' + _UNIDADE_QTD + r')\s+(?:de\s+)?(.{3,50})',
    re.I,
)

# Lookback de quantidade imediatamente antes de uma menção de agente.
# Aceita tanto "8 Ruídos" (número colado) quanto "7 medições de Ruído"
# (número separado do agente pela unidade de contagem).
_RE_QTD_LOOKBACK = re.compile(
    r'(\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco)\s*[xX×]?\s*'
    r'(?:(?:' + _UNIDADE_QTD + r')\s+(?:de\s+|do\s+|da\s+|dos\s+|das\s+)?)?$',
    re.I,
)
# Padrão quantidade antes: "4 ruídos" / "4x ruído" / "quatro ruídos"
_RE_QTD_ANTES = re.compile(
    r'(\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco)\s*[xX×]?\s+(.{3,50})',
    re.I,
)

# Aliases muito curtos ou ambíguos para evitar falsos positivos
_ALIAS_CURTO_MINLEN = 4   # aliases com < 4 chars normalizado → requerem contexto explícito
# Símbolos/siglas (CO, UV, Pb...) só contam quando vêm em MAIÚSCULA no texto
# original — distingue o símbolo "CO" de uma sílaba qualquer.
_ALIASES_AMBIGUOS  = {'co', 'pb', 'mn', 'ni', 'cd', 'hg', 'uv', 'iv'}
# Abreviações que são PALAVRAS comuns do português → nunca contam como token
# solto (o agente é sempre detectado pelo nome por extenso: "arsênio", "umidade",
# "ergonomia"). Sem isto, o artigo "as" de qualquer texto virava "Arsênio".
_ALIAS_PALAVRA_PT  = {'as', 'ur', 'ler'}


def _upsert_max(
    resultado: Dict[str, AgenteExtraido],
    canonical: str,
    qtd: int,
    conf: float,
    fonte: FonteInfo,
) -> None:
    """Grava o agente mantendo o MÁXIMO de quantidade e confiança já vistos.

    As estratégias B/C leem uma menção isolada; a estratégia A soma todas as
    menções do texto (OS multi-unidade). Sobrescrever direto fazia a leitura
    isolada derrubar a soma — aqui a quantidade nunca regride.
    """
    ex = resultado.get(canonical)
    if ex is None:
        resultado[canonical] = AgenteExtraido(
            canonical=canonical, quantidade=qtd,
            tipo=_tipo_agente(canonical), fontes=[fonte], confianca=conf,
        )
        return
    if conf > ex.confianca:
        ex.confianca = conf
        ex.fontes.append(fonte)
    if qtd > ex.quantidade:
        ex.quantidade = qtd


# Palavras que denunciam que o "agente" capturado é, na verdade, item de
# processo/documento — nunca vira medição de campo.
_RUIDO_GENERICO = re.compile(
    r'\b(laudo|treinamento|aet|ltcat|pcmso|ppra|pgr\b|ppp\b|proposta|'
    r'faturamento|visita|reuni[aã]o|cliente|contato|email|e-mail|'
    r'cronograma|relat[oó]rio|documento|planilha|assinatura|'
    r'funcion[aá]rio|colaborador|cargo|fun[cç][aã]o|setor|GHE|'
    r'campo|equipe|t[eé]cnico|prazo|entrega|dia|semana|m[eê]s)\b',
    re.I,
)

# "N <unidade> de <AGENTE>" — âncora explícita de medição no texto da OS.
_RE_AGENTE_LIVRE = re.compile(
    r'(\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco)\s*[xX×]?\s*'
    r'(?:' + _UNIDADE_QTD + r')\s+de\s+'
    r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\-/()\.]{2,58})',
    re.I,
)


def _agentes_desconhecidos(
    texto: str,
    peso_fonte: float,
) -> List[Tuple[str, int, float, str]]:
    """Captura agentes citados com âncora explícita mas ausentes do dicionário.

    Só aceita a forma "N medições/pontos/coletas de X" — âncora forte o
    bastante para não inventar agente a partir de prosa solta. Devolve
    [(nome, quantidade, confianca, trecho), ...].
    """
    achados: List[Tuple[str, int, float, str]] = []
    vistos: set = set()

    for m in _RE_AGENTE_LIVRE.finditer(texto or ''):
        bruto = m.group(2)
        # Corta na primeira pontuação forte: pega "Sais de Cianeto" de
        # "Sais de Cianeto\n1 medição de ..." sem arrastar a linha seguinte.
        nome = re.split(r'[\n\r,;.:•|]', bruto)[0].strip(' -–—/')
        if len(nome) < 3 or len(nome) > 58:
            continue
        if _RUIDO_GENERICO.search(nome):
            continue
        if not re.search(r'[A-Za-zÀ-ÿ]{3}', nome):
            continue

        nome_n = _norm(nome)
        # Já é conhecido pelo dicionário? Então as estratégias A/B/C tratam.
        if any(a in nome_n for a, _ in _ALIAS_SORTED
               if len(a) >= _ALIAS_CURTO_MINLEN):
            continue
        if nome_n in vistos:
            continue
        vistos.add(nome_n)

        qtd = _parse_int(m.group(1)) or 1
        if not 1 <= qtd <= 99:
            qtd = 1
        # Abaixo dos agentes de dicionário, acima do corte de exibição (0.55):
        # aparece para o técnico, mas sinalizado como menos confiante.
        conf = round(0.70 * peso_fonte, 3)
        trecho = m.group(0).strip()[:120]
        achados.append((_titulo_quimico(nome), qtd, conf, trecho))

    return achados


# Preposições que ficam em minúscula no nome do agente: o .title() cru
# devolvia "Metacrilato De Metila" na tela do técnico.
_PREPOSICOES = {'de', 'da', 'do', 'das', 'dos', 'e', 'em', 'com', 'a', 'o'}


def _titulo_quimico(nome: str) -> str:
    """Title-case respeitando preposições e siglas já em maiúscula."""
    palavras = nome.split()
    out = []
    for i, p in enumerate(palavras):
        if i > 0 and p.lower() in _PREPOSICOES:
            out.append(p.lower())
        elif p.isupper() and len(p) > 1:
            out.append(p)          # sigla: MEK, TDI, BTX
        else:
            out.append(p.capitalize())
    return ' '.join(out)


def _extrair_agentes_de_texto(
    texto: str,
    campo: str,
    peso_fonte: float = 1.0,
) -> Dict[str, AgenteExtraido]:
    """
    Extrai agentes de um texto com score de confiança.
    Retorna dict canonical → AgenteExtraido.
    """
    if not texto:
        return {}
    txt_n = _norm(texto)
    resultado: Dict[str, AgenteExtraido] = {}

    # Estratégia A: varrer aliases do maior para o menor (evita substring)
    for alias, canonical in _ALIAS_SORTED:
        # Abreviação que é palavra comum do português ("as", "ur", "ler") nunca
        # conta como token solto — o agente vem pelo nome por extenso. Sem isto,
        # o artigo "as" de qualquer descrição virava "Arsênio".
        if alias in _ALIAS_PALAVRA_PT:
            continue
        # OS multi-unidade (ex.: Belgo) repete o mesmo agente por unidade
        # ("GGAL - 8 Ruídos ... GCM - 4 Ruídos"): dentro de UM texto as menções
        # SOMAM; entre aliases do mesmo canonical vale o MÁXIMO (o alias curto
        # re-casa as mesmas menções do longo — somar somas duplicaria).
        if len(alias) < _ALIAS_CURTO_MINLEN:
            if alias in _ALIASES_AMBIGUOS:
                # Símbolo/sigla (CO, UV, Pb...): só conta em MAIÚSCULA no texto
                # original — distingue o símbolo de uma sílaba qualquer.
                # Ambíguo demais para somar menções: mantém 1ª ocorrência.
                pat_sym = r'(?<![A-Za-zÀ-ÿ])' + re.escape(alias.upper()) + r'(?![A-Za-zÀ-ÿ])'
                if not re.search(pat_sym, texto):
                    continue
                start = txt_n.find(alias)
                if start < 0:
                    continue
                starts = [start]
            else:
                # Alias curto: fronteira de LETRA (dígito colado conta: "2vci")
                pat = r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])'
                starts = [m.start() for m in re.finditer(pat, txt_n)]
                if not starts:
                    continue
        else:
            # Alias longo: todas as ocorrências como substring
            starts = [m.start() for m in re.finditer(re.escape(alias), txt_n)]
            if not starts:
                continue

        soma_qtd = 0
        melhor_conf = 0.0
        qtd_explicita = False
        for start in starts:
            # Fronteira de LETRA (não isalnum): "2ruidos" conta a menção,
            # "amostruido" não — dígito colado não pode descartar quantidade.
            pre_char  = txt_n[start - 1]   if start > 0              else ' '
            post_char = txt_n[start + len(alias)] if start + len(alias) < len(txt_n) else ' '
            fronteira_ok = not pre_char.isalpha() and not post_char.isalpha()

            conf_base = 0.85 if fronteira_ok else 0.55
            if len(alias) <= 5:
                conf_base = min(conf_base, 0.70)   # alias curto → menos confiante
            melhor_conf = max(melhor_conf, conf_base * peso_fonte)

            # Quantidade: olhar até 25 chars antes de CADA menção
            qtd = 1
            pre_txt = txt_n[max(0, start - 25):start]
            m_qtd = _RE_QTD_LOOKBACK.search(pre_txt)
            if m_qtd:
                q = _parse_int(m_qtd.group(1))
                if q and 1 <= q <= 30:
                    qtd = q
                    qtd_explicita = True
            soma_qtd += qtd

        conf = melhor_conf
        if qtd_explicita:
            conf = min(1.0, conf + 0.08)  # quantidade explícita → mais confiante

        primeiro = starts[0]
        trecho = texto[max(0, primeiro - 20):primeiro + len(alias) + 20].strip()[:120]
        fonte  = FonteInfo(campo=campo, trecho=trecho, confianca=round(conf, 3))

        if canonical not in resultado:
            resultado[canonical] = AgenteExtraido(
                canonical=canonical,
                quantidade=soma_qtd,
                tipo=_tipo_agente(canonical),
                fontes=[fonte],
                confianca=round(conf, 3),
            )
        else:
            ex = resultado[canonical]
            ex.fontes.append(fonte)
            ex.confianca = min(1.0, max(ex.confianca, conf))
            if soma_qtd > ex.quantidade:
                ex.quantidade = soma_qtd

    # Estratégia B: "realizar N avaliações de X"
    for m in _RE_REALIZAR.finditer(texto):
        q = _parse_int(m.group(1))
        agente_txt = _norm(m.group(2)[:60])
        for alias, canonical in _ALIAS_SORTED:
            if alias in agente_txt and len(alias) >= 4:
                conf = round(0.90 * peso_fonte, 3)
                fonte = FonteInfo(campo=campo, trecho=m.group(0)[:100], confianca=conf)
                _upsert_max(resultado, canonical, q or 1, conf, fonte)
                break

    # Estratégia C: "N pontos/coletas/medições de X"
    for m in _RE_PONTOS.finditer(texto):
        q = _parse_int(m.group(1))
        agente_txt = _norm(m.group(2)[:50])
        for alias, canonical in _ALIAS_SORTED:
            if alias in agente_txt and len(alias) >= 4:
                conf = round(0.88 * peso_fonte, 3)
                fonte = FonteInfo(campo=campo, trecho=m.group(0)[:100], confianca=conf)
                _upsert_max(resultado, canonical, q or 1, conf, fonte)
                break

    # Estratégia D: agente FORA do dicionário ("1 medição de Sais de Cianeto").
    # O dicionário é fechado e a lista de substâncias de higiene ocupacional é
    # aberta — sem isto, todo reagente não previsto sumia calado da OS.
    for canonical, qtd, conf, trecho in _agentes_desconhecidos(texto, peso_fonte):
        if canonical not in resultado:
            resultado[canonical] = AgenteExtraido(
                canonical=canonical, quantidade=qtd, tipo='quimico',
                fontes=[FonteInfo(campo=campo, trecho=trecho, confianca=conf)],
                confianca=conf,
            )

    return resultado


def extrair_agentes_multifonte(
    titulo: str = '',
    descricao: str = '',
    checklist: Any = None,
    chat_texto: str = '',
    bucket: str = '',
) -> List[AgenteExtraido]:
    """
    Extrai agentes de todas as fontes disponíveis.
    Fontes mais confiáveis têm peso maior: título > checklist > descrição > chat.
    Agente encontrado em múltiplas fontes ganha bônus de confiança.
    """
    # Converter checklist para texto
    if isinstance(checklist, dict):
        items = [v.get('title', '') for v in checklist.values() if isinstance(v, dict)]
    elif isinstance(checklist, list):
        items = [str(i) for i in checklist]
    else:
        items = []
    checklist_texto = ' | '.join(items)

    # Pesos por fonte (confiabilidade operacional)
    fontes = [
        (titulo,          'titulo',    1.00),
        (checklist_texto, 'checklist', 0.95),
        (descricao,       'descricao', 0.90),
        (chat_texto,      'chat',      0.72),
    ]

    acumulado: Dict[str, AgenteExtraido] = {}

    for texto, campo, peso in fontes:
        if not texto:
            continue
        parcial = _extrair_agentes_de_texto(texto, campo, peso)
        for canonical, ag in parcial.items():
            if canonical not in acumulado:
                acumulado[canonical] = ag
            else:
                ex = acumulado[canonical]
                ex.fontes.extend(ag.fontes)
                # Bônus multi-fonte: encontrado em mais de uma fonte → +5% por fonte extra
                ex.confianca = min(1.0, max(ex.confianca, ag.confianca) + 0.05)
                if ag.quantidade > ex.quantidade:
                    ex.quantidade = ag.quantidade

    # Bucket confirma agentes já encontrados (bônus) ou sugere com confiança menor
    if bucket:
        bucket_n = _norm(bucket)
        for alias, canonical in _ALIAS_SORTED:
            if len(alias) < 4:
                continue
            if alias in bucket_n:
                if canonical in acumulado:
                    acumulado[canonical].confianca = min(1.0, acumulado[canonical].confianca + 0.08)
                    acumulado[canonical].fontes.append(
                        FonteInfo(campo='bucket', trecho=bucket[:80], confianca=0.75)
                    )
                else:
                    # Bucket sugere agente não encontrado nos outros campos
                    acumulado[canonical] = AgenteExtraido(
                        canonical=canonical, quantidade=1,
                        tipo=_tipo_agente(canonical),
                        fontes=[FonteInfo('bucket', bucket[:80], 0.65)],
                        confianca=0.65,
                    )

    # Dedup vibração: se há vibração ESPECÍFICA (VCI/VMB), descarta a genérica
    # "Vibração (geral)". Sem isso, "vibração de corpo inteiro" casa nos dois
    # (a substring "vibração" dispara o genérico junto com o específico).
    if ('Vibração de Corpo Inteiro (VCI)' in acumulado
            or 'Vibração de Mão-Braço (VMB)' in acumulado):
        acumulado.pop('Vibração (geral)', None)

    # Dedup gases: se há um gás/vapor orgânico ESPECÍFICO (VOC), descarta o
    # genérico "Gases e Vapores (geral)" — a substring 'gases'/'vapores' dispara
    # o genérico junto com o específico (mesma classe do bug da vibração).
    _GAS_VOC = {'Benzeno', 'Tolueno', 'Xileno', 'Hexano', 'MEK (Butanona)', 'Acetona', 'Álcool'}
    if any(c in acumulado for c in _GAS_VOC):
        acumulado.pop('Gases e Vapores (geral)', None)

    # BTX / BTEX: a sigla cobre Benzeno + Tolueno + Xileno (1 tubo de carvão,
    # análise múltipla). Quando a sigla aparece em qualquer fonte, garante o trio.
    # OS multi-unidade repete a sigla por unidade ("FX - 1 BTX ... EMINAS - 1 BTX"):
    # soma as menções DENTRO de cada fonte; entre fontes vale o máximo.
    _re_btx = re.compile(r'(?<![a-z])bte?x(?![a-z])')
    _btx_qtd = 0
    for _t, _, _ in fontes:
        if not _t:
            continue
        _tn = _norm(_t)
        _soma = 0
        for _m in _re_btx.finditer(_tn):
            _q = 1
            _pre = _tn[max(0, _m.start() - 25):_m.start()]
            _mq = re.search(r'(\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco)[xX×\s]*$', _pre)
            if _mq:
                _pq = _parse_int(_mq.group(1))
                if _pq and 1 <= _pq <= 30:
                    _q = _pq
            _soma += _q
        _btx_qtd = max(_btx_qtd, _soma)
    if _btx_qtd:
        _base = acumulado.get('Benzeno')
        _conf = _base.confianca if _base else 0.80
        _qtd  = max(_btx_qtd, _base.quantidade if _base else 0)
        for _canon in ('Benzeno', 'Tolueno', 'Xileno'):
            if _canon not in acumulado:
                acumulado[_canon] = AgenteExtraido(
                    canonical=_canon, quantidade=_qtd,
                    tipo=_tipo_agente(_canon),
                    fontes=[FonteInfo('btx', 'BTX/BTEX', _conf)],
                    confianca=_conf,
                )
            elif _qtd > acumulado[_canon].quantidade:
                acumulado[_canon].quantidade = _qtd

    return sorted(acumulado.values(), key=lambda a: -a.confianca)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. EXTRAÇÃO DE NÚMERO DE OS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_OS_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    # Explícito: "OS 12345" / "O.S.: 1234" / "OS-12345"
    (re.compile(r'\bO\.?S\.?\s*[:\-]?\s*(\d{3,8})\b', re.I), 0.95, 'os_explicito'),
    # "nº 1234" / "n° 1234"
    (re.compile(r'\bn[°oº\.]\s*(\d{3,8})\b', re.I),           0.90, 'numero_ordinal'),
    # Número após separador: " - 1234 " ou " – 1234 "
    (re.compile(r'[-–—]\s*(\d{4,7})\b'),                       0.80, 'numero_separador'),
    # Número ao início da string (antes do nome da empresa)
    (re.compile(r'^\s*(\d{4,7})\b'),                           0.75, 'numero_inicio'),
    # Número entre parênteses
    (re.compile(r'\((\d{4,8})\)'),                             0.70, 'numero_parenteses'),
    # Após vírgula: "Empresa X, 12345"
    (re.compile(r',\s*(\d{4,7})\b'),                           0.65, 'numero_virgula'),
]

# Anos que NÃO são OS (2020-2030)
_RE_ANO = re.compile(r'^20[2-3]\d$')


def extrair_os_multifonte(
    titulo: str = '',
    descricao: str = '',
    checklist_texto: str = '',
    chat_texto: str = '',
) -> Tuple[Optional[str], float, List[FonteInfo]]:
    """
    Extrai número de OS de múltiplas fontes com score consolidado.
    Retorna (os_numero, confianca, fontes).
    """
    candidatos: List[Tuple[str, float, str, str]] = []

    fontes = [
        (titulo,          'titulo',    1.00),
        (descricao,       'descricao', 0.85),
        (checklist_texto, 'checklist', 0.80),
        (chat_texto,      'chat',      0.65),
    ]

    for texto, campo, peso in fontes:
        if not texto:
            continue
        for pattern, base_conf, padrao in _OS_PATTERNS:
            for m in pattern.finditer(texto):
                valor = m.group(1)
                if len(valor) < 3 or len(valor) > 8:
                    continue
                if _RE_ANO.match(valor):          # rejeitar anos
                    continue
                conf  = base_conf * peso
                trecho = texto[max(0, m.start() - 20):m.end() + 20].strip()[:120]
                candidatos.append((valor, conf, campo, trecho))

    if not candidatos:
        return None, 0.0, []

    # Consolidar: mesmo número em múltiplas fontes → bônus
    contagem = Counter(c[0] for c in candidatos)
    scores: Dict[str, float] = {}
    for valor, conf, campo, trecho in candidatos:
        bonus = 0.08 * (contagem[valor] - 1)   # +8% por fonte extra
        scores[valor] = max(scores.get(valor, 0.0), min(1.0, conf + bonus))

    melhor = max(scores, key=scores.get)
    conf_final = min(1.0, scores[melhor])
    fontes_out = [
        FonteInfo(campo=campo, trecho=trecho, confianca=round(conf, 3))
        for valor, conf, campo, trecho in candidatos if valor == melhor
    ]
    return melhor, round(conf_final, 3), fontes_out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. EXTRAÇÃO DE DIAS / VISITAS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_RE_DIAS: List[re.Pattern] = [
    re.compile(r'(\d+|um|dois|tr[eê]s|quatro|cinco)\s*dias?\s+(?:de\s+)?(?:campo|visita|obra)', re.I),
    re.compile(r'previs[aã]o\s+(?:de\s+)?(\d+)\s*dias?', re.I),
    re.compile(r'equipe\s+(?:em\s+)?(\d+)\s*dias?', re.I),
    re.compile(r'(\d+)\s*visitas?\s+(?:de\s+campo|previstas?)', re.I),
    re.compile(r'(\d+)\s*dias?\s+(?:de\s+)?(?:medic|campo)', re.I),
]

def extrair_dias_visita(
    textos: List[Tuple[str, str]],
) -> Tuple[Optional[int], float]:
    """textos = [(texto, campo), ...]. Retorna (dias, confiança)."""
    for pattern in _RE_DIAS:
        for texto, campo in textos:
            m = pattern.search(texto or '')
            if m:
                v = _parse_int(m.group(1))
                if v and 1 <= v <= 30:
                    conf = 0.85 if campo == 'titulo' else 0.70
                    return v, conf
    return None, 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. CHAT API — Comentários vs Chat do Planner no Teams
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extrair_texto_chat(
    group_id: str,
    task: dict,
    graph_get_fn,
) -> str:
    """
    Extrai texto do chat/comentários de uma tarefa do Planner.

    Microsoft Planner tem DOIS sistemas:

    SISTEMA ANTIGO (abolido como interface, mas API ainda responde):
      task.conversationThreadId → /groups/{gid}/threads/{threadId}/posts
      Muitas tasks antigas têm o campo mas posts vêm vazios.

    SISTEMA NOVO (Teams Chat):
      O chat de tarefa no Teams NÃO tem endpoint público direto sem
      permissão ChannelMessage.Read.All (permissão de aplicação).
      Alternativa: buscar mensagens no canal do grupo que mencionem
      o ID ou título da tarefa via $search — impreciso.

    Esta função tenta o sistema antigo. Se retornar vazio, retorna ''.
    Não faz suposições sobre o conteúdo.
    """
    if not task or not graph_get_fn:
        return ''

    thread_id = task.get('conversationThreadId', '')
    if not thread_id:
        return ''

    try:
        data = graph_get_fn(f'/groups/{group_id}/threads/{thread_id}/posts')
        posts = (data or {}).get('value', [])
        textos = []
        for post in posts[:10]:
            content = (post.get('body') or {}).get('content', '') or ''
            # Remover HTML
            content = re.sub(r'<[^>]+>', ' ', content)
            content = re.sub(r'\s+', ' ', content).strip()
            if len(content) > 10:
                textos.append(content[:500])
        return ' | '.join(textos)
    except Exception:
        return ''


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. VALIDAÇÃO DE CONSISTÊNCIA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_BUCKETS_CONCLUIDOS = {
    'entregue', 'concluido', 'concluído', 'finalizado',
    'fechado', 'arquivado', 'done', 'completed',
}
_BUCKETS_ABERTOS = {
    'novas demandas', 'em andamento', 'clientes da base', 'renovação',
    'renovacao', 'medições', 'medicoes', 'verde', 'amarela',
    'vermelho', 'laranja', 'entregas técnicas', 'entregas tecnicas', 'pcmso',
}


def validar_resultado(
    result: ExtractionResult,
    bucket: str,
    percent_complete: int,
) -> ExtractionResult:
    """Detecta inconsistências entre campos e popula result.inconsistencias / conflitos."""
    bn = _norm(bucket or '')

    concluido_bucket = any(b in bn for b in _BUCKETS_CONCLUIDOS)
    aberto_bucket    = any(b in bn for b in _BUCKETS_ABERTOS)

    # Inconsistência 1: 100% + bucket aberto
    if percent_complete == 100 and aberto_bucket:
        result.inconsistencias.append(
            f'percentComplete=100 mas bucket="{bucket}" indica aberta — demanda esquecida?'
        )

    # Inconsistência 2: 0% + bucket concluído
    if percent_complete == 0 and concluido_bucket:
        result.warnings.append(
            f'Bucket="{bucket}" indica concluída mas percentComplete=0%'
        )

    # Aviso: sem OS
    if not result.numero_os:
        result.warnings.append('Nenhum número de OS identificado')

    # Aviso: sem agentes
    if not result.agentes:
        result.warnings.append('Nenhum agente SST identificado')

    # Inconsistência 3: quantidade de agente suspeita
    for ag in result.agentes:
        if ag.quantidade > 20:
            result.inconsistencias.append(
                f'"{ag.canonical}": quantidade={ag.quantidade} parece incorreta'
            )

    # Conflito: empresa com confiança muito baixa
    if result.empresa_nome and result.empresa_confianca < 0.50:
        result.conflitos.append(
            f'Empresa "{result.empresa_nome}" com confiança={result.empresa_confianca:.0%} — verificar'
        )

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. MOTOR PRINCIPAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def analisar_tarefa_planner(
    task: dict,
    task_details: dict,
    group_id: str = '',
    bucket_nome: str = '',
    graph_get_fn=None,
) -> ExtractionResult:
    """
    Motor principal. Analisa uma tarefa do Planner com todas as fontes
    disponíveis e retorna ExtractionResult com scores e rastreabilidade.
    """
    result = ExtractionResult(
        task_id=task.get('id', ''),
        titulo=task.get('title', '') or '',
    )

    titulo    = task.get('title', '') or ''
    details   = task_details or {}
    descricao = details.get('description', '') or ''
    checklist = details.get('checklist') or {}
    try:
        percent = int(task.get('percentComplete', 0) or 0)
    except (ValueError, TypeError):
        # Planner às vezes manda valores não-numéricos (ex.: 'M') → trata como 0
        percent = 0

    # Checklist → texto plano
    if isinstance(checklist, dict):
        cl_items = [v.get('title', '') for v in checklist.values() if isinstance(v, dict)]
    elif isinstance(checklist, list):
        cl_items = [str(i) for i in checklist]
    else:
        cl_items = []
    checklist_texto = ' | '.join(cl_items)

    # Chat / comentários
    chat_texto = extrair_texto_chat(group_id, task, graph_get_fn) if graph_get_fn else ''

    # Registrar fontes lidas
    result.fontes_lidas = ['titulo']
    if descricao:       result.fontes_lidas.append('descricao')
    if checklist_texto: result.fontes_lidas.append('checklist')
    if chat_texto:      result.fontes_lidas.append('chat')
    result.fontes_lidas.append('bucket')

    # ── Extrair OS ──────────────────────────────────────────────────────
    os_num, os_conf, os_fontes = extrair_os_multifonte(
        titulo=titulo,
        descricao=descricao,
        checklist_texto=checklist_texto,
        chat_texto=chat_texto,
    )
    result.numero_os           = os_num
    result.numero_os_confianca = os_conf
    result.numero_os_fontes    = os_fontes

    # ── Extrair agentes ────────────────────────────────────────────────
    result.agentes = extrair_agentes_multifonte(
        titulo=titulo,
        descricao=descricao,
        checklist=checklist,
        chat_texto=chat_texto,
        bucket=bucket_nome,
    )

    # ── Extrair dias de visita ─────────────────────────────────────────
    result.dias_visita, result.dias_visita_confianca = extrair_dias_visita([
        (titulo,          'titulo'),
        (checklist_texto, 'checklist'),
        (descricao,       'descricao'),
    ])

    # ── Validar inconsistências ────────────────────────────────────────
    result = validar_resultado(result, bucket_nome, percent)

    # ── Score final ───────────────────────────────────────────────────
    result.calcular_score()

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 11. COMPATIBILIDADE COM PARSER ANTIGO (api_compat)
# Mantém a assinatura antiga para que planner_sync.py seja migrado
# gradualmente sem quebrar nada.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extrair_agentes_compat(descricao: str) -> List[dict]:
    """
    API de compatibilidade com parser_agentes.extrair_agentes().
    Retorna lista de dicts no formato antigo: {tipo, agente, qtd, pontos}.
    """
    agentes = extrair_agentes_multifonte(descricao=descricao)
    result = []
    for ag in agentes:
        # Separar tipo canônico em tipo/agente compatível
        result.append({
            'tipo':   ag.tipo,
            'agente': ag.canonical,
            'qtd':    ag.quantidade,
            'pontos': ag.quantidade,
            'confianca': round(ag.confianca, 3),
        })
    return result


def extrair_os_compat(titulo: str, descricao: str = '', checklist_texto: str = '') -> Optional[str]:
    """API de compatibilidade com classificador.extrair_os()."""
    os_num, _, _ = extrair_os_multifonte(titulo=titulo, descricao=descricao, checklist_texto=checklist_texto)
    return os_num
