# -*- coding: utf-8 -*-
"""Produto de MEDIÇÃO do catálogo do CRM -> agente do guia de métodos.

A OS que nasce no CRM já diz o que foi vendido: produto do catálogo e
quantidade. Este mapa copia, não interpreta. Antes (06/10/2026) a OS do CRM
virava texto e o motor de extração relia: os 5 álcoois viravam "Álcool",
BTXE perdia o benzeno, "Peróxido de Metil Etil Cetona" virava MEK e Cromo e
PNOS perdiam a quantidade.

Cada linha: produto -> (agente, tipo legado, situação). O agente é o texto que
o planejamento usa; todos foram conferidos em `_buscar_metodos_agente` contra a
entrada certa do guia (tests/test_catalogo_medicao.py refaz a conferência).

Situação:
  ok         — agente certo, sem ambiguidade
  confirmar  — o guia separa a substância em formas com método diferente; entra
               a forma padrão e o técnico confere no planejamento (lista para o
               Helbert validar no padrão de 06/10/2026)
  sem_metodo — produto sem entrada no guia de métodos
  logistica  — não é agente (diária = dias de campo)
  generico   — "Medições" sem dizer o quê: OS de medição recusada

Produto novo no catálogo do CRM = linha nova aqui. Sem linha, a OS é recusada
com o nome do produto, em vez de entrar adivinhada.
"""
import re
import unicodedata

CATALOGO_MEDICAO = {
    '1-Metoxi 2-Propanol': ('1-METOXI-2-PROPANOL', 'quimico', 'ok'),
    '2-Butóxi Etanol': ('2-Butóxi Etanol', 'quimico', 'ok'),
    '2-Etoxietanol': ('2-Etoxietanol', 'quimico', 'ok'),
    '2-Metoxietanol': ('2-Metoxietanol', 'quimico', 'ok'),
    'Acetato de 2-Etoxietila': ('Acetato de 2-Etoxietila', 'quimico', 'ok'),
    'Acetato de 2-Metoxietila': ('Acetato de 2-Metoxietila', 'quimico', 'ok'),
    'Acetato de Butila': ('Acetato de Butila', 'quimico', 'ok'),
    'Acetato de Etila': ('Acetato de Etila', 'quimico', 'ok'),
    'Acetato de Metila': ('Acetato de Metila', 'quimico', 'ok'),
    'Acetato de Pentila': ('Acetato de Pentila', 'quimico', 'ok'),
    'Acetato de Vinila': ('Acetato de Vinila', 'quimico', 'ok'),
    'Acetona': ('Acetona', 'quimico', 'ok'),
    'Acetonitrila': ('Acetonitrila', 'quimico', 'ok'),
    'Aguarrás Mineral': ('Aguarrás Mineral', 'quimico', 'ok'),
    'Alumínio': ('Alumínio', 'quimico', 'ok'),
    'Amônia': ('Amônia', 'quimico', 'ok'),
    'Anilina': ('Anilina', 'quimico', 'ok'),
    'Arsênico (Arsênio)': ('ARSÊNIO E COMPOSTOS INORGÂNICOS, COM AS', 'quimico', 'ok'),
    'Asfalto': ('Asfalto', 'quimico', 'ok'),
    'Azida de Sódio': ('Azida de Sódio', 'quimico', 'ok'),
    'Benzeno': ('Benzeno', 'quimico', 'ok'),
    'Borato': ('Borato', 'quimico', 'ok'),
    'BTX (Benzeno, Tolueno e Xileno)': ('BTX (Benzeno + Tolueno + Xileno)', 'quimico', 'ok'),
    'BTXE (Benzeno, Tolueno, Xileno e Etilbenzeno)': ('BTXE (Benzeno + Tolueno + Xileno + Etilbenzeno)', 'quimico', 'ok'),
    'Calor (por função / GHE, independente do nº de pontos)': ('Calor (IBUTG)', 'calor', 'ok'),
    'Carvão, poeiras': ('Carvão, poeiras', 'particulado', 'confirmar'),
    'Chumbo': ('Chumbo', 'quimico', 'ok'),
    'Cianeto de Hidrogênio + Sais de Cianeto': ('Cianeto de Hidrogênio + Sais de Cianeto', 'quimico', 'confirmar'),
    'Cianoacrilato de etila': ('Cianoacrilato de etila', 'quimico', 'ok'),
    'Ciclohexano': ('Ciclohexano', 'quimico', 'ok'),
    'Ciclohexanona': ('Ciclohexanona', 'quimico', 'ok'),
    'Cimento Portland': ('Cimento Portland', 'quimico', 'ok'),
    'Cloro e seus compostos': ('CLORO', 'quimico', 'confirmar'),
    'Clorodifluormetano (Freon 22)': ('Clorodifluormetano (Freon 22)', 'quimico', 'ok'),
    'Clorofórmio': ('Clorofórmio', 'quimico', 'ok'),
    'Cobalto': ('Cobalto', 'quimico', 'ok'),
    'Cobre': ('COBRE, FUMOS COMO CU', 'quimico', 'confirmar'),
    'Cromo e seus compostos tóxicos (inclui metal e compostos de CrIII, compostos de Cr VI solúveis em água e compostos de Cr VI insolúveis)': ('COMPOSTOS DE CROMO HEXAVALENTE, COMO CR(VI), COMPOSTOS SOLÚVEIS EM ÁGUA', 'quimico', 'confirmar'),
    'Cádmio': ('Cádmio', 'quimico', 'ok'),
    'Diclorometano (Cloreto de Metileno)': ('Diclorometano (Cloreto de Metileno)', 'quimico', 'ok'),
    'Diária profissional para quantificação dos agentes ambientais': (None, None, 'logistica'),
    'Dióxido de Carbono': ('Dióxido de Carbono', 'quimico', 'ok'),
    'Dióxido de Carbono (gás de solda)': ('Dióxido de Carbono (gás de solda)', 'quimico', 'ok'),
    'Dióxido de Enxofre': ('Dióxido de Enxofre', 'quimico', 'ok'),
    'Dióxido de titânio': ('Dióxido de titânio', 'quimico', 'ok'),
    'Estanho': ('ESTANHO E COMPOSTOS INORGÂNICOS EXCLUINDO HIDRETO DE SN, COMO SN', 'quimico', 'confirmar'),
    'Estearatos': ('Estearatos', 'quimico', 'confirmar'),
    'Estireno': ('Estireno', 'quimico', 'ok'),
    'Etilbenzeno': ('Etilbenzeno', 'quimico', 'ok'),
    'Etileno Glicol': ('Etileno Glicol', 'quimico', 'ok'),
    'Farinha': ('Farinha', 'quimico', 'ok'),
    'Fenol': ('Fenol', 'quimico', 'ok'),
    'Ferro (óxido)': ('FERRO, ÓXIDO (FE2O3)', 'quimico', 'ok'),
    'Fibra de Vidro': ('FIBRA DE VIDRO FILAMENTO CONTÍNUO «', 'quimico', 'confirmar'),
    'Fluoretos': ('Fluoretos', 'quimico', 'ok'),
    'Formaldeído': ('Formaldeído', 'quimico', 'ok'),
    'Gasolina': ('Gasolina', 'quimico', 'ok'),
    'Glifosato': ('Glifosato', 'quimico', 'ok'),
    'Glutaraldeído': ('Glutaraldeído', 'quimico', 'ok'),
    'Grafite': ('Grafite', 'quimico', 'ok'),
    'Grãos, poeira (aveia, trigo, cevada)': ('Grãos, poeira (aveia, trigo, cevada)', 'particulado', 'ok'),
    'Heptano': ('Heptano', 'quimico', 'ok'),
    'Hexametileno Diisocianato': ('Hexametileno Diisocianato', 'quimico', 'ok'),
    'Hidroquinona': ('Hidroquinona', 'quimico', 'ok'),
    'Hidroxitolueno Butilado (FIV)': ('HIDROXITOLUENO BUTILADO (FI)', 'quimico', 'confirmar'),
    'Hidróxido de Cálcio': ('Hidróxido de Cálcio', 'quimico', 'ok'),
    'Hidróxido de Potássio': ('Hidróxido de Potássio', 'quimico', 'ok'),
    'Hidróxido de Sódio': ('Hidróxido de Sódio', 'quimico', 'ok'),
    'Iluminância (Ponto)': ('Iluminamento', 'iluminamento', 'ok'),
    'Isoforona': ('Isoforona', 'quimico', 'ok'),
    'Isopropilbenzeno': ('CUMENO', 'quimico', 'ok'),
    'Madeira': ('Madeira', 'quimico', 'confirmar'),
    'Manganês': ('Manganês', 'quimico', 'ok'),
    'Medicoes': (None, None, 'generico'),
    'Medições Ambientais': (None, None, 'generico'),
    'Mercúrio': ('MERCÚRIO, HG ELEMENTAR E FORMAS INORGÂNICAS', 'quimico', 'confirmar'),
    'Metabissulfito de Sódio': ('Metabissulfito de Sódio', 'quimico', 'ok'),
    'Metil Etil Cetona': ('Metil Etil Cetona', 'quimico', 'ok'),
    'Metil Isobutil Cetona': ('Metil Isobutil Cetona', 'quimico', 'ok'),
    'Molibdênio': ('Molibdênio', 'quimico', 'confirmar'),
    'Monóxido de Carbono': ('Monóxido de Carbono', 'quimico', 'ok'),
    'n-Hexano': ('n-Hexano', 'quimico', 'ok'),
    'Nafta de Petróleo': ('Nafta de Petróleo', 'quimico', 'ok'),
    'Negro de Fumo': ('Negro de Fumo', 'quimico', 'ok'),
    'Névoas de Óleos minerais': ('ÓLEO MINERAL, EXCLUÍDOS OS FLUIDOS DE TRABALHO COM METAIS', 'quimico', 'confirmar'),
    'Níquel': ('NÍQUEL E COMPOSTOS INORGÂNICOS SOLÚVEIS, (NOS)', 'quimico', 'confirmar'),
    'Pentano': ('PENTANO, TODOS OS ISÔMEROS', 'quimico', 'ok'),
    'Peróxido de Hidrogênio': ('Peróxido de Hidrogênio', 'quimico', 'ok'),
    'Peróxido de Metil Etil Cetona': ('Peróxido de Metil Etil Cetona', 'quimico', 'ok'),
    'PNOS Respirável - Partículas não especificadas de outra maneira': ('PARTICULADO RESPIRÁVEL (PNOS)', 'particulado', 'ok'),
    'Poeira Mineral - Sílica Livre Cristalizada': ('POEIRA RESPIRÁVEL + SÍLICA LIVRE CRISTALINA', 'particulado', 'ok'),
    'Prata': ('Prata', 'quimico', 'confirmar'),
    'Querosene': ('QUEROSENE, COMO VAPOR DE HIDROCARBONETOS TOTAIS', 'quimico', 'confirmar'),
    'Ruído': ('Ruído Ocupacional', 'ruido', 'ok'),
    'Sais de Cianeto': ('Sais de Cianeto', 'quimico', 'ok'),
    'Sulfato de Bário (Bário e seus compostos)': ('Sulfato de Bário (Bário e seus compostos)', 'quimico', 'confirmar'),
    'Sulfato de Cálcio': ('Sulfato de Cálcio', 'quimico', 'ok'),
    'Talco': ('Talco', 'quimico', 'ok'),
    'Tetracloroetileno': ('Tetracloroetileno', 'quimico', 'ok'),
    'Tolueno': ('Tolueno', 'quimico', 'ok'),
    'Tricloroetileno': ('Tricloroetileno', 'quimico', 'ok'),
    'Trietanolamina': ('Trietanolamina', 'quimico', 'ok'),
    'Trimetilbenzeno': ('TRIMETIL BENZENO (MISTURA DE ISÔMEROS)', 'quimico', 'ok'),
    'Tungstênio': ('Tungstênio', 'quimico', 'ok'),
    'Vapores de Etanolamina': ('Vapores de Etanolamina', 'quimico', 'ok'),
    'Vapores Ácido Bórico / Borato': ('BORATO, COMPOSTOS INORGÂNICOS', 'quimico', 'ok'),
    'Varredura de Metais': ('Varredura de Metais', 'quimico', 'ok'),
    'Varredura de Solventes (Vapores Orgânicos)': ('VARREDURA DE VAPORES ORGÂNICOS (33 AGENTES)', 'quimico', 'ok'),
    'Vibração Corpo Inteiro - (aren + VDVR)': ('Vibração de Corpo Inteiro (VCI)', 'vibracao_vci', 'ok'),
    'Vibração Extremidades Superiores': ('Vibração de Mão-Braço (VMB)', 'vibracao_vbma', 'ok'),
    'Xileno': ('Xileno', 'quimico', 'ok'),
    'Zircônio': ('Zircônio', 'quimico', 'ok'),
    'Ácido Acrílico': ('Ácido Acrílico', 'quimico', 'ok'),
    'Ácido Acético': ('Ácido Acético', 'quimico', 'ok'),
    'Ácido Clorídrico': ('Ácido Clorídrico', 'quimico', 'ok'),
    'Ácido Fluorídrico': ('FLUORETO DE HIDROGÊNIO', 'quimico', 'ok'),
    'Ácido Fosfórico': ('Ácido Fosfórico', 'quimico', 'ok'),
    'Ácido Metacrílico': ('Ácido Metacrílico', 'quimico', 'ok'),
    'Ácido Nítrico': ('Ácido Nítrico', 'quimico', 'ok'),
    'Ácido Oxálico': ('Ácido Oxálico', 'quimico', 'ok'),
    'Ácido peracético': ('Ácido peracético', 'quimico', 'ok'),
    'Ácido Perclórico': ('Ácido Perclórico', 'quimico', 'ok'),
    'Ácido Sulfúrico': ('Ácido Sulfúrico', 'quimico', 'ok'),
    'Álcool Butílico': ('ÁLCOOL N-BUTÍLICO', 'quimico', 'ok'),
    'Álcool Etílico (Etanol)': ('ETANOL', 'quimico', 'ok'),
    'Álcool Isobutílico': ('Álcool Isobutílico', 'quimico', 'ok'),
    'Álcool Isopropílico (2-Propanol)': ('Álcool Isopropílico (2-Propanol)', 'quimico', 'ok'),
    'Álcool Metílico (Metanol)': ('Álcool Metílico (Metanol)', 'quimico', 'ok'),
    'Éter Etílico': ('Éter Etílico', 'quimico', 'ok'),
    'Éter Etílico de Dietileno Glicol': ('Éter Etílico de Dietileno Glicol', 'quimico', 'sem_metodo'),
    'Óleo Diesel': ('DIESEL COMBUSTÍVEL, COMO HIDROCARBONETOS TOTAIS (FI)', 'quimico', 'confirmar'),
    'Óxido de Cálcio': ('Óxido de Cálcio', 'quimico', 'ok'),
    'Óxido de Magnésio': ('Óxido de Magnésio', 'quimico', 'ok'),
    'Óxido de Zinco': ('Óxido de Zinco', 'quimico', 'ok'),
    'Óxido Nitroso': ('Óxido Nitroso', 'quimico', 'ok'),
}


def _chave(nome):
    s = unicodedata.normalize('NFKD', str(nome or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).strip().lower()


_POR_CHAVE = {_chave(k): (k, v) for k, v in CATALOGO_MEDICAO.items()}


def resolver_produto(nome):
    """Produto do CRM -> dict {produto, agente, tipo, situacao} ou None.
    Acento, caixa e espaço sobrando no nome não atrapalham."""
    achado = _POR_CHAVE.get(_chave(nome))
    if not achado:
        return None
    produto, (agente, tipo, situacao) = achado
    return {'produto': produto, 'agente': agente, 'tipo': tipo, 'situacao': situacao}
